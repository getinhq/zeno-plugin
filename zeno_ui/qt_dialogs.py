from __future__ import annotations

from typing import Any, Callable

import os
import getpass

from zeno_client.client import ZenoClient

from zeno_ui.data_source import (
    ZenoDataCache,
    global_cache,
    refresh_palette_state_cached,
)
from zeno_ui.dcc_detect import detect_dcc
from zeno_ui.qt_compat import get_qt_modules
from zeno_ui.qt_workers import make_async_runner, make_debounced
from zeno_ui.stylesheet import apply_stylesheet
from zeno_ui.workflows import (
    PaletteRefreshResult,
    resolve_palette_default_project,
)


def _exec_dialog(dlg: Any) -> int:
    if hasattr(dlg, "exec"):
        return int(dlg.exec())
    return int(dlg.exec_())


def _show_raised(dlg: Any) -> None:
    """Non-modal show that raises the window above the host (Blender etc.)."""
    dlg.show()
    try:
        dlg.raise_()
        dlg.activateWindow()
    except Exception:  # pragma: no cover - older bindings
        pass


class ZenoNavigatorDialog:
    """Dashboard-styled Navigator: project, asset, version, representation → open."""

    def __init__(
        self,
        *,
        client: ZenoClient,
        parent: Any | None = None,
        default_version: str = "latest",
        default_representation: str = "blend",
        on_open: Callable[..., Any] | None = None,
        stay_on_top: bool = False,
        cache: ZenoDataCache | None = None,
    ) -> None:
        QtWidgets, QtCore, _ = get_qt_modules()
        self._client = client
        self._on_open = on_open
        self._cache = cache or global_cache()
        self._dlg = QtWidgets.QDialog(parent)
        self._dlg.setWindowTitle("Zeno Navigator")
        self._dlg.setMinimumWidth(520)
        if stay_on_top:
            self._dlg.setWindowFlags(
                self._dlg.windowFlags() | QtCore.Qt.WindowStaysOnTopHint
            )
        apply_stylesheet(self._dlg)

        root = QtWidgets.QHBoxLayout(self._dlg)
        accent = QtWidgets.QFrame(self._dlg)
        accent.setObjectName("lineAccent")
        root.addWidget(accent)

        col = QtWidgets.QVBoxLayout()
        root.addLayout(col, 1)

        title = QtWidgets.QLabel("Navigator")
        title.setStyleSheet("font-weight: bold; font-size: 12pt; color: #e5e5e5;")
        col.addWidget(title)
        sub = QtWidgets.QLabel("Browse project hierarchy and open assets")
        sub.setStyleSheet("color: #888888; font-size: 9pt;")
        col.addWidget(sub)

        self._project = QtWidgets.QComboBox(self._dlg)
        self._asset = QtWidgets.QComboBox(self._dlg)
        self._version = QtWidgets.QLineEdit(self._dlg)
        self._version.setText(default_version)
        self._representation = QtWidgets.QLineEdit(self._dlg)
        self._representation.setText(default_representation)

        col.addWidget(QtWidgets.QLabel("Project"))
        col.addWidget(self._project)
        col.addWidget(QtWidgets.QLabel("Asset"))
        col.addWidget(self._asset)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Version"))
        row.addWidget(self._version)
        col.addLayout(row)
        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(QtWidgets.QLabel("Representation"))
        row2.addWidget(self._representation)
        col.addLayout(row2)

        self._status = QtWidgets.QLabel("Loading projects…")
        self._status.setStyleSheet("color: #888888; font-size: 9pt;")
        col.addWidget(self._status)

        btn_row = QtWidgets.QHBoxLayout()
        refresh = QtWidgets.QPushButton("Refresh")
        open_btn = QtWidgets.QPushButton("Open selected")
        open_btn.setObjectName("primaryButton")
        open_btn.setProperty("primary", True)
        cancel = QtWidgets.QPushButton("Cancel")
        btn_row.addWidget(refresh)
        btn_row.addStretch(1)
        btn_row.addWidget(cancel)
        btn_row.addWidget(open_btn)
        col.addLayout(btn_row)

        self._projects_rows: list[dict[str, Any]] = []

        self._runner = make_async_runner()
        self._runner.finished.connect(self._on_projects_loaded)
        self._runner.failed.connect(self._on_load_failed)

        self._asset_runner = make_async_runner()
        self._asset_runner.finished.connect(self._on_assets_loaded)
        self._asset_runner.failed.connect(self._on_load_failed)

        self._project.currentIndexChanged.connect(self._on_project_changed)
        open_btn.clicked.connect(self._emit_open)
        cancel.clicked.connect(self._dlg.reject)
        refresh.clicked.connect(self._force_reload)

        self._reload_projects_async()

    def _reload_projects_async(self) -> None:
        self._status.setText("Loading projects…")
        client = self._client
        cache = self._cache
        self._runner.submit(lambda: cache.get_projects(client))

    def _on_projects_loaded(self, rows: Any) -> None:
        self._projects_rows = list(rows or [])
        self._project.blockSignals(True)
        self._project.clear()
        for row in self._projects_rows:
            label = f"{row['code']}  {row['name']}".strip()
            self._project.addItem(label, row["enum_value"])
        self._project.blockSignals(False)
        self._status.setText("") if self._projects_rows else self._status.setText("No projects")
        self._on_project_changed()

    def _on_project_changed(self) -> None:
        self._asset.blockSignals(True)
        self._asset.clear()
        self._asset.blockSignals(False)
        idx = self._project.currentIndex()
        if idx < 0:
            return
        enum_val = self._project.itemData(idx)
        pid = ""
        for row in self._projects_rows:
            if row["enum_value"] == enum_val:
                pid = row["id"]
                break
        if not pid:
            return
        self._status.setText("Loading assets…")
        client = self._client
        cache = self._cache
        self._asset_runner.submit(lambda: cache.get_assets(client, pid))

    def _on_assets_loaded(self, rows: Any) -> None:
        self._asset.blockSignals(True)
        self._asset.clear()
        for arow in list(rows or []):
            label = f"{arow['code']}  {arow['name']}".strip()
            self._asset.addItem(label, arow["enum_value"])
        self._asset.blockSignals(False)
        self._status.setText("") if rows else self._status.setText("No assets")

    def _on_load_failed(self, exc: Any) -> None:
        self._status.setText(f"Load failed: {type(exc).__name__}")

    def _force_reload(self) -> None:
        self._cache.clear()
        self._reload_projects_async()

    def _emit_open(self) -> None:
        pidx = self._project.currentIndex()
        aidx = self._asset.currentIndex()
        if pidx < 0 or aidx < 0:
            return
        enum_val = self._project.itemData(pidx)
        project_code = ""
        for row in self._projects_rows:
            if row["enum_value"] == enum_val:
                project_code = row["code"]
                break
        asset_code = str(self._asset.itemData(aidx) or "").strip()
        version = (self._version.text() or "latest").strip() or "latest"
        rep = (self._representation.text() or "blend").strip() or "blend"
        if not project_code or not asset_code:
            return
        if self._on_open:
            self._on_open(
                project=project_code,
                asset=asset_code,
                version=version,
                representation=rep,
            )
        self._dlg.accept()

    def exec_modal(self) -> int:
        return _exec_dialog(self._dlg)

    def show_non_modal(self) -> None:
        _show_raised(self._dlg)

    def widget(self) -> Any:
        return self._dlg


class ZenoPaletteDialog:
    """Dashboard-styled command palette: search, load / publish / version switch."""

    def __init__(
        self,
        *,
        client: ZenoClient,
        parent: Any | None = None,
        prefs_default_project: str = "",
        launch_hint: dict[str, Any] | None = None,
        default_representation: str = "blend",
        on_load: Callable[..., Any] | None = None,
        on_publish: Callable[..., Any] | None = None,
        on_report_issue: Callable[..., Any] | None = None,
        stay_on_top: bool = False,
        cache: ZenoDataCache | None = None,
    ) -> None:
        QtWidgets, QtCore, _ = get_qt_modules()
        self._client = client
        self._on_load = on_load
        self._on_publish = on_publish
        self._on_report_issue = on_report_issue
        self._default_representation = default_representation
        self._cache = cache or global_cache()
        self._state: PaletteRefreshResult | None = None
        self._launch_hint = dict(launch_hint or {})
        self._my_tasks_rows: list[dict[str, Any]] = []
        self._tasks_project_code: str = ""
        self._system_username = self._resolve_system_username().lower()
        self._dlg = QtWidgets.QDialog(parent)
        self._dlg.setWindowTitle("Chimera Command Palette")
        self._dlg.setMinimumWidth(480)
        if stay_on_top:
            self._dlg.setWindowFlags(
                self._dlg.windowFlags() | QtCore.Qt.WindowStaysOnTopHint
            )
        apply_stylesheet(self._dlg)

        root = QtWidgets.QHBoxLayout(self._dlg)
        accent = QtWidgets.QFrame(self._dlg)
        accent.setObjectName("lineAccent")
        root.addWidget(accent)
        col = QtWidgets.QVBoxLayout()
        root.addLayout(col, 1)

        title = QtWidgets.QLabel("Command Palette")
        title.setStyleSheet("font-weight: bold; font-size: 12pt; color: #e5e5e5;")
        col.addWidget(title)
        sub = QtWidgets.QLabel("Search assets, load versions, and publish")
        sub.setStyleSheet("color: #888888; font-size: 9pt;")
        col.addWidget(sub)

        self._project = QtWidgets.QLineEdit(self._dlg)
        # Default project resolution is cheap for prefs; only hits API if hint has no code.
        self._project.setText(prefs_default_project or "")
        self._query = QtWidgets.QLineEdit(self._dlg)
        self._action = QtWidgets.QComboBox(self._dlg)
        self._action.addItem("Load", "load")
        self._action.addItem("Publish", "publish")
        self._action.addItem("Version switch", "switch")
        self._action.addItem("Report issue", "report_issue")

        # Pipeline stage picker — required for publishes so the new version
        # lands in the correct per-stage feed on the dashboard. Kept visible
        # for all actions (cheap) so artists always see which stage context
        # will be used.
        self._stage = QtWidgets.QComboBox(self._dlg)
        self._stage.addItem("— Legacy / no stage —", "")
        self._stage.addItem("Modelling", "modelling")
        self._stage.addItem("Texturing", "texturing")
        self._stage.addItem("Rigging", "rigging")
        self._stage.addItem("Lookdev", "lookdev")
        # Prefer a stage coming from the DCC's launch hint (dashboard-driven
        # launches can set this explicitly); fall back to modelling — the
        # overwhelmingly common starting point — so users don't silently
        # publish into the legacy bucket.
        initial_stage = ""
        if isinstance(launch_hint, dict):
            initial_stage = str(launch_hint.get("pipeline_stage") or "").strip().lower()
        if not initial_stage:
            initial_stage = "modelling"
        idx = self._stage.findData(initial_stage)
        if idx >= 0:
            self._stage.setCurrentIndex(idx)

        self._asset = QtWidgets.QComboBox(self._dlg)
        self._task_search = QtWidgets.QLineEdit(self._dlg)
        self._task = QtWidgets.QComboBox(self._dlg)
        self._version = QtWidgets.QLineEdit(self._dlg)
        self._version.setText("latest")

        col.addWidget(QtWidgets.QLabel("Project"))
        col.addWidget(self._project)
        col.addWidget(QtWidgets.QLabel("Search"))
        col.addWidget(self._query)
        col.addWidget(QtWidgets.QLabel("Action"))
        col.addWidget(self._action)
        col.addWidget(QtWidgets.QLabel("Pipeline stage"))
        col.addWidget(self._stage)
        col.addWidget(QtWidgets.QLabel("Asset"))
        col.addWidget(self._asset)
        col.addWidget(QtWidgets.QLabel("Task (required for publish)"))
        self._task_search.setPlaceholderText("Search tasks by title, stage, or status…")
        col.addWidget(self._task_search)
        col.addWidget(self._task)
        col.addWidget(QtWidgets.QLabel("Version"))
        col.addWidget(self._version)

        preview = QtWidgets.QLabel("")
        preview.setWordWrap(True)
        preview.setStyleSheet("color: #888888; font-size: 9pt;")
        self._preview = preview
        col.addWidget(preview)

        btn_row = QtWidgets.QHBoxLayout()
        refresh = QtWidgets.QPushButton("Refresh")
        go = QtWidgets.QPushButton("Run")
        go.setObjectName("primaryButton")
        go.setProperty("primary", True)
        cancel = QtWidgets.QPushButton("Cancel")
        btn_row.addWidget(refresh)
        btn_row.addStretch(1)
        btn_row.addWidget(cancel)
        btn_row.addWidget(go)
        col.addLayout(btn_row)
        # Stash for enable/disable while background publishes run.
        self._run_button = go
        self._cancel_button = cancel
        self._refresh_button = refresh

        self._runner = make_async_runner()
        self._runner.finished.connect(self._on_state_loaded)
        self._runner.failed.connect(self._on_load_failed)

        self._default_runner = make_async_runner()
        self._default_runner.finished.connect(self._on_default_project_resolved)
        self._default_runner.failed.connect(self._on_load_failed)

        # Publish/load actions run off the Qt main thread so big uploads don't
        # freeze the palette. Results are marshalled back via signals.
        self._publish_runner = make_async_runner()
        self._publish_runner.finished.connect(self._on_publish_done)
        self._publish_runner.failed.connect(self._on_publish_failed)
        self._task_runner = make_async_runner()
        self._task_runner.finished.connect(self._on_my_tasks_loaded)
        self._task_runner.failed.connect(self._on_load_failed)

        refresh.clicked.connect(self._force_reload)
        go.clicked.connect(self._run)
        cancel.clicked.connect(self._dlg.reject)
        self._asset.currentIndexChanged.connect(lambda _: self._rebuild_task_options())
        self._task_search.textChanged.connect(lambda _: self._rebuild_task_options())

        self._debounced_refresh = make_debounced(self._do_refresh_async, interval_ms=250)
        self._query.textChanged.connect(lambda _: self._debounced_refresh())
        self._project.textChanged.connect(lambda _: self._debounced_refresh())

        if not self._project.text().strip():
            client_ref = self._client
            hint = launch_hint
            self._default_runner.submit(
                lambda: resolve_palette_default_project(
                    client_ref, prefs_default=prefs_default_project, hint=hint
                )
            )
        else:
            self._do_refresh_async()

    def _on_default_project_resolved(self, code: Any) -> None:
        code_s = str(code or "").strip()
        if code_s and not self._project.text().strip():
            self._project.blockSignals(True)
            self._project.setText(code_s)
            self._project.blockSignals(False)
        self._do_refresh_async()

    def _do_refresh_async(self) -> None:
        prev_asset = ""
        if self._asset.currentIndex() >= 0:
            prev_asset = str(self._asset.currentData() or "")
        if self._state is not None and not prev_asset:
            prev_asset = self._state.asset_code
        project = self._project.text()
        query = self._query.text()
        version = self._version.text()
        client = self._client
        cache = self._cache
        self._preview.setText("Loading…")
        self._runner.submit(
            lambda: refresh_palette_state_cached(
                cache,
                client,
                project_code=project,
                query=query,
                asset_code=prev_asset,
                version=version,
            )
        )

    def _on_state_loaded(self, st: Any) -> None:
        if not isinstance(st, PaletteRefreshResult):
            return
        self._state = st
        self._asset.blockSignals(True)
        self._asset.clear()
        for a in st.assets:
            code = str(a.get("code") or "").strip()
            label = f"{code}  {str(a.get('name') or code)}".strip()
            self._asset.addItem(label, code)
        self._asset.blockSignals(False)
        idx = self._asset.findData(st.asset_code)
        if idx >= 0:
            self._asset.setCurrentIndex(idx)
        self._version.setText(st.version)
        self._rebuild_task_options()
        project_code = (self._project.text() or "").strip()
        if project_code and project_code != self._tasks_project_code:
            self._tasks_project_code = project_code
            client = self._client
            self._task_runner.submit(lambda: self._load_username_scoped_tasks(client, project_code))
        prev = ""
        if st.assets:
            codes = [str(a.get("code") or "") for a in st.assets[:8] if str(a.get("code") or "").strip()]
            prev = "Matches: " + ", ".join(codes)
        if st.versions:
            prev = (prev + "\n" if prev else "") + "Versions: " + ", ".join(str(v) for v in st.versions[:12])
        self._preview.setText(prev.strip())

    def _on_my_tasks_loaded(self, rows: Any) -> None:
        self._my_tasks_rows = list(rows or [])
        self._rebuild_task_options()

    def _rebuild_task_options(self) -> None:
        self._task.blockSignals(True)
        self._task.clear()
        self._task.addItem("Select task…", "")
        selected_asset_code = str(self._asset.currentData() or "").strip()
        selected_asset_id = ""
        for a in (self._state.assets if self._state else []):
            if str(a.get("code") or "").strip() == selected_asset_code:
                raw = a.get("raw") if isinstance(a.get("raw"), dict) else a
                selected_asset_id = str(raw.get("id") or "")
                break
        query = (self._task_search.text() or "").strip().lower()
        filtered = [
            t
            for t in self._my_tasks_rows
            if (query or not selected_asset_id or str(t.get("asset_id") or "") == selected_asset_id)
        ]
        for t in filtered:
            task_id = str(t.get("id") or "")
            if not task_id:
                continue
            title = str(t.get("title") or "").strip() or str(t.get("type") or "task")
            stage = str((t.get("metadata") or {}).get("stage") or t.get("type") or "").strip()
            status = str(t.get("status") or "").strip()
            if query:
                haystack = f"{title} {stage} {status}".lower()
                if query not in haystack:
                    continue
            label = f"{title} [{stage}] ({status})".strip()
            self._task.addItem(label, task_id)
        preferred = str(self._launch_hint.get("task_id") or "").strip()
        if preferred:
            idx = self._task.findData(preferred)
            if idx >= 0:
                self._task.setCurrentIndex(idx)
        self._task.blockSignals(False)

    def _resolve_system_username(self) -> str:
        # Resolve username using OS-specific conventions first:
        # - Windows: USERNAME
        # - Linux/macOS: USER, then LOGNAME
        # then fall back to cross-platform stdlib helpers.
        if os.name == "nt":
            env_username = str(os.environ.get("USERNAME") or "").strip()
            if env_username:
                return env_username
        else:
            env_user = str(os.environ.get("USER") or "").strip()
            if env_user:
                return env_user
            env_logname = str(os.environ.get("LOGNAME") or "").strip()
            if env_logname:
                return env_logname
        try:
            guessed = str(getpass.getuser() or "").strip()
            if guessed:
                return guessed
        except Exception:
            pass
        try:
            return str(os.getlogin() or "").strip() or "unknown"
        except OSError:
            return "unknown"

    def _load_username_scoped_tasks(self, client: ZenoClient, project_code: str) -> list[dict[str, Any]]:
        project_id = ""
        try:
            projects = client.list_projects(code=project_code)
            if projects:
                project_id = str(projects[0].get("id") or "").strip()
        except Exception:
            project_id = ""
        if not project_id:
            return []

        tasks = client.list_tasks(project_id=project_id)
        try:
            users = client.list_users(is_active=True)
        except Exception:
            users = []
        uname = self._system_username

        matched_user_ids: set[str] = set()
        for u in users:
            uid = str(u.get("id") or "").strip()
            if not uid:
                continue
            username = str(u.get("username") or "").strip().lower()
            name = str(u.get("name") or "").strip().lower()
            if uname and (uname == username or uname == name):
                matched_user_ids.add(uid)

        # Fallback: if username mapping failed, keep server-side /mine semantics.
        if not matched_user_ids:
            try:
                return client.list_my_tasks(project_id=project_id)
            except Exception:
                return []

        out: list[dict[str, Any]] = []
        for t in tasks:
            assignee_ids = set(
                str(x).strip()
                for x in [t.get("assignee_id"), *(t.get("assignees") or []), *(t.get("collaborators") or [])]
                if str(x).strip()
            )
            if assignee_ids.intersection(matched_user_ids):
                out.append(t)
        return out

    def _on_load_failed(self, exc: Any) -> None:
        self._preview.setText(f"Load failed: {type(exc).__name__}: {exc}")

    def _force_reload(self) -> None:
        self._cache.clear()
        self._do_refresh_async()

    def _run(self) -> None:
        action = self._action.currentData()
        project = (self._project.text() or "").strip()
        # Report Issue has no asset dependency, but we *do* forward whichever
        # asset/project the artist currently has selected so the issue lands
        # against the right entity automatically.
        if action == "report_issue":
            current_asset = ""
            if self._asset.currentIndex() >= 0:
                current_asset = str(self._asset.currentData() or "")
            assets_rows: list[dict[str, Any]] = []
            if self._state is not None:
                assets_rows = list(self._state.assets or [])
            self._open_report_issue_dialog(
                project=project,
                current_asset_code=current_asset,
                assets=assets_rows,
            )
            return
        if not self._state or not self._state.assets:
            self._preview.setText(
                "No assets loaded yet — reloading. Wait a moment and click Run again."
            )
            self._do_refresh_async()
            return
        asset_code = str(self._asset.currentData() or "").strip()
        if not asset_code:
            self._preview.setText("Select an asset before running.")
            return
        version = (self._version.text() or "latest").strip() or "latest"
        if action == "publish":
            if not self._on_publish:
                # No callback wired — surface the condition instead of silently
                # swallowing the click (this was the previous behaviour and why
                # users saw "nothing happening, no logs").
                self._preview.setText(
                    "Publish is not wired into this palette instance. "
                    "Open the palette from inside a DCC to publish the "
                    "currently open file."
                )
                return
            stage = str(self._stage.currentData() or "").strip().lower()
            stage_label = self._stage.currentText() if stage else "legacy (no stage)"
            task_id = str(self._task.currentData() or "").strip()
            if not task_id:
                self._preview.setText(
                    "Select a task before publishing. Only tasks assigned to you "
                    "or where you're a collaborator are listed."
                )
                return
            self._preview.setText(f"Publishing to {stage_label}…")
            self._set_busy(True)
            on_publish = self._on_publish
            payload = dict(
                project=project,
                asset=asset_code,
                representation=self._default_representation,
                pipeline_stage=stage,
                task_id=task_id,
            )
            self._publish_runner.submit(lambda: on_publish(**payload))
            return
        if self._on_load:
            self._on_load(
                project=project,
                asset=asset_code,
                version=version,
                representation=self._default_representation,
            )
        self._dlg.accept()

    def _set_busy(self, busy: bool) -> None:
        """Toggle button state during an in-flight publish/load so the artist
        doesn't fire duplicate operations (and so the dialog visibly reflects
        that work is happening)."""
        for btn in (
            getattr(self, "_run_button", None),
            getattr(self, "_refresh_button", None),
        ):
            if btn is not None:
                try:
                    btn.setEnabled(not busy)
                except Exception:
                    pass

    def _on_publish_done(self, result: Any) -> None:
        # Re-enable buttons but deliberately keep the palette OPEN. Users
        # often publish multiple stages in a row (e.g. modelling → lookdev)
        # and auto-closing the dialog forced them to re-open it from the
        # DCC after every publish. They explicitly asked for it to stay up
        # regardless of outcome.
        self._set_busy(False)
        ok = True
        message = "Published."
        version_num: Any = None
        stage_val: Any = None
        if isinstance(result, dict):
            ok = bool(result.get("ok", True))
            message = str(result.get("message") or ("Published" if ok else "Publish failed"))
            version_num = result.get("version")
            stage_val = result.get("pipeline_stage")
        if ok:
            stage_str = str(stage_val or "").strip()
            stage_suffix = f" to {stage_str} stage" if stage_str else ""
            if version_num is not None:
                self._preview.setText(
                    f"Published v{version_num}{stage_suffix}. {message}".strip()
                )
            else:
                self._preview.setText(message)
        else:
            self._preview.setText(f"Publish failed: {message}")

    def _on_publish_failed(self, exc: Any) -> None:
        # Same rationale as _on_publish_done: keep the palette open so the
        # artist can read the error and retry without a re-open round-trip.
        self._set_busy(False)
        self._preview.setText(f"Publish failed: {type(exc).__name__}: {exc}")

    def _open_report_issue_dialog(
        self,
        *,
        project: str,
        current_asset_code: str = "",
        assets: list[dict[str, Any]] | None = None,
    ) -> None:
        """Compact modal asking the artist for title / body / entity / attachment."""
        QtWidgets, QtCore, _ = get_qt_modules()
        dlg = QtWidgets.QDialog(self._dlg)
        dlg.setWindowTitle("Report Issue")
        dlg.setMinimumWidth(420)
        apply_stylesheet(dlg)

        root = QtWidgets.QVBoxLayout(dlg)
        root.addWidget(QtWidgets.QLabel("Title"))
        title_edit = QtWidgets.QLineEdit(dlg)
        root.addWidget(title_edit)

        root.addWidget(QtWidgets.QLabel("Details"))
        body_edit = QtWidgets.QPlainTextEdit(dlg)
        body_edit.setMinimumHeight(120)
        root.addWidget(body_edit)

        # Entity (asset) selection. The palette's current asset is pre-selected
        # but the artist can switch or drop to "No entity" if the issue is
        # project-wide.
        root.addWidget(QtWidgets.QLabel("Asset (optional)"))
        asset_combo = QtWidgets.QComboBox(dlg)
        asset_combo.addItem("— No asset (project-level) —", "")
        for a in assets or []:
            code = str(a.get("code") or "").strip()
            if not code:
                continue
            label = f"{code}  {str(a.get('name') or code)}".strip()
            asset_combo.addItem(label, code)
        if current_asset_code:
            idx = asset_combo.findData(current_asset_code)
            if idx >= 0:
                asset_combo.setCurrentIndex(idx)
        root.addWidget(asset_combo)

        attach_row = QtWidgets.QHBoxLayout()
        attachment_path = {"path": ""}
        attach_label = QtWidgets.QLabel("No attachment")
        attach_label.setStyleSheet("color: #888888; font-size: 9pt;")

        def choose_file() -> None:
            f, _flt = QtWidgets.QFileDialog.getOpenFileName(dlg, "Attach media")
            if f:
                attachment_path["path"] = f
                attach_label.setText(os.path.basename(f))

        attach_btn = QtWidgets.QPushButton("Attach media…")
        attach_btn.clicked.connect(choose_file)
        attach_row.addWidget(attach_btn)
        attach_row.addWidget(attach_label, 1)
        root.addLayout(attach_row)

        try:
            reporter = os.getlogin()
        except OSError:
            reporter = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
        dcc_name = detect_dcc()

        meta_line = QtWidgets.QLabel(
            f"Reporter: {reporter}    DCC: {dcc_name}    Project: {project or '—'}"
        )
        meta_line.setStyleSheet("color: #888888; font-size: 9pt;")
        root.addWidget(meta_line)

        err_label = QtWidgets.QLabel("")
        err_label.setStyleSheet("color: #ff6b6b; font-size: 9pt;")
        err_label.setWordWrap(True)
        root.addWidget(err_label)

        btn_row = QtWidgets.QHBoxLayout()
        cancel = QtWidgets.QPushButton("Cancel")
        submit = QtWidgets.QPushButton("Submit")
        submit.setProperty("primary", True)
        btn_row.addStretch(1)
        btn_row.addWidget(cancel)
        btn_row.addWidget(submit)
        root.addLayout(btn_row)

        def on_submit() -> None:
            title = title_edit.text().strip()
            if not title:
                err_label.setText("A title is required.")
                return
            selected_asset = str(asset_combo.currentData() or "").strip()
            payload = {
                "title": title,
                "body": body_edit.toPlainText(),
                "project_code": project,
                "asset_code": selected_asset or None,
                "reporter": reporter,
                "dcc": dcc_name,
                "source": "palette",
                "attachment_path": attachment_path["path"] or None,
            }
            try:
                if self._on_report_issue is not None:
                    self._on_report_issue(payload)
                dlg.accept()
                self._preview.setText(f"Issue submitted: {title}")
            except Exception as exc:  # noqa: BLE001
                err_label.setText(f"Submit failed: {type(exc).__name__}: {exc}")

        submit.clicked.connect(on_submit)
        cancel.clicked.connect(dlg.reject)
        _exec_dialog(dlg)

    def exec_modal(self) -> int:
        return _exec_dialog(self._dlg)

    def show_non_modal(self) -> None:
        _show_raised(self._dlg)

    def widget(self) -> Any:
        return self._dlg


__all__ = ["ZenoNavigatorDialog", "ZenoPaletteDialog"]
