"""Optional Qt UI inside Blender: themed dialogs match zeno-dashboard.

Requires PySide6 (or PySide2) in Blender's bundled Python. Uses
``qt_host.ensure_qt_runtime`` to create the ``QApplication`` once and tick
the Qt event loop from ``bpy.app.timers``. Callers pass the Blender
``Operator`` so PySide import or runtime errors are surfaced to the user
instead of silently falling back to the native operator.
"""
from __future__ import annotations

import threading
import traceback
from typing import Any

import bpy

from .bridge import addon_prefs, make_client
from . import launch_context as launch_context_mod
from . import pyside_provision, qt_host


# Upper bound on how long a single queued ``bpy.ops`` call is allowed to
# block the Qt worker before we bail out with a timeout error. Five
# minutes covers chunked uploads of very large .blend files on a slow
# link; longer than that almost always indicates a stuck main thread.
_QUEUED_OP_TIMEOUT_S = 300.0


def _report(operator: Any, level: str, message: str) -> None:
    """Surface a message through the operator report AND the system console.

    Blender's operator.report lives in the Info area (bottom bar); artists
    often miss it. Printing the same text to stdout makes it show up in
    ``Window > Toggle System Console`` / launched-terminal output.
    """
    print(f"[chimera] {level}: {message}")
    if operator is None:
        return
    try:
        operator.report({level}, message)
    except Exception:
        pass


def _load_dialog_classes(operator: Any) -> tuple[Any, Any, Any, Any, Any, Any] | None:
    # Opportunistically patch sys.path *before* the import check — if PySide6
    # was installed via ``--user`` earlier and Blender's user-site is off,
    # this is what makes it importable.
    added = pyside_provision.ensure_pyside_on_path()
    if added:
        _report(operator, "INFO", "Added to sys.path: " + "; ".join(added))

    if not pyside_provision.pyside_available():
        prefs = addon_prefs()
        diag = pyside_provision.diagnostics()
        search_dirs = "; ".join(diag.get("search_dirs") or [])
        if prefs is None or getattr(prefs, "auto_install_pyside", True):
            state = pyside_provision.ensure_pyside_async()
            _report(
                operator,
                "WARNING",
                (
                    f"PySide6 missing — kicking off install via {state.get('python')}. "
                    f"State: {state.get('message')}. "
                    f"Searched: {search_dirs}. "
                    "Falling back to native dialog; re-run after the install finishes."
                ),
            )
        else:
            _report(
                operator,
                "WARNING",
                (
                    "PySide6 not installed and auto-install is disabled. "
                    "Install manually: "
                    f"{diag.get('python_binary_guess')} -m pip install --user PySide6  "
                    "— then restart Blender or run 'Chimera: Check Qt Status'."
                ),
            )
        return None

    try:
        from zeno_ui.action_dialogs import (
            ZenoNavigatorActionDialog,
            ZenoPublisherDialog,
            ZenoReportIssueDialog,
            ZenoVersionSwitcherDialog,
        )
        from zeno_ui.qt_dialogs import ZenoNavigatorDialog, ZenoPaletteDialog
    except Exception as exc:
        _report(
            operator,
            "WARNING",
            (
                f"Chimera dashboard UI unavailable even though PySide imported: {exc}. "
                "Falling back to native dialog. Check system console for the stack trace."
            ),
        )
        import traceback

        traceback.print_exc()
        return None
    return (
        ZenoNavigatorDialog,
        ZenoPaletteDialog,
        ZenoNavigatorActionDialog,
        ZenoVersionSwitcherDialog,
        ZenoReportIssueDialog,
        ZenoPublisherDialog,
    )


def _run_operator_on_main_thread(
    *,
    label: str,
    call: "Any",
) -> dict[str, Any]:
    """Run a ``bpy.ops`` invocation on Blender's main thread synchronously.

    Called from Qt worker threads (``QThreadPool``). We enqueue a closure
    on :mod:`qt_host`'s main-thread queue, block on a ``threading.Event``
    until the closure has completed, then return a structured result
    dict that the palette's ``_on_publish_done`` / ``_on_publish_failed``
    slots can render accurately.

    Returning a real success/failure (instead of the previous fire-and-
    forget) is what makes "Publish" in the palette actually report the
    outcome to the artist instead of claiming success the instant the
    worker thread had enqueued the op.
    """
    done = threading.Event()
    result: dict[str, Any] = {"ok": False, "message": "", "returned": None}

    def _do() -> None:
        print(f"[chimera.{label}] main-thread dispatch: invoking operator", flush=True)
        try:
            ret = call()
        except BaseException as exc:  # noqa: BLE001
            traceback.print_exc()
            result["ok"] = False
            result["message"] = f"{type(exc).__name__}: {exc}"
            result["returned"] = None
            done.set()
            return
        returned = set(ret) if hasattr(ret, "__iter__") else {ret}
        print(f"[chimera.{label}] operator result={returned!r}", flush=True)
        if "FINISHED" in returned:
            result["ok"] = True
            result["message"] = "Operator completed."
        elif "CANCELLED" in returned:
            result["ok"] = False
            result["message"] = (
                "Operator cancelled. See Blender system console for details."
            )
        else:
            result["ok"] = False
            result["message"] = f"Unexpected operator result: {returned!r}"
        result["returned"] = returned
        done.set()

    qt_host.queue_main_thread(_do)

    if not done.wait(timeout=_QUEUED_OP_TIMEOUT_S):
        return {
            "ok": False,
            "message": (
                f"Timed out after {_QUEUED_OP_TIMEOUT_S:.0f}s waiting for "
                "Blender's main thread to process the operator. Check that "
                "Blender is not busy (long render, modal dialog, etc.)."
            ),
            "returned": None,
        }

    return result


def _queued_load(
    *, project: str, asset: str, version: str, representation: str
) -> None:
    """Fire-and-forget load dispatch.

    Unlike publish (which runs inside a ``QThreadPool`` worker and so can
    safely block waiting for the main thread), load is invoked directly
    from the palette's GUI thread — Blender's *same* main thread that
    drains :mod:`qt_host`'s queue. Blocking here would deadlock the
    operator against its own drain timer, so we enqueue and return
    immediately and let the load complete asynchronously.
    """
    print(
        f"[chimera.load] _queued_load received project={project!r} asset={asset!r} "
        f"version={version!r} rep={representation!r}",
        flush=True,
    )

    def _do() -> None:
        try:
            ret = bpy.ops.chimera.load_asset(
                "EXEC_DEFAULT",
                project=project,
                asset=asset,
                version=version,
                representation=representation,
            )
            print(f"[chimera.load] operator result={ret!r}", flush=True)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            print(f"[chimera.load] operator raised: {exc!r}", flush=True)

    qt_host.queue_main_thread(_do)


def _queued_link(*, project: str, asset: str, version: str, representation: str) -> None:
    """Link an asset version into the open file, outside Qt's event processing."""

    def _do() -> None:
        try:
            ret = bpy.ops.chimera.link_asset(
                "EXEC_DEFAULT", project=project, asset=asset, version=version, representation=representation
            )
            print(f"[chimera.link] operator result={ret!r}", flush=True)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    qt_host.queue_main_thread(_do)


def _queued_publish(
    *,
    project: str,
    asset: str,
    representation: str,
    pipeline_stage: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    stage = (pipeline_stage or "").strip().lower()
    tid = (task_id or "").strip()
    print(
        f"[chimera.publish] _queued_publish received project={project!r} "
        f"asset={asset!r} rep={representation!r} stage={stage!r} task_id={tid!r} "
        "— dispatching to main thread",
        flush=True,
    )
    op_result = _run_operator_on_main_thread(
        label="publish",
        call=lambda: bpy.ops.chimera.publish_current_file(
            "EXEC_DEFAULT",
            project=project,
            asset=asset,
            version="next",
            representation=representation,
            pipeline_stage=stage,
            task_id=tid,
        ),
    )

    # The operator records structured outcome data (version number, chunk
    # counts, error message, ...) in a module-level dict so we can surface
    # the real registration result to the palette instead of just a generic
    # "FINISHED" / "CANCELLED". Reading happens *after* the main-thread call
    # returned, so there's no race with the operator writing it.
    try:
        from . import operators_publish

        extra = dict(operators_publish._LAST_PUBLISH_RESULT)
    except Exception:  # noqa: BLE001
        extra = {}

    if extra:
        # Operator-provided status wins over the coarse FINISHED/CANCELLED
        # mapping (e.g. "FINISHED" can still come with ok=False if the
        # operator trapped an error and returned gracefully; likewise the
        # operator's message is more useful than "Operator completed.").
        for key in ("ok", "message", "version", "pipeline_stage", "uploaded_chunks", "manifest_id"):
            if key in extra:
                op_result[key] = extra[key]
    print(f"[chimera.publish] final palette response: {op_result!r}", flush=True)
    return op_result


def _queued_report_issue(*, client: Any, payload: dict[str, Any]) -> dict[str, Any]:
    issue = client.create_issue(payload)
    issue_id = str(issue.get("id") or "").strip()
    attachment_path = str(payload.get("attachment_path") or "").strip()
    if issue_id and attachment_path:
        client.upload_issue_attachment(issue_id=issue_id, file_path=attachment_path)
    return issue


def show_command_palette_qt(operator: Any | None = None) -> bool:
    """Show ``ZenoPaletteDialog`` non-modally. Returns False if Qt is unavailable."""
    classes = _load_dialog_classes(operator)
    if classes is None:
        return False
    _, ZenoPaletteDialog, *_rest = classes

    app, err = qt_host.ensure_qt_runtime()
    if app is None:
        _report(operator, "WARNING", err or "Qt runtime unavailable.")
        return False

    try:
        prefs = addon_prefs()
        prefs_default = (prefs.default_project if prefs else "") or ""
        client = make_client()
        hint = launch_context_mod.get_session_launch_hint()

        dlg = ZenoPaletteDialog(
            client=client,
            parent=None,
            prefs_default_project=prefs_default,
            launch_hint=hint,
            default_representation="blend",
            on_load=_queued_load,
            on_publish=_queued_publish,
            stay_on_top=True,
        )
        qt_host.retain_dialog(dlg)
        dlg.show_non_modal()
    except Exception as exc:
        _report(operator, "WARNING", f"Dashboard UI failed to open: {exc}")
        return False
    return True


def show_navigator_qt(operator: Any | None = None) -> bool:
    """Show ``ZenoNavigatorDialog`` non-modally. Returns False if Qt is unavailable."""
    classes = _load_dialog_classes(operator)
    if classes is None:
        return False
    ZenoNavigatorDialog, _palette, *_rest = classes

    app, err = qt_host.ensure_qt_runtime()
    if app is None:
        _report(operator, "WARNING", err or "Qt runtime unavailable.")
        return False

    try:
        dlg = ZenoNavigatorDialog(
            client=make_client(),
            parent=None,
            default_version="latest",
            default_representation="blend",
            on_open=_queued_load,
            stay_on_top=True,
        )
        qt_host.retain_dialog(dlg)
        dlg.show_non_modal()
    except Exception as exc:
        _report(operator, "WARNING", f"Dashboard UI failed to open: {exc}")
        return False
    return True


def show_navigator_action_qt(operator: Any | None = None) -> bool:
    classes = _load_dialog_classes(operator)
    if classes is None:
        return False
    _, _, ZenoNavigatorActionDialog, *_rest = classes

    app, err = qt_host.ensure_qt_runtime()
    if app is None:
        _report(operator, "WARNING", err or "Qt runtime unavailable.")
        return False
    try:
        prefs = addon_prefs()
        prefs_default = (prefs.default_project if prefs else "") or ""
        hint = launch_context_mod.get_session_launch_hint()
        dlg = ZenoNavigatorActionDialog(
            client=make_client(),
            parent=None,
            launch_hint=hint,
            prefs_default_project=prefs_default,
            on_load_entity=_queued_load,
            on_link_entity=_queued_link,
            stay_on_top=True,
        )
        qt_host.retain_dialog(dlg)
        dlg.show_non_modal()
    except Exception as exc:
        _report(operator, "WARNING", f"Navigator UI failed to open: {exc}")
        return False
    return True


def show_version_switcher_qt(operator: Any | None = None) -> bool:
    classes = _load_dialog_classes(operator)
    if classes is None:
        return False
    _, _, _nav, ZenoVersionSwitcherDialog, *_rest = classes
    app, err = qt_host.ensure_qt_runtime()
    if app is None:
        _report(operator, "WARNING", err or "Qt runtime unavailable.")
        return False
    try:
        prefs = addon_prefs()
        prefs_default = (prefs.default_project if prefs else "") or ""
        hint = launch_context_mod.get_session_launch_hint()
        dlg = ZenoVersionSwitcherDialog(
            client=make_client(),
            parent=None,
            launch_hint=hint,
            prefs_default_project=prefs_default,
            on_switch_version=_queued_load,
            stay_on_top=True,
        )
        qt_host.retain_dialog(dlg)
        dlg.show_non_modal()
    except Exception as exc:
        _report(operator, "WARNING", f"Version Switcher UI failed to open: {exc}")
        return False
    return True


def show_report_issue_qt(operator: Any | None = None) -> bool:
    classes = _load_dialog_classes(operator)
    if classes is None:
        return False
    _, _, _nav, _switch, ZenoReportIssueDialog, *_rest = classes
    app, err = qt_host.ensure_qt_runtime()
    if app is None:
        _report(operator, "WARNING", err or "Qt runtime unavailable.")
        return False
    try:
        prefs = addon_prefs()
        prefs_default = (prefs.default_project if prefs else "") or ""
        hint = launch_context_mod.get_session_launch_hint()
        client = make_client()
        dlg = ZenoReportIssueDialog(
            client=client,
            parent=None,
            launch_hint=hint,
            prefs_default_project=prefs_default,
            on_raise_ticket=lambda payload: _queued_report_issue(client=client, payload=payload),
            stay_on_top=True,
        )
        qt_host.retain_dialog(dlg)
        dlg.show_non_modal()
    except Exception as exc:
        _report(operator, "WARNING", f"Report Issue UI failed to open: {exc}")
        return False
    return True


def show_publisher_qt(operator: Any | None = None) -> bool:
    classes = _load_dialog_classes(operator)
    if classes is None:
        return False
    _, _, _nav, _switch, _report_cls, ZenoPublisherDialog = classes
    app, err = qt_host.ensure_qt_runtime()
    if app is None:
        _report(operator, "WARNING", err or "Qt runtime unavailable.")
        return False
    try:
        prefs = addon_prefs()
        prefs_default = (prefs.default_project if prefs else "") or ""
        hint = launch_context_mod.get_session_launch_hint()
        dlg = ZenoPublisherDialog(
            client=make_client(),
            parent=None,
            launch_hint=hint,
            prefs_default_project=prefs_default,
            on_publish=_queued_publish,
            stay_on_top=True,
        )
        qt_host.retain_dialog(dlg)
        dlg.show_non_modal()
    except Exception as exc:
        _report(operator, "WARNING", f"Publisher UI failed to open: {exc}")
        return False
    return True


_review_notes: Any = None


def _review_notes_alive() -> bool:
    if _review_notes is None:
        return False
    try:
        return bool(_review_notes.widget().isVisible())
    except RuntimeError:  # the C++ dialog was deleted on close
        return False


def show_review_notes_qt(operator: Any | None = None) -> bool:
    """Show the Review Notes window (one at a time). Returns False if Qt is unavailable."""
    global _review_notes
    if _review_notes_alive():
        _review_notes.show_non_modal()
        _review_notes.reload()
        return True
    if _load_dialog_classes(operator) is None:
        return False
    app, err = qt_host.ensure_qt_runtime()
    if app is None:
        _report(operator, "WARNING", err or "Qt runtime unavailable.")
        return False
    try:
        from zeno_ui.review_notes_dialog import ZenoReviewNotesDialog

        from .review_host import BlenderReviewHost

        dlg = ZenoReviewNotesDialog(client=make_client(), host=BlenderReviewHost(), parent=None, stay_on_top=True)
        qt_host.retain_dialog(dlg)
        dlg.show_non_modal()
        _review_notes = dlg
    except Exception as exc:
        traceback.print_exc()
        _report(operator, "WARNING", f"Review Notes UI failed to open: {exc}")
        return False
    return True


_submit_review: Any = None


def show_submit_review_qt(operator: Any | None = None, task_id: str = "") -> bool:
    """Show the Submit for Review window (one at a time). Returns False if Qt is unavailable."""
    global _submit_review
    if _submit_review is not None:
        try:
            if _submit_review.widget().isVisible():
                _submit_review.show_non_modal()
                return True
        except RuntimeError:
            pass
    if _load_dialog_classes(operator) is None:
        return False
    app, err = qt_host.ensure_qt_runtime()
    if app is None:
        _report(operator, "WARNING", err or "Qt runtime unavailable.")
        return False
    try:
        from zeno_ui.submit_review_dialog import ZenoSubmitReviewDialog

        from .review_host import BlenderReviewHost

        dlg = ZenoSubmitReviewDialog(
            client=make_client(), host=BlenderReviewHost(), task_id=task_id, parent=None, stay_on_top=True
        )
        qt_host.retain_dialog(dlg)
        dlg.show_non_modal()
        _submit_review = dlg
    except Exception as exc:
        traceback.print_exc()
        _report(operator, "WARNING", f"Submit for Review UI failed to open: {exc}")
        return False
    return True


def reload_review_notes_qt() -> None:
    """Refetch the open Review Notes window's notes (after a submission, for example)."""
    if _review_notes_alive():
        _review_notes.reload()


# ---------------------------------------------------------------------------
# Warm-up: pay Qt's one-time costs while Blender is idle, not on first click.
# ---------------------------------------------------------------------------

# Importing PySide, creating the QApplication, loading the fonts and parsing the
# stylesheet cost a second or more the first time, which made the first Zeno
# window feel slow to open. Each step runs on its own timer tick so Blender's UI
# never stalls for the whole lot at once.
_WARM_DELAY_S = 3.0
_warm_steps: list[Any] = []


def _warm_wanted() -> bool:
    if bpy.app.background:
        return False
    prefs = addon_prefs()
    if prefs is None:
        return True
    return bool(
        getattr(prefs, "preload_qt_ui", True)
        and getattr(prefs, "use_dashboard_qt_ui", True)
        and not getattr(prefs, "use_hub_mode", False)
    )


def _warm_import_qt() -> bool:
    pyside_provision.ensure_pyside_on_path()
    if not pyside_provision.pyside_available():
        return False  # the first click offers the install, as before
    from zeno_ui.qt_compat import get_qt_modules

    get_qt_modules()
    return True


def _warm_app() -> bool:
    _, err = qt_host.ensure_qt_runtime()
    return err is None


def _warm_dialogs() -> bool:
    import zeno_client  # noqa: F401  (pulls in httpx, used by every dialog)
    import zeno_ui.action_dialogs  # noqa: F401
    import zeno_ui.qt_dialogs  # noqa: F401
    import zeno_ui.review_notes_dialog  # noqa: F401
    import zeno_ui.submit_review_dialog  # noqa: F401
    from zeno_ui.stylesheet import warm_up

    warm_up()
    return True


def _warm_tick() -> float | None:
    if not _warm_steps:
        return None
    step = _warm_steps.pop(0)
    try:
        keep_going = step()
    except Exception as exc:  # never let a warm-up problem reach the artist
        print(f"[chimera] Qt warm-up skipped: {exc}")
        keep_going = False
    if not keep_going:
        _warm_steps.clear()
        return None
    return 0.2 if _warm_steps else None


def cancel_warm_up() -> None:
    _warm_steps.clear()
    try:
        if bpy.app.timers.is_registered(_warm_tick):
            bpy.app.timers.unregister(_warm_tick)
    except Exception:  # pragma: no cover - Blender runtime
        pass


def schedule_warm_up() -> None:
    """Prepare the Qt windows a few seconds after Blender starts (see above)."""
    if _warm_steps or not _warm_wanted():
        return
    _warm_steps.extend([_warm_import_qt, _warm_app, _warm_dialogs])
    try:
        bpy.app.timers.register(_warm_tick, first_interval=_WARM_DELAY_S, persistent=True)
    except Exception as exc:  # pragma: no cover - Blender runtime
        _warm_steps.clear()
        print(f"[chimera] could not schedule Qt warm-up: {exc}")


__all__ = [
    "cancel_warm_up",
    "schedule_warm_up",
    "show_review_notes_qt",
    "reload_review_notes_qt",
    "show_submit_review_qt",
    "show_command_palette_qt",
    "show_navigator_qt",
    "show_navigator_action_qt",
    "show_version_switcher_qt",
    "show_report_issue_qt",
    "show_publisher_qt",
]
