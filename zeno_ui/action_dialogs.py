from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
import getpass
import os

from zeno_client.client import ZenoClient
from zeno_ui.qt_compat import get_qt_modules
from zeno_ui.stylesheet import apply_stylesheet
from zeno_ui.workflows import resolve_palette_default_project


def _exec_dialog(dlg: Any) -> int:
    if hasattr(dlg, "exec"):
        return int(dlg.exec())
    return int(dlg.exec_())


def _show_raised(dlg: Any) -> None:
    dlg.show()
    try:
        dlg.raise_()
        dlg.activateWindow()
    except Exception:
        pass


def _resolve_system_username() -> str:
    if os.name == "nt":
        v = str(os.environ.get("USERNAME") or "").strip()
        if v:
            return v
    else:
        v = str(os.environ.get("USER") or "").strip() or str(os.environ.get("LOGNAME") or "").strip()
        if v:
            return v
    try:
        return str(getpass.getuser() or "").strip() or "unknown"
    except Exception:
        return "unknown"


def _extract_stage(group: dict[str, Any]) -> str:
    stage = str(group.get("pipeline_stage") or "").strip().lower()
    if stage:
        return stage
    meta = group.get("metadata")
    if isinstance(meta, dict):
        stage = str(meta.get("pipeline_stage") or "").strip().lower()
    return stage


@dataclass
class _AssetContext:
    project_id: str
    project_code: str
    asset_id: str
    asset_code: str
    asset_name: str


class ZenoNavigatorActionDialog:
    def __init__(
        self,
        *,
        client: ZenoClient,
        parent: Any | None = None,
        launch_hint: dict[str, Any] | None = None,
        prefs_default_project: str = "",
        on_load_entity: Callable[..., Any] | None = None,
        stay_on_top: bool = False,
    ) -> None:
        QtWidgets, QtCore, _ = get_qt_modules()
        self._client = client
        self._launch_hint = dict(launch_hint or {})
        self._on_load_entity = on_load_entity
        self._assets: list[dict[str, Any]] = []
        self._groups_by_asset_id: dict[str, list[dict[str, Any]]] = {}
        self._asset_ctx: _AssetContext | None = None

        self._dlg = QtWidgets.QDialog(parent)
        self._dlg.setWindowTitle("Zeno Navigator")
        self._dlg.setMinimumWidth(640)
        if stay_on_top:
            self._dlg.setWindowFlags(self._dlg.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        apply_stylesheet(self._dlg)

        root = QtWidgets.QVBoxLayout(self._dlg)
        title = QtWidgets.QLabel("Navigator")
        title.setStyleSheet("font-weight: bold; font-size: 12pt;")
        root.addWidget(title)

        root.addWidget(QtWidgets.QLabel("Project"))
        self._project = QtWidgets.QLineEdit(self._dlg)
        self._project.setReadOnly(True)
        root.addWidget(self._project)

        root.addWidget(QtWidgets.QLabel("Entities"))
        self._tree = QtWidgets.QTreeWidget(self._dlg)
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumHeight(220)
        root.addWidget(self._tree)

        root.addWidget(QtWidgets.QLabel("Selected Asset"))
        self._selected_asset = QtWidgets.QLineEdit(self._dlg)
        self._selected_asset.setReadOnly(True)
        root.addWidget(self._selected_asset)

        root.addWidget(QtWidgets.QLabel("Stage"))
        self._stage = QtWidgets.QComboBox(self._dlg)
        root.addWidget(self._stage)

        root.addWidget(QtWidgets.QLabel("Version"))
        self._version = QtWidgets.QComboBox(self._dlg)
        root.addWidget(self._version)

        root.addWidget(QtWidgets.QLabel("Representation"))
        self._representation = QtWidgets.QComboBox(self._dlg)
        root.addWidget(self._representation)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #888888; font-size: 9pt;")
        root.addWidget(self._status)

        row = QtWidgets.QHBoxLayout()
        self._load = QtWidgets.QPushButton("Load Entity")
        self._load.setProperty("primary", True)
        self._refresh = QtWidgets.QPushButton("Refresh")
        self._cancel = QtWidgets.QPushButton("Cancel")
        row.addWidget(self._refresh)
        row.addStretch(1)
        row.addWidget(self._cancel)
        row.addWidget(self._load)
        root.addLayout(row)

        self._tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self._stage.currentIndexChanged.connect(lambda _=None: self._rebuild_versions())
        self._version.currentIndexChanged.connect(lambda _=None: self._rebuild_representations())
        self._load.clicked.connect(self._on_load_clicked)
        self._refresh.clicked.connect(self._reload)
        self._cancel.clicked.connect(self._dlg.reject)

        self._reload(default_project=prefs_default_project)

    def _reload(self, *, default_project: str = "") -> None:
        QtWidgets, QtCore, _ = get_qt_modules()
        self._status.setText("Loading entities…")
        self._tree.clear()
        self._stage.clear()
        self._version.clear()
        self._representation.clear()
        try:
            project_code = resolve_palette_default_project(
                self._client,
                prefs_default=default_project,
                hint=self._launch_hint,
            )
            if not project_code:
                self._status.setText("No project context available.")
                return
            projects = self._client.list_projects(code=project_code)
            if not projects:
                self._status.setText("Project not found in API.")
                return
            p = projects[0]
            project_id = str(p.get("id") or "")
            project_name = str(p.get("name") or project_code).strip()
            self._project.setText(f"{project_code}  {project_name}")
            self._assets = self._client.list_assets(project_id)
            self._groups_by_asset_id = {}
            for a in self._assets:
                aid = str(a.get("id") or "").strip()
                if not aid:
                    continue
                try:
                    self._groups_by_asset_id[aid] = self._client.list_asset_version_groups(aid)
                except Exception:
                    self._groups_by_asset_id[aid] = []
            root = self._tree.invisibleRootItem()
            entity_root = QtWidgets.QTreeWidgetItem(["Entity"])
            root.addChild(entity_root)
            assets_root = QtWidgets.QTreeWidgetItem(["Assets"])
            entity_root.addChild(assets_root)
            hinted_asset_id = str(self._launch_hint.get("asset_id") or "").strip()
            hinted_item = None
            for a in self._assets:
                aid = str(a.get("id") or "").strip()
                code = str(a.get("code") or "").strip()
                name = str(a.get("name") or code).strip()
                item = QtWidgets.QTreeWidgetItem([f"{code}  {name}".strip()])
                item.setData(0, QtCore.Qt.UserRole, {"project_id": project_id, "project_code": project_code, "asset_id": aid, "asset_code": code, "asset_name": name})
                assets_root.addChild(item)
                if hinted_asset_id and aid == hinted_asset_id:
                    hinted_item = item
            self._tree.expandItem(entity_root)
            self._tree.expandItem(assets_root)
            if hinted_item is not None:
                self._tree.setCurrentItem(hinted_item)
            self._status.setText("" if self._assets else "No assets found for project.")
        except Exception as exc:
            self._status.setText(f"Load failed: {type(exc).__name__}: {exc}")

    def _on_tree_selection_changed(self) -> None:
        QtWidgets, QtCore, _ = get_qt_modules()
        items = self._tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, QtCore.Qt.UserRole)
        if not isinstance(data, dict):
            return
        self._asset_ctx = _AssetContext(
            project_id=str(data.get("project_id") or ""),
            project_code=str(data.get("project_code") or ""),
            asset_id=str(data.get("asset_id") or ""),
            asset_code=str(data.get("asset_code") or ""),
            asset_name=str(data.get("asset_name") or ""),
        )
        self._selected_asset.setText(f"{self._asset_ctx.asset_code}  {self._asset_ctx.asset_name}".strip())
        self._rebuild_stages()

    def _rebuild_stages(self) -> None:
        self._stage.blockSignals(True)
        self._stage.clear()
        default_stages = ["modelling", "texturing", "rigging", "lookdev"]
        if not self._asset_ctx:
            self._stage.blockSignals(False)
            return
        groups = self._groups_by_asset_id.get(self._asset_ctx.asset_id, [])
        seen = set()
        for g in groups:
            stage = _extract_stage(g)
            if stage:
                seen.add(stage)
        if not seen:
            seen = set(default_stages)
        ordered = sorted(seen)
        for s in ordered:
            self._stage.addItem(s.title(), s)
        self._stage.blockSignals(False)
        self._rebuild_versions()

    def _rebuild_versions(self) -> None:
        self._version.blockSignals(True)
        self._version.clear()
        if not self._asset_ctx:
            self._version.blockSignals(False)
            return
        stage = str(self._stage.currentData() or "").strip().lower()
        groups = self._groups_by_asset_id.get(self._asset_ctx.asset_id, [])
        nums: list[int] = []
        for g in groups:
            if stage and _extract_stage(g) and _extract_stage(g) != stage:
                continue
            try:
                nums.append(int(g.get("version_number")))
            except Exception:
                pass
        for n in sorted(set(nums), reverse=True):
            self._version.addItem(f"v{n:03d}", str(n))
        self._version.blockSignals(False)
        self._rebuild_representations()

    def _rebuild_representations(self) -> None:
        self._representation.clear()
        if not self._asset_ctx:
            return
        stage = str(self._stage.currentData() or "").strip().lower()
        version = str(self._version.currentData() or "").strip()
        groups = self._groups_by_asset_id.get(self._asset_ctx.asset_id, [])
        reps: set[str] = set()
        for g in groups:
            if version and str(g.get("version_number") or "") != version:
                continue
            gstage = _extract_stage(g)
            if stage and gstage and gstage != stage:
                continue
            rep = str(g.get("representation") or "").strip()
            if rep:
                reps.add(rep)
        if not reps:
            reps.add("blend")
        for rep in sorted(reps):
            self._representation.addItem(rep, rep)

    def _on_load_clicked(self) -> None:
        if not self._asset_ctx:
            self._status.setText("Select an asset first.")
            return
        version = str(self._version.currentData() or "").strip() or "latest"
        representation = str(self._representation.currentData() or "").strip() or "blend"
        if self._on_load_entity:
            self._on_load_entity(
                project=self._asset_ctx.project_code,
                asset=self._asset_ctx.asset_code,
                version=version,
                representation=representation,
            )
        self._dlg.accept()

    def show_non_modal(self) -> None:
        _show_raised(self._dlg)

    def exec_modal(self) -> int:
        return _exec_dialog(self._dlg)

    def widget(self) -> Any:
        return self._dlg


class ZenoVersionSwitcherDialog:
    def __init__(
        self,
        *,
        client: ZenoClient,
        parent: Any | None = None,
        launch_hint: dict[str, Any] | None = None,
        prefs_default_project: str = "",
        on_switch_version: Callable[..., Any] | None = None,
        stay_on_top: bool = False,
    ) -> None:
        QtWidgets, QtCore, _ = get_qt_modules()
        self._client = client
        self._on_switch_version = on_switch_version
        self._hint = dict(launch_hint or {})
        self._asset_ctx: _AssetContext | None = None
        self._groups: list[dict[str, Any]] = []

        self._dlg = QtWidgets.QDialog(parent)
        self._dlg.setWindowTitle("Zeno Version Switcher")
        self._dlg.setMinimumWidth(520)
        if stay_on_top:
            self._dlg.setWindowFlags(self._dlg.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        apply_stylesheet(self._dlg)

        root = QtWidgets.QVBoxLayout(self._dlg)
        root.addWidget(QtWidgets.QLabel("Asset Entity"))
        self._asset = QtWidgets.QLineEdit(self._dlg)
        self._asset.setReadOnly(True)
        root.addWidget(self._asset)
        root.addWidget(QtWidgets.QLabel("Stage"))
        self._stage = QtWidgets.QLineEdit(self._dlg)
        self._stage.setReadOnly(True)
        root.addWidget(self._stage)
        root.addWidget(QtWidgets.QLabel("Version"))
        self._version = QtWidgets.QComboBox(self._dlg)
        root.addWidget(self._version)
        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color: #888888; font-size: 9pt;")
        root.addWidget(self._status)

        row = QtWidgets.QHBoxLayout()
        self._switch = QtWidgets.QPushButton("Switch Version")
        self._switch.setProperty("primary", True)
        self._cancel = QtWidgets.QPushButton("Cancel")
        row.addStretch(1)
        row.addWidget(self._cancel)
        row.addWidget(self._switch)
        root.addLayout(row)

        self._switch.clicked.connect(self._on_switch)
        self._cancel.clicked.connect(self._dlg.reject)
        self._bootstrap(default_project=prefs_default_project)

    def _bootstrap(self, *, default_project: str = "") -> None:
        self._status.setText("Loading context…")
        try:
            project_code = resolve_palette_default_project(
                self._client,
                prefs_default=default_project,
                hint=self._hint,
            )
            asset_id_hint = str(self._hint.get("asset_id") or "").strip()
            stage_hint = str(self._hint.get("pipeline_stage") or "").strip().lower() or "modelling"
            if not project_code:
                self._status.setText("No project context available.")
                return
            projects = self._client.list_projects(code=project_code)
            if not projects:
                self._status.setText("Project not found.")
                return
            project_id = str(projects[0].get("id") or "")
            assets = self._client.list_assets(project_id)
            match = None
            if asset_id_hint:
                for a in assets:
                    if str(a.get("id") or "").strip() == asset_id_hint:
                        match = a
                        break
            if not match and assets:
                match = assets[0]
            if not match:
                self._status.setText("No assets available.")
                return
            self._asset_ctx = _AssetContext(
                project_id=project_id,
                project_code=project_code,
                asset_id=str(match.get("id") or ""),
                asset_code=str(match.get("code") or ""),
                asset_name=str(match.get("name") or ""),
            )
            self._asset.setText(f"{self._asset_ctx.asset_code}  {self._asset_ctx.asset_name}".strip())
            self._stage.setText(stage_hint)
            self._groups = self._client.list_asset_version_groups(self._asset_ctx.asset_id)
            nums: list[int] = []
            for g in self._groups:
                gstage = _extract_stage(g)
                if gstage and gstage != stage_hint:
                    continue
                try:
                    nums.append(int(g.get("version_number")))
                except Exception:
                    pass
            for n in sorted(set(nums), reverse=True):
                self._version.addItem(f"v{n:03d}", str(n))
            self._status.setText("" if nums else "No versions found for this stage.")
        except Exception as exc:
            self._status.setText(f"Load failed: {type(exc).__name__}: {exc}")

    def _on_switch(self) -> None:
        if not self._asset_ctx:
            return
        version = str(self._version.currentData() or "").strip()
        if not version:
            self._status.setText("Select a version.")
            return
        stage = (self._stage.text() or "").strip().lower()
        rep = "blend"
        for g in self._groups:
            if str(g.get("version_number") or "") != version:
                continue
            gstage = _extract_stage(g)
            if stage and gstage and gstage != stage:
                continue
            candidate = str(g.get("representation") or "").strip()
            if candidate:
                rep = candidate
                break
        if self._on_switch_version:
            self._on_switch_version(
                project=self._asset_ctx.project_code,
                asset=self._asset_ctx.asset_code,
                version=version,
                representation=rep,
                pipeline_stage=stage,
            )
        self._dlg.accept()

    def show_non_modal(self) -> None:
        _show_raised(self._dlg)

    def exec_modal(self) -> int:
        return _exec_dialog(self._dlg)

    def widget(self) -> Any:
        return self._dlg


class ZenoReportIssueDialog:
    def __init__(
        self,
        *,
        client: ZenoClient,
        parent: Any | None = None,
        launch_hint: dict[str, Any] | None = None,
        prefs_default_project: str = "",
        on_raise_ticket: Callable[[dict[str, Any]], Any] | None = None,
        stay_on_top: bool = False,
    ) -> None:
        QtWidgets, QtCore, _ = get_qt_modules()
        self._client = client
        self._hint = dict(launch_hint or {})
        self._prefs_default_project = prefs_default_project
        self._on_raise_ticket = on_raise_ticket
        self._assets: list[dict[str, Any]] = []

        self._dlg = QtWidgets.QDialog(parent)
        self._dlg.setWindowTitle("Report Issue")
        self._dlg.setMinimumWidth(520)
        if stay_on_top:
            self._dlg.setWindowFlags(self._dlg.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        apply_stylesheet(self._dlg)
        root = QtWidgets.QVBoxLayout(self._dlg)
        root.addWidget(QtWidgets.QLabel("Title"))
        self._title = QtWidgets.QLineEdit(self._dlg)
        root.addWidget(self._title)
        root.addWidget(QtWidgets.QLabel("Details"))
        self._body = QtWidgets.QPlainTextEdit(self._dlg)
        self._body.setMinimumHeight(120)
        root.addWidget(self._body)
        root.addWidget(QtWidgets.QLabel("Asset (optional)"))
        self._asset = QtWidgets.QComboBox(self._dlg)
        root.addWidget(self._asset)
        attach_row = QtWidgets.QHBoxLayout()
        self._attachment_path = ""
        self._attachment_label = QtWidgets.QLabel("No attachment")
        self._attachment_label.setStyleSheet("color: #888888; font-size: 9pt;")
        self._attach = QtWidgets.QPushButton("Attach media…")
        attach_row.addWidget(self._attach)
        attach_row.addWidget(self._attachment_label, 1)
        root.addLayout(attach_row)
        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color: #888888; font-size: 9pt;")
        self._status.setWordWrap(True)
        root.addWidget(self._status)
        row = QtWidgets.QHBoxLayout()
        self._raise = QtWidgets.QPushButton("Raise Ticket")
        self._raise.setProperty("primary", True)
        self._refresh = QtWidgets.QPushButton("Refresh")
        self._cancel = QtWidgets.QPushButton("Cancel")
        row.addWidget(self._refresh)
        row.addStretch(1)
        row.addWidget(self._cancel)
        row.addWidget(self._raise)
        root.addLayout(row)

        self._attach.clicked.connect(self._choose_attachment)
        self._raise.clicked.connect(self._submit)
        self._refresh.clicked.connect(self._reset)
        self._cancel.clicked.connect(self._dlg.reject)

        self._bootstrap()

    def _bootstrap(self) -> None:
        self._reset()
        try:
            project_code = resolve_palette_default_project(
                self._client,
                prefs_default=self._prefs_default_project,
                hint=self._hint,
            )
            if not project_code:
                self._status.setText("No project context available.")
                return
            self._project_code = project_code
            projects = self._client.list_projects(code=project_code)
            if not projects:
                self._status.setText("Project not found.")
                return
            project_id = str(projects[0].get("id") or "")
            self._assets = self._client.list_assets(project_id)
            self._asset.clear()
            self._asset.addItem("— No asset (project-level) —", "")
            hinted_asset_id = str(self._hint.get("asset_id") or "").strip()
            for a in self._assets:
                aid = str(a.get("id") or "").strip()
                code = str(a.get("code") or "").strip()
                name = str(a.get("name") or code).strip()
                self._asset.addItem(f"{code}  {name}".strip(), code)
                if hinted_asset_id and aid == hinted_asset_id:
                    idx = self._asset.count() - 1
                    self._asset.setCurrentIndex(idx)
            self._status.setText("")
        except Exception as exc:
            self._status.setText(f"Load failed: {type(exc).__name__}: {exc}")

    def _reset(self) -> None:
        self._title.setText("")
        self._body.setPlainText("")
        self._attachment_path = ""
        self._attachment_label.setText("No attachment")
        self._status.setText("")

    def _choose_attachment(self) -> None:
        QtWidgets, _, _ = get_qt_modules()
        path, _flt = QtWidgets.QFileDialog.getOpenFileName(self._dlg, "Attach media")
        if path:
            self._attachment_path = path
            self._attachment_label.setText(os.path.basename(path))

    def _submit(self) -> None:
        title = (self._title.text() or "").strip()
        if not title:
            self._status.setText("A title is required.")
            return
        payload = {
            "title": title,
            "body": self._body.toPlainText(),
            "project_code": getattr(self, "_project_code", ""),
            "asset_code": str(self._asset.currentData() or "").strip() or None,
            "reporter": _resolve_system_username(),
            "dcc": "blender",
            "source": "palette",
            "attachment_path": self._attachment_path or None,
        }
        try:
            if self._on_raise_ticket:
                self._on_raise_ticket(payload)
            self._status.setText("Issue submitted.")
            self._dlg.accept()
        except Exception as exc:
            self._status.setText(f"Submit failed: {type(exc).__name__}: {exc}")

    def show_non_modal(self) -> None:
        _show_raised(self._dlg)

    def exec_modal(self) -> int:
        return _exec_dialog(self._dlg)

    def widget(self) -> Any:
        return self._dlg


class ZenoPublisherDialog:
    def __init__(
        self,
        *,
        client: ZenoClient,
        parent: Any | None = None,
        launch_hint: dict[str, Any] | None = None,
        prefs_default_project: str = "",
        on_publish: Callable[..., Any] | None = None,
        stay_on_top: bool = False,
    ) -> None:
        QtWidgets, QtCore, _ = get_qt_modules()
        self._client = client
        self._hint = dict(launch_hint or {})
        self._prefs_default_project = prefs_default_project
        self._on_publish = on_publish
        self._asset_ctx: _AssetContext | None = None
        self._all_tasks: list[dict[str, Any]] = []
        self._user_by_id: dict[str, dict[str, Any]] = {}

        self._dlg = QtWidgets.QDialog(parent)
        self._dlg.setWindowTitle("Publisher")
        self._dlg.setMinimumWidth(620)
        if stay_on_top:
            self._dlg.setWindowFlags(self._dlg.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        apply_stylesheet(self._dlg)
        root = QtWidgets.QVBoxLayout(self._dlg)
        root.addWidget(QtWidgets.QLabel("Project"))
        self._project = QtWidgets.QLineEdit(self._dlg)
        self._project.setReadOnly(True)
        root.addWidget(self._project)
        root.addWidget(QtWidgets.QLabel("Asset Entity"))
        self._asset = QtWidgets.QLineEdit(self._dlg)
        self._asset.setReadOnly(True)
        root.addWidget(self._asset)
        root.addWidget(QtWidgets.QLabel("Stage"))
        self._stage = QtWidgets.QComboBox(self._dlg)
        self._stage.addItem("Modelling", "modelling")
        self._stage.addItem("Texturing", "texturing")
        self._stage.addItem("Rigging", "rigging")
        self._stage.addItem("Lookdev", "lookdev")
        root.addWidget(self._stage)
        root.addWidget(QtWidgets.QLabel("Tasks assigned to you"))
        self._task_search = QtWidgets.QLineEdit(self._dlg)
        self._task_search.setPlaceholderText("Filter tasks by title, stage, status…")
        root.addWidget(self._task_search)
        self._task_list = QtWidgets.QListWidget(self._dlg)
        self._task_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._task_list.setMinimumHeight(180)
        root.addWidget(self._task_list)
        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color: #888888; font-size: 9pt;")
        self._status.setWordWrap(True)
        root.addWidget(self._status)
        row = QtWidgets.QHBoxLayout()
        self._publish = QtWidgets.QPushButton("Publish")
        self._publish.setProperty("primary", True)
        self._refresh = QtWidgets.QPushButton("Refresh")
        self._cancel = QtWidgets.QPushButton("Cancel")
        row.addWidget(self._refresh)
        row.addStretch(1)
        row.addWidget(self._cancel)
        row.addWidget(self._publish)
        root.addLayout(row)

        self._task_search.textChanged.connect(lambda _=None: self._render_tasks())
        self._refresh.clicked.connect(self._bootstrap)
        self._cancel.clicked.connect(self._dlg.reject)
        self._publish.clicked.connect(self._publish_clicked)
        self._bootstrap()

    def _bootstrap(self) -> None:
        self._status.setText("Loading publisher context…")
        self._all_tasks = []
        self._task_list.clear()
        try:
            project_code = resolve_palette_default_project(
                self._client,
                prefs_default=self._prefs_default_project,
                hint=self._hint,
            )
            if not project_code:
                self._status.setText("No project context available.")
                return
            projects = self._client.list_projects(code=project_code)
            if not projects:
                self._status.setText("Project not found.")
                return
            project = projects[0]
            project_id = str(project.get("id") or "")
            self._project.setText(f"{project_code}  {str(project.get('name') or project_code)}".strip())
            assets = self._client.list_assets(project_id)
            hinted_asset_id = str(self._hint.get("asset_id") or "").strip()
            asset = None
            if hinted_asset_id:
                for a in assets:
                    if str(a.get("id") or "").strip() == hinted_asset_id:
                        asset = a
                        break
            if not asset and assets:
                asset = assets[0]
            if not asset:
                self._status.setText("No assets available.")
                return
            self._asset_ctx = _AssetContext(
                project_id=project_id,
                project_code=project_code,
                asset_id=str(asset.get("id") or ""),
                asset_code=str(asset.get("code") or ""),
                asset_name=str(asset.get("name") or ""),
            )
            self._asset.setText(f"{self._asset_ctx.asset_code}  {self._asset_ctx.asset_name}".strip())
            stage_hint = str(self._hint.get("pipeline_stage") or "").strip().lower()
            if stage_hint:
                idx = self._stage.findData(stage_hint)
                if idx >= 0:
                    self._stage.setCurrentIndex(idx)

            self._user_by_id = {str(u.get("id") or ""): u for u in self._client.list_users(is_active=True)}
            system_username = _resolve_system_username().lower()
            tasks = self._client.list_tasks(project_id=project_id)
            filtered: list[dict[str, Any]] = []
            for t in tasks:
                ids = {
                    str(x).strip()
                    for x in [t.get("assignee_id"), *(t.get("assignees") or []), *(t.get("collaborators") or [])]
                    if str(x).strip()
                }
                include = False
                for uid in ids:
                    u = self._user_by_id.get(uid) or {}
                    uname = str(u.get("username") or "").strip().lower()
                    name = str(u.get("name") or "").strip().lower()
                    if system_username and (system_username == uname or system_username == name):
                        include = True
                        break
                if include:
                    filtered.append(t)
            self._all_tasks = filtered
            self._render_tasks()
            self._status.setText("" if filtered else "No matching tasks assigned to current system user.")
        except Exception as exc:
            self._status.setText(f"Load failed: {type(exc).__name__}: {exc}")

    def _render_tasks(self) -> None:
        QtWidgets, QtCore, _ = get_qt_modules()
        self._task_list.clear()
        query = (self._task_search.text() or "").strip().lower()
        selected_asset_id = self._asset_ctx.asset_id if self._asset_ctx else ""
        for t in self._all_tasks:
            if selected_asset_id and str(t.get("asset_id") or "") not in ("", selected_asset_id):
                continue
            title = str(t.get("title") or "").strip() or str(t.get("type") or "task")
            stage = str((t.get("metadata") or {}).get("stage") or t.get("type") or "").strip()
            status = str(t.get("status") or "").strip()
            line = f"{title} [{stage}] ({status})"
            if query and query not in line.lower():
                continue
            item = QtWidgets.QListWidgetItem(line)
            item.setData(QtCore.Qt.UserRole, str(t.get("id") or ""))
            self._task_list.addItem(item)

    def _publish_clicked(self) -> None:
        QtCore = get_qt_modules()[1]
        if not self._asset_ctx:
            self._status.setText("Missing asset context.")
            return
        item = self._task_list.currentItem()
        if not item:
            self._status.setText("Select a task before publishing.")
            return
        task_id = str(item.data(QtCore.Qt.UserRole) or "").strip()
        stage = str(self._stage.currentData() or "").strip().lower()
        if not self._on_publish:
            self._status.setText("Publish callback not configured in host.")
            return
        result = self._on_publish(
            project=self._asset_ctx.project_code,
            asset=self._asset_ctx.asset_code,
            representation="blend",
            pipeline_stage=stage,
            task_id=task_id,
        )
        if isinstance(result, dict) and not result.get("ok", True):
            self._status.setText(str(result.get("message") or "Publish failed"))
            return
        self._dlg.accept()

    def show_non_modal(self) -> None:
        _show_raised(self._dlg)

    def exec_modal(self) -> int:
        return _exec_dialog(self._dlg)

    def widget(self) -> Any:
        return self._dlg


__all__ = [
    "ZenoNavigatorActionDialog",
    "ZenoVersionSwitcherDialog",
    "ZenoReportIssueDialog",
    "ZenoPublisherDialog",
]
