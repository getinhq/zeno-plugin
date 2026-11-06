"""Submit for Review window in the Zeno theme, for any DCC.

Before anything renders it checks the scene against Zeno (frame range vs the shot's cut,
frame rate and start frame vs the last revision, a camera, whether the studio allows image
sequences) and lists the notes ticked as fixed, which are marked fixed in the new revision.

The DCC supplies a host:

- ``scene_info() -> {"frame_start", "frame_end", "fps", "has_camera", "resolution"?, "file_name"?}``
- ``set_frame_range(start, end)``
- ``pending(task_id) -> list[str]`` / ``set_pending(task_id, note_ids)``
- ``default_task_id(task_ids) -> str``; optional ``on_tasks(tasks)``
- optional ``scene_facts() -> dict`` (sent to ``PUT /shots/{id}/scene-facts``; the answer adds
  checks on linked asset versions and casting) and ``apply_fix(fix, on_done)`` for fixes
  that change the file (``update_asset``)
- ``render_and_submit(task_id, *, description, as_sequence, fixes, on_done, on_stage)``:
  render, upload in the background, then call ``on_done(result)`` on the UI thread with
  ``error`` or ``item``, ``url``, ``fixed`` and ``fix_errors``. ``on_stage("uploading")``
  reports that the render finished. It may return before rendering starts.
"""
from __future__ import annotations

import webbrowser
from typing import Any

from zeno_client.client import ZenoClient
from zeno_client.review import frames_label, submit_checks
from zeno_client.scene_facts import facts_checks
from zeno_ui.qt_compat import get_qt_modules
from zeno_ui.qt_workers import make_async_runner
from zeno_ui.stylesheet import apply_stylesheet, theme_tokens

SCENE_POLL_MS = 500
_ICON = {"ok": "✓", "info": "i", "warning": "!", "error": "✕"}


def _qss(t: dict[str, str]) -> str:
    return f"""
QLabel#context {{ color: {t['color.foreground']}; font-size: 9pt; }}
QFrame#checkRow {{ background: {t['color.card']}; border: 1px solid {t['color.border']}; border-radius: 6px; }}
QFrame#checkRow[level="warning"] {{ border-color: {t['color.warning']}; }}
QFrame#checkRow[level="error"] {{ border-color: {t['color.danger']}; }}
QLabel#icon_ok {{ color: {t['color.success']}; font-weight: bold; }}
QLabel#icon_warning {{ color: {t['color.warning']}; font-weight: bold; }}
QLabel#icon_error {{ color: {t['color.danger']}; font-weight: bold; }}
QLabel#icon_info {{ color: {t['color.info']}; font-weight: bold; }}
QPushButton#fixButton {{ padding: 3px 10px; color: {t['color.primary']}; border-color: {t['color.primary']}; background: transparent; }}
QPushButton#fixButton:hover {{ background: {t['color.selection-bg']}; }}
QFrame#formatCard {{ background: {t['color.card']}; border: 1px solid {t['color.border']}; border-radius: 6px; }}
QFrame#formatCard[selected="true"] {{ border-color: {t['color.primary']}; }}
QFrame#formatCard[disabled="true"] {{ background: {t['color.background']}; }}
QRadioButton {{ font-weight: bold; }}
QRadioButton:disabled {{ color: {t['color.muted']}; }}
QLabel#statusOk {{ color: {t['color.success']}; }}
QLabel#statusError {{ color: {t['color.danger']}; }}
QLabel#statusBusy {{ color: {t['color.primary']}; }}
"""


class ZenoSubmitReviewDialog:
    def __init__(
        self,
        *,
        client: ZenoClient,
        host: Any,
        task_id: str = "",
        parent: Any | None = None,
        stay_on_top: bool = False,
    ) -> None:
        QtWidgets, QtCore, _ = get_qt_modules()
        self._client = client
        self._host = host
        self._task_id = task_id
        self._tasks: list[dict[str, Any]] = []
        self._ctx: dict[str, Any] = {}  # per task: shot, last, notes, sequences_off
        self._scene: dict[str, Any] = {}
        self._busy = False
        self._done_url = ""
        self._seq = 0
        self._notice: tuple[str, str] | None = None  # shown once the next load finishes

        self._dlg = QtWidgets.QDialog(parent)
        self._dlg.setWindowTitle("Zeno · Submit for Review")
        self._dlg.setMinimumWidth(500)
        self._dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        if stay_on_top:
            self._dlg.setWindowFlags(self._dlg.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        apply_stylesheet(self._dlg, _qss(theme_tokens()))

        root = QtWidgets.QHBoxLayout(self._dlg)
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(8)
        root.addLayout(col, 1)

        title = QtWidgets.QLabel("Submit for Review")
        title.setObjectName("dialogTitle")
        col.addWidget(title)
        sub = QtWidgets.QLabel("Playblasts the camera view and sends it to your reviewer.")
        sub.setObjectName("muted")
        col.addWidget(sub)

        row = QtWidgets.QHBoxLayout()
        self._task = QtWidgets.QComboBox(self._dlg)
        self._refresh = QtWidgets.QPushButton("Refresh")
        row.addWidget(self._task, 1)
        row.addWidget(self._refresh)
        col.addLayout(row)
        self._context = QtWidgets.QLabel("")
        self._context.setObjectName("context")
        self._context.setWordWrap(True)
        col.addWidget(self._context)

        col.addWidget(self._section("BEFORE YOU RENDER"))
        self._checks_box = QtWidgets.QVBoxLayout()
        self._checks_box.setSpacing(6)
        col.addLayout(self._checks_box)

        col.addWidget(self._section("WHAT CHANGED"))
        self._desc = QtWidgets.QPlainTextEdit(self._dlg)
        self._desc.setPlaceholderText("e.g. Pushed the key light, held the last pose 6 frames longer")
        self._desc.setFixedHeight(64)
        col.addWidget(self._desc)

        col.addWidget(self._section("FORMAT"))
        fmt = QtWidgets.QHBoxLayout()
        self._movie, self._movie_card = self._format_card("Movie (H.264)", "One file, quickest to send.")
        self._frames, self._frames_card = self._format_card("Image sequence (PNG)", "Only frames that changed since the last revision upload.")
        # The radios live in separate cards, so Qt's sibling auto-exclusivity doesn't apply.
        self._format_group = QtWidgets.QButtonGroup(self._dlg)
        self._format_group.addButton(self._movie)
        self._format_group.addButton(self._frames)
        self._movie.setChecked(True)
        fmt.addWidget(self._movie_card, 1)
        fmt.addWidget(self._frames_card, 1)
        col.addLayout(fmt)

        col.addWidget(self._section("FIXES IN THIS REVISION"))
        self._fixes_box = QtWidgets.QVBoxLayout()
        self._fixes_box.setSpacing(4)
        col.addLayout(self._fixes_box)

        self._open_after = QtWidgets.QCheckBox("Open the review in my browser when it's uploaded")
        col.addWidget(self._open_after)

        self._status = QtWidgets.QLabel("")
        self._status.setObjectName("muted")
        self._status.setWordWrap(True)
        col.addWidget(self._status)

        foot = QtWidgets.QHBoxLayout()
        self._close = QtWidgets.QPushButton("Cancel")
        self._go = QtWidgets.QPushButton("Render && Submit")
        self._go.setProperty("primary", True)
        self._go.setMinimumHeight(34)
        foot.addWidget(self._close)
        foot.addWidget(self._go, 1)
        col.addLayout(foot)

        self._runner = make_async_runner()
        self._runner.finished.connect(self._on_loaded)
        self._runner.failed.connect(self._on_failed)
        self._task.currentIndexChanged.connect(self._on_task_changed)
        self._refresh.clicked.connect(lambda: self._load(with_tasks=True))
        self._movie.toggled.connect(lambda _on: self._render_checks())
        self._close.clicked.connect(self._dlg.close)
        self._go.clicked.connect(self._on_go)

        self._scene_timer = QtCore.QTimer(self._dlg)
        self._scene_timer.setInterval(SCENE_POLL_MS)
        self._scene_timer.timeout.connect(self._poll_scene)
        self._scene_timer.start()

        self._poll_scene()
        self._load(with_tasks=True)

    # --- building blocks ----------------------------------------------------------------

    def _section(self, text: str) -> Any:
        QtWidgets, _, _ = get_qt_modules()
        label = QtWidgets.QLabel(text)
        label.setObjectName("section")
        return label

    def _format_card(self, title: str, blurb: str) -> tuple[Any, Any]:
        QtWidgets, QtCore, _ = get_qt_modules()
        card = QtWidgets.QFrame()
        card.setObjectName("formatCard")
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(10, 8, 10, 8)
        radio = QtWidgets.QRadioButton(title)
        note = QtWidgets.QLabel(blurb)
        note.setObjectName("muted")
        note.setWordWrap(True)
        # Word-wrapped labels side by side under-report their height; reserve three lines.
        note.setMinimumHeight(note.fontMetrics().lineSpacing() * 3)
        note.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        lay.addWidget(radio)
        lay.addWidget(note)
        card._blurb = note  # type: ignore[attr-defined]
        radio.toggled.connect(lambda on, c=card: self._restyle(c, selected=on))
        return radio, card

    @staticmethod
    def _restyle(widget: Any, **props: Any) -> None:
        for key, value in props.items():
            widget.setProperty(key, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    @staticmethod
    def _clear(layout: Any) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    # --- data ---------------------------------------------------------------------------

    def _load(self, *, with_tasks: bool) -> None:
        self._seq += 1
        seq, client, task_id = self._seq, self._client, self._task_id
        facts = None
        if hasattr(self._host, "scene_facts"):
            try:
                facts = self._host.scene_facts()  # scene access stays on the UI thread
            except Exception:  # noqa: BLE001 - facts are advice; the window works without them
                facts = None
        self._set_status("Checking with Zeno…")

        def call() -> dict[str, Any]:
            out: dict[str, Any] = {"seq": seq, "task_id": task_id}
            if with_tasks:
                out["tasks"] = client.list_my_tasks()
                if task_id not in [str(t.get("id")) for t in out["tasks"]]:
                    return out
            if task_id:
                out["ctx"] = _task_context(client, task_id, out.get("tasks"), facts)
            return out

        self._runner.submit(call)

    def _on_loaded(self, out: dict[str, Any]) -> None:
        if out.get("seq") != self._seq:
            return
        self._set_status(*(self._notice or ("",)))
        self._notice = None
        if "tasks" in out:
            self._tasks = list(out["tasks"])
            if hasattr(self._host, "on_tasks"):
                self._host.on_tasks(self._tasks)
            ids = [str(t.get("id")) for t in self._tasks]
            self._task.blockSignals(True)
            self._task.clear()
            for t in self._tasks:
                label = str(t.get("title") or t.get("type") or t.get("id"))
                status = str(t.get("status") or "").replace("_", " ")
                self._task.addItem(f"{label}  ·  {status}" if status else label, str(t.get("id")))
            if out["task_id"] not in ids:
                out["task_id"] = self._host.default_task_id(ids) or (ids[0] if ids else "")
                self._task_id = out["task_id"]
                idx = self._task.findData(self._task_id)
                if idx >= 0:
                    self._task.setCurrentIndex(idx)
                self._task.blockSignals(False)
                if self._task_id:
                    self._load(with_tasks=False)
                else:
                    self._set_status("No tasks are assigned to you.")
                    self._render_all()
                return
            idx = self._task.findData(out["task_id"])
            if idx >= 0:
                self._task.setCurrentIndex(idx)
            self._task.blockSignals(False)
        self._task_id = out["task_id"]
        self._ctx = out.get("ctx") or {}
        self._render_all()

    def _on_failed(self, exc: Any) -> None:
        self._set_status(f"Could not reach Zeno: {exc}", "statusError")

    def _on_task_changed(self, _index: int) -> None:
        self._task_id = str(self._task.currentData() or "")
        self._ctx = {}
        self._render_all()
        self._load(with_tasks=False)

    def _poll_scene(self) -> None:
        try:
            info = dict(self._host.scene_info())
        except Exception:
            return
        if info != self._scene:
            self._scene = info
            self._render_checks()

    # --- rendering ----------------------------------------------------------------------

    def _render_all(self) -> None:
        last = self._ctx.get("last")
        shot = self._ctx.get("shot")
        bits = []
        if shot and shot.get("shot_code"):
            bits.append(str(shot["shot_code"]))
        if last:
            n = int(last.get("revision") or 0) + 1
            bits.append(f"next is v{n:03d}")
            state = str(last.get("review_status") or "").replace("_", " ")
            bits.append(f"{last.get('label')}: {state}" if state else str(last.get("label")))
        elif self._task_id and self._ctx:
            bits.append("first revision (v001)")
        self._context.setText("  ·  ".join(bits))

        off = self._ctx.get("sequences_off")
        self._frames.setEnabled(not off)
        self._restyle(self._frames_card, disabled=bool(off))
        self._frames_card._blurb.setText(  # type: ignore[attr-defined]
            f"Switched off for this studio ({off})." if off else "Only frames that changed since the last revision upload."
        )
        if off and self._frames.isChecked():
            self._movie.setChecked(True)
        self._render_fixes()
        self._render_checks()

    def _render_fixes(self) -> None:
        QtWidgets, _, _ = get_qt_modules()
        self._clear(self._fixes_box)
        pending = list(self._host.pending(self._task_id)) if self._task_id else []
        by_id = {n["id"]: n for n in self._ctx.get("notes") or []}
        if not pending:
            empty = QtWidgets.QLabel("None ticked. Tick fixes in Zeno › Review Notes and they go out with this revision.")
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self._fixes_box.addWidget(empty)
            empty.show()
        for note_id in pending:
            n = by_id.get(note_id)
            text = f"{frames_label(n)}  ·  {' '.join(str(n.get('body') or '(drawing)').split())}" if n else "A note that is no longer open"
            box = QtWidgets.QCheckBox(text if len(text) < 90 else text[:88] + "…")
            box.setChecked(True)
            box.setToolTip("Untick to leave this note open")
            box.toggled.connect(lambda on, nid=note_id: self._toggle_fix(nid, on))
            self._fixes_box.addWidget(box)
            box.show()

    def _toggle_fix(self, note_id: str, on: bool) -> None:
        ids = [n for n in self._host.pending(self._task_id) if n != note_id]
        if on:
            ids.append(note_id)
        self._host.set_pending(self._task_id, ids)

    def _checks(self) -> list[dict[str, Any]]:
        if not self._scene:
            return []
        return submit_checks(
            self._scene,
            shot=self._ctx.get("shot"),
            last=self._ctx.get("last"),
            sequences_off=self._ctx.get("sequences_off"),
            as_sequence=self._frames.isChecked(),
        ) + facts_checks(self._ctx.get("facts_report"))

    def _render_checks(self) -> None:
        QtWidgets, _, _ = get_qt_modules()
        self._clear(self._checks_box)
        checks = self._checks()
        for c in checks:
            row = QtWidgets.QFrame()
            row.setObjectName("checkRow")
            row.setProperty("level", c["level"])
            lay = QtWidgets.QHBoxLayout(row)
            lay.setContentsMargins(10, 6, 8, 6)
            icon = QtWidgets.QLabel(_ICON[c["level"]])
            icon.setObjectName(f"icon_{c['level']}")
            icon.setFixedWidth(14)
            text = QtWidgets.QLabel(c["text"])
            text.setWordWrap(True)
            text.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
            lay.addWidget(icon)
            lay.addWidget(text, 1)
            fix = c.get("fix")
            handler = None
            if fix and fix.get("action") == "use_shot_range" and hasattr(self._host, "set_frame_range"):
                handler = lambda _=False, f=fix: self._host.set_frame_range(f["start"], f["end"])  # noqa: E731
            elif fix and fix.get("action") == "update_asset" and hasattr(self._host, "apply_fix"):
                handler = lambda _=False, f=fix: self._apply_fix(f)  # noqa: E731
            if handler is not None:
                btn = QtWidgets.QPushButton(fix["label"])
                btn.setObjectName("fixButton")
                btn.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
                btn.clicked.connect(handler)
                lay.addWidget(btn)
            self._checks_box.addWidget(row)
            row.show()
        res = self._scene.get("resolution")
        if res:
            hint = QtWidgets.QLabel(f"{res} · {self._scene.get('fps')} fps · camera view")
            hint.setObjectName("muted")
            self._checks_box.addWidget(hint)
            hint.show()
        blocked = any(c["level"] == "error" for c in checks)
        if not self._busy and not self._done_url:
            self._go.setEnabled(bool(self._task_id) and not blocked and bool(self._scene))
            pending = len(self._host.pending(self._task_id)) if self._task_id else 0
            frames = int(self._scene.get("frame_end", 0)) - int(self._scene.get("frame_start", 0)) + 1 if self._scene else 0
            # "&&": a single "&" marks a keyboard mnemonic in Qt button text.
            label = f"Render {frames} frames && Submit" if frames > 0 else "Render && Submit"
            self._go.setText(label + (f" · {pending} fix{'es' if pending != 1 else ''}" if pending else ""))
        self._dlg.layout().activate()  # wrapped warnings get their full height straight away

    def _apply_fix(self, fix: dict[str, Any]) -> None:
        self._set_status(f"{fix['label']} for {fix.get('asset_code')}…", "statusBusy")

        def done(ok: bool, message: str) -> None:
            self._set_status(message, "statusOk" if ok else "statusError")
            if ok:
                self._notice = (message, "statusOk")
                self._load(with_tasks=False)  # report the updated file and re-check

        self._host.apply_fix(fix, done)

    def _set_status(self, text: str, kind: str = "muted") -> None:
        self._status.setText(text)
        self._status.setObjectName(kind)
        self._restyle(self._status)

    # --- submit -------------------------------------------------------------------------

    def _on_go(self) -> None:
        QtWidgets, QtCore, _ = get_qt_modules()
        if self._done_url:
            webbrowser.open(self._done_url)
            return
        if not self._task_id:
            return
        self._busy = True
        for w in (self._task, self._refresh, self._desc, self._movie, self._frames, self._go, self._close):
            w.setEnabled(False)
        self._set_status("Rendering the playblast. The DCC is busy until it finishes…", "statusBusy")
        self._dlg.repaint()
        QtWidgets.QApplication.processEvents()
        fixes = list(self._host.pending(self._task_id))
        # Give the window a moment to paint before the DCC blocks on the render.
        QtCore.QTimer.singleShot(60, lambda: self._start(fixes))

    def _start(self, fixes: list[str]) -> None:
        try:
            self._host.render_and_submit(
                self._task_id,
                description=self._desc.toPlainText(),
                as_sequence=self._frames.isChecked(),
                fixes=fixes,
                on_done=self._on_done,
                on_stage=self._on_stage,
            )
        except Exception as exc:  # noqa: BLE001 - render failed before upload
            self._on_done({"error": f"Playblast failed: {exc}"})

    def _on_stage(self, stage: str) -> None:
        if stage == "uploading" and self._busy:
            self._set_status("Uploading to Zeno… you can keep working.", "statusBusy")

    def _on_done(self, result: dict[str, Any]) -> None:
        self._busy = False
        self._close.setEnabled(True)
        self._close.setText("Close")
        if "error" in result:
            self._set_status(f"Not submitted: {result['error']}", "statusError")
            for w in (self._task, self._refresh, self._desc, self._movie, self._go):
                w.setEnabled(True)
            self._frames.setEnabled(not self._ctx.get("sequences_off"))
            self._render_checks()
            return
        item = result.get("item") or {}
        upload = item.get("upload") or {}
        lines = [f"{item.get('label') or 'The revision'} is with the reviewer."]
        if upload.get("frames"):
            lines.append(
                f"Uploaded {upload['uploaded_frames']} of {upload['frames']} frames; {upload['skipped_frames']} were already stored."
            )
        if result.get("fixed"):
            lines.append(f"{len(result['fixed'])} note(s) marked fixed in {item.get('label') or 'it'}.")
        if result.get("fix_errors"):
            lines.append(f"{len(result['fix_errors'])} note(s) could not be updated and stay ticked.")
        self._set_status(" ".join(lines), "statusOk")
        self._done_url = str(result.get("url") or "")
        self._go.setText("Open in browser")
        self._go.setEnabled(bool(self._done_url))
        for i in range(self._fixes_box.count()):  # keep the list as a record of what went out
            w = self._fixes_box.itemAt(i).widget()
            if w is not None:
                w.setEnabled(False)
        if self._done_url and self._open_after.isChecked():
            webbrowser.open(self._done_url)

    # --- host-facing --------------------------------------------------------------------

    def show_non_modal(self) -> None:
        self._dlg.show()
        try:
            self._dlg.raise_()
            self._dlg.activateWindow()
        except Exception:
            pass

    def widget(self) -> Any:
        return self._dlg


def _task_context(
    client: ZenoClient, task_id: str, tasks: list[dict[str, Any]] | None, facts: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Shot, last revision, open notes, the sequence switch and (for shot tasks with scene
    facts) the facts report, for one task (worker thread)."""
    ctx: dict[str, Any] = {"shot": None, "last": None, "notes": [], "sequences_off": None, "facts_report": None}
    task = next((t for t in tasks or [] if str(t.get("id")) == task_id), None)
    if task is None:
        task = next((t for t in client.list_my_tasks() if str(t.get("id")) == task_id), None)
    if task and task.get("shot_id"):
        try:
            ctx["shot"] = client.get_shot(str(task["shot_id"]))
        except Exception:  # noqa: BLE001 - range check is advice, not a blocker
            ctx["shot"] = {"shot_code": "the shot"}
        if facts is not None:
            try:
                ctx["facts_report"] = client.put_scene_facts(str(task["shot_id"]), facts).get("report")
            except Exception:  # noqa: BLE001 - older server without scene facts
                pass
    try:
        ctx["last"] = client.latest_review_item(task_id)
    except Exception:  # noqa: BLE001
        pass
    try:
        ctx["notes"] = client.list_review_notes(task_id, status="open")
    except Exception:  # noqa: BLE001
        pass
    try:
        decision = client.resolve_storage("frame.png")
        if decision.get("strategy") == "off":
            ctx["sequences_off"] = str(decision.get("reason") or "studio storage workflow")
    except Exception:  # noqa: BLE001 - older server: no storage workflow
        pass
    return ctx


__all__ = ["ZenoSubmitReviewDialog"]
