"""Review Notes window in the Zeno theme, for any DCC.

The dialog talks to the API itself (on worker threads) and to the DCC through a small host
object, so Blender, Maya or Houdini only implement the parts that touch the scene:

- ``current_frame() -> int``
- ``go_to(frame: int, frame_end: int | None) -> None``
- ``pending(task_id) -> list[str]`` / ``set_pending(task_id, note_ids) -> None``: notes the
  artist ticked as fixed, stored with the scene and sent with the next submission
- ``on_notes(task_id, notes) -> None``: the loaded notes, e.g. for viewport drawings
- ``default_task_id(task_ids) -> str``
- ``submit_review(task_id) -> None``: open the DCC's Submit for Review
- optional: ``on_tasks(tasks)``, ``drawings_visible() -> bool``, ``set_drawings_visible(bool)``,
  ``show_markers(notes)``, ``can_submit() -> bool``
"""
from __future__ import annotations

from typing import Any

from zeno_client.client import ZenoClient
from zeno_client.review import adjacent_note_frame, frames_label, notes_at_frame, notes_summary, sort_notes
from zeno_ui.qt_compat import get_qt_modules
from zeno_ui.qt_workers import make_async_runner
from zeno_ui.stylesheet import apply_stylesheet, theme_tokens

AUTO_REFRESH_MS = 120_000
FRAME_POLL_MS = 250

_STATUS_TEXT = {"open": "● Open", "fixed": "◐ Waiting for the reviewer", "verified": "✓ Verified"}


def _qss(t: dict[str, str]) -> str:
    return f"""
QFrame#noteCard {{
    background: {t['color.card']};
    border: 1px solid {t['color.border']};
    border-radius: 6px;
}}
QFrame#noteCard[here="true"] {{
    border: 1px solid {t['color.primary']};
}}
QFrame#noteCard[ticked="true"] {{
    background: {t['color.card-hover']};
}}
QPushButton#frameChip {{
    font-weight: bold;
    padding: 3px 10px;
    border-radius: 10px;
    color: {t['color.primary']};
    border-color: {t['color.primary']};
    background: transparent;
}}
QPushButton#frameChip:hover {{
    background: {t['color.selection-bg']};
}}
QFrame#noteCard[here="true"] QPushButton#frameChip {{
    background: {t['color.primary']};
    color: {t['color.primary-foreground']};
}}
QLabel#statusOpen {{ color: {t['color.danger']}; font-size: 9pt; }}
QLabel#statusFixed {{ color: {t['color.warning']}; font-size: 9pt; }}
QLabel#statusVerified {{ color: {t['color.success']}; font-size: 9pt; }}
QLabel#reply {{ color: {t['color.muted']}; font-size: 9pt; }}
QLabel#noteBody {{ color: {t['color.foreground']}; }}
QLabel#drawTag {{
    color: {t['color.info']};
    font-size: 8pt;
    border: 1px solid {t['color.info']};
    border-radius: 8px;
    padding: 1px 6px;
}}
QLabel#summary {{ color: {t['color.foreground']}; font-size: 9pt; }}
QLabel#frameNow {{ color: {t['color.muted']}; font-size: 9pt; padding: 0 4px; }}
QPushButton#stepButton {{ padding: 3px 10px; font-weight: bold; }}
QCheckBox#tickFix:checked {{ color: {t['color.primary']}; }}
QScrollArea {{ border: none; background: transparent; }}
QWidget#cards {{ background: transparent; }}
"""


class ZenoReviewNotesDialog:
    """Tool window: the selected task's open review notes, jump to frames, tick fixes."""

    def __init__(
        self,
        *,
        client: ZenoClient,
        host: Any,
        parent: Any | None = None,
        stay_on_top: bool = False,
    ) -> None:
        QtWidgets, QtCore, _ = get_qt_modules()
        self._client = client
        self._host = host
        self._tasks: list[dict[str, Any]] = []
        self._task_id = ""
        self._notes: list[dict[str, Any]] = []
        self._frame: int | None = None
        self._cards: list[tuple[Any, dict[str, Any]]] = []
        self._seq = 0
        self._notice = ""  # shown once the next load finishes (e.g. "2 notes marked fixed")

        self._dlg = QtWidgets.QDialog(parent)
        self._dlg.setWindowTitle("Zeno · Review Notes")
        self._dlg.setMinimumSize(440, 560)
        self._dlg.resize(460, 720)
        self._dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        if stay_on_top:
            self._dlg.setWindowFlags(self._dlg.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        apply_stylesheet(self._dlg, _qss(theme_tokens()))

        root = QtWidgets.QHBoxLayout(self._dlg)
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(8)
        root.addLayout(col, 1)

        title = QtWidgets.QLabel("Review Notes")
        title.setObjectName("dialogTitle")
        col.addWidget(title)
        sub = QtWidgets.QLabel("Tick what you fixed. It goes to the reviewer with your next submission.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        col.addWidget(sub)

        row = QtWidgets.QHBoxLayout()
        self._task = QtWidgets.QComboBox(self._dlg)
        self._task.setMinimumWidth(220)
        self._refresh = QtWidgets.QPushButton("Refresh")
        row.addWidget(self._task, 1)
        row.addWidget(self._refresh)
        col.addLayout(row)

        self._summary = QtWidgets.QLabel("")
        self._summary.setObjectName("summary")
        col.addWidget(self._summary)

        nav = QtWidgets.QHBoxLayout()
        self._prev = QtWidgets.QPushButton("‹")
        self._next = QtWidgets.QPushButton("›")
        for b, tip in ((self._prev, "Previous note"), (self._next, "Next note")):
            b.setObjectName("stepButton")
            b.setToolTip(tip)
        self._frame_now = QtWidgets.QLabel("")
        self._frame_now.setObjectName("frameNow")
        self._drawings = QtWidgets.QCheckBox("Drawings in viewport")
        nav.addWidget(self._prev)
        nav.addWidget(self._frame_now)
        nav.addWidget(self._next)
        nav.addStretch(1)
        nav.addWidget(self._drawings)
        col.addLayout(nav)

        self._scroll = QtWidgets.QScrollArea(self._dlg)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._cards_host = QtWidgets.QWidget()
        self._cards_host.setObjectName("cards")
        self._cards_layout = QtWidgets.QVBoxLayout(self._cards_host)
        self._cards_layout.setContentsMargins(0, 0, 4, 0)
        self._cards_layout.setSpacing(8)
        self._scroll.setWidget(self._cards_host)
        col.addWidget(self._scroll, 1)

        self._status = QtWidgets.QLabel("")
        self._status.setObjectName("muted")
        self._status.setWordWrap(True)
        col.addWidget(self._status)

        foot = QtWidgets.QHBoxLayout()
        self._markers = QtWidgets.QPushButton("Timeline markers")
        self._send_now = QtWidgets.QPushButton("Mark fixed now")
        self._send_now.setToolTip(
            "Mark the ticked notes fixed against the latest revision, without submitting. "
            "Use when the fix reaches review another way (a farm render, for example)."
        )
        self._submit = QtWidgets.QPushButton("Submit for Review…")
        self._submit.setProperty("primary", True)
        self._submit.setMinimumHeight(34)
        foot.addWidget(self._markers)
        foot.addStretch(1)
        foot.addWidget(self._send_now)
        col.addLayout(foot)
        col.addWidget(self._submit)

        self._runner = make_async_runner()
        self._runner.finished.connect(self._on_loaded)
        self._runner.failed.connect(self._on_failed)

        self._task.currentIndexChanged.connect(self._on_task_changed)
        self._refresh.clicked.connect(lambda: self._load(with_tasks=True))
        self._prev.clicked.connect(lambda: self._step(-1))
        self._next.clicked.connect(lambda: self._step(1))
        self._markers.clicked.connect(self._on_markers)
        self._send_now.clicked.connect(self._on_send_now)
        self._submit.clicked.connect(self._on_submit)
        if hasattr(host, "drawings_visible"):
            self._drawings.setChecked(bool(host.drawings_visible()))
            self._drawings.toggled.connect(lambda on: host.set_drawings_visible(bool(on)))
        else:
            self._drawings.hide()
        if not hasattr(host, "show_markers"):
            self._markers.hide()

        self._frame_timer = QtCore.QTimer(self._dlg)
        self._frame_timer.setInterval(FRAME_POLL_MS)
        self._frame_timer.timeout.connect(self._poll_frame)
        self._frame_timer.start()
        self._refresh_timer = QtCore.QTimer(self._dlg)
        self._refresh_timer.setInterval(AUTO_REFRESH_MS)
        self._refresh_timer.timeout.connect(lambda: self._load(with_tasks=False))
        self._refresh_timer.start()

        self._render()
        self._load(with_tasks=True)

    # --- data -------------------------------------------------------------------------

    def _load(self, *, with_tasks: bool) -> None:
        self._seq += 1
        seq, client, task_id = self._seq, self._client, self._task_id
        self._status.setText("Loading…")
        self._refresh.setEnabled(False)

        def call() -> dict[str, Any]:
            out: dict[str, Any] = {"seq": seq, "task_id": task_id}
            if with_tasks:
                out["tasks"] = client.list_my_tasks()
                if task_id not in [str(t.get("id")) for t in out["tasks"]]:
                    return out  # the main thread picks a task, then loads its notes
            if task_id:
                out["notes"] = client.list_review_notes(task_id, status="unresolved")
            return out

        self._runner.submit(call)

    def _on_loaded(self, out: dict[str, Any]) -> None:
        if out.get("seq") != self._seq:
            return
        self._refresh.setEnabled(True)
        self._status.setText(self._notice)
        self._notice = ""
        if "tasks" in out:
            self._tasks = list(out["tasks"])
            if hasattr(self._host, "on_tasks"):
                self._host.on_tasks(self._tasks)
            ids = [str(t.get("id")) for t in self._tasks]
            if out["task_id"] not in ids:
                out["task_id"] = self._host.default_task_id(ids) or (ids[0] if ids else "")
                out["needs_notes"] = bool(out["task_id"])
            self._task.blockSignals(True)
            self._task.clear()
            for t in self._tasks:
                label = str(t.get("title") or t.get("type") or t.get("id"))
                status = str(t.get("status") or "").replace("_", " ")
                self._task.addItem(f"{label}  ·  {status}" if status else label, str(t.get("id")))
            idx = self._task.findData(out["task_id"])
            if idx >= 0:
                self._task.setCurrentIndex(idx)
            self._task.blockSignals(False)
            if not self._tasks:
                self._status.setText("No tasks are assigned to you.")
        self._task_id = out["task_id"]
        if out.get("needs_notes"):
            self._render()
            self._load(with_tasks=False)
            return
        self._notes = sort_notes(out.get("notes") or [])
        try:
            self._host.on_notes(self._task_id, self._notes)
        except Exception:
            pass
        self._render()

    def _on_failed(self, exc: Any) -> None:
        self._refresh.setEnabled(True)
        self._status.setText(f"Could not load notes: {exc}")

    def _on_task_changed(self, _index: int) -> None:
        self._task_id = str(self._task.currentData() or "")
        self._notes = []
        self._render()
        self._load(with_tasks=False)

    # --- rendering ----------------------------------------------------------------------

    def _pending(self) -> list[str]:
        return list(self._host.pending(self._task_id)) if self._task_id else []

    def _render(self) -> None:
        QtWidgets, QtCore, _ = get_qt_modules()
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            old = item.widget()
            if old is not None:
                # Detach now: deleteLater alone leaves the old card painted until the next
                # event-loop pass, on top of its replacement.
                old.hide()
                old.setParent(None)
                old.deleteLater()
        self._cards = []
        pending = set(self._pending())
        self._summary.setText(notes_summary(self._notes, len(pending)) if self._task_id else "")
        if self._task_id and not self._notes and not self._status.text():
            empty = QtWidgets.QLabel("No open notes. Nice work.")
            empty.setObjectName("muted")
            empty.setAlignment(QtCore.Qt.AlignCenter)
            empty.setMinimumHeight(120)
            self._cards_layout.addWidget(empty)
            empty.show()
        for note in self._notes:
            card = self._card(note, ticked=note["id"] in pending)
            self._cards.append((card, note))
            self._cards_layout.addWidget(card)
            card.show()  # layouts only show new children on the next event pass
        self._cards_layout.addStretch(1)
        self._cards_layout.activate()  # settle now so a redraw never shows a half-laid-out list
        self._send_now.setEnabled(bool(pending))
        can_submit = getattr(self._host, "can_submit", lambda: True)()
        self._submit.setEnabled(bool(self._task_id) and can_submit)
        self._submit.setText(
            f"Submit for Review · {len(pending)} fix{'es' if len(pending) != 1 else ''} included" if pending else "Submit for Review…"
        )
        has_frames = any(n.get("frame") is not None for n in self._notes)
        self._prev.setEnabled(has_frames)
        self._next.setEnabled(has_frames)
        self._markers.setEnabled(has_frames)
        self._frame = None
        self._poll_frame()

    def _card(self, note: dict[str, Any], *, ticked: bool) -> Any:
        QtWidgets, QtCore, _ = get_qt_modules()
        card = QtWidgets.QFrame()
        card.setObjectName("noteCard")
        card.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        card.setProperty("ticked", ticked)
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        head = QtWidgets.QHBoxLayout()
        chip = QtWidgets.QPushButton(frames_label(note))
        chip.setObjectName("frameChip")
        chip.setCursor(QtCore.Qt.PointingHandCursor)
        if note.get("frame") is None:
            chip.setEnabled(False)
        else:
            chip.setToolTip("Go to this frame" + (" and loop the range" if note.get("frame_end") not in (None, note.get("frame")) else ""))
            chip.clicked.connect(lambda _=False, n=note: self._host.go_to(int(n["frame"]), n.get("frame_end")))
        head.addWidget(chip)
        status = str(note.get("status") or "")
        state = QtWidgets.QLabel(_STATUS_TEXT.get(status, status))
        state.setObjectName({"open": "statusOpen", "fixed": "statusFixed", "verified": "statusVerified"}.get(status, "muted"))
        head.addWidget(state)
        head.addStretch(1)
        if (note.get("drawing") or {}).get("shapes"):
            tag = QtWidgets.QLabel("✎ drawing")
            tag.setObjectName("drawTag")
            head.addWidget(tag)
        who = str(note.get("author_name") or "Someone")
        if note.get("raised_in"):
            who += f" · {note['raised_in']}"
        author = QtWidgets.QLabel(who)
        author.setObjectName("muted")
        head.addWidget(author)
        lay.addLayout(head)

        body = QtWidgets.QLabel(" ".join(str(note.get("body") or "(drawing only)").split()))
        body.setObjectName("noteBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(body)

        for reply in (note.get("replies") or [])[-3:]:
            if not reply.get("body") or reply.get("kind") == "status":
                continue
            r = QtWidgets.QLabel(f"↳ {reply.get('author_name') or 'Someone'}: {' '.join(reply['body'].split())}")
            r.setObjectName("reply")
            r.setWordWrap(True)
            lay.addWidget(r)

        if status == "open":
            tick = QtWidgets.QCheckBox("Fixed: goes with my next submission" if ticked else "Tick when fixed")
            tick.setObjectName("tickFix")
            tick.setChecked(ticked)
            tick.toggled.connect(lambda on, nid=note["id"]: self._set_ticked(nid, on))
            lay.addWidget(tick)
        elif status == "fixed":
            where = f"Fixed in {note['fixed_in']}" if note.get("fixed_in") else "Fixed"
            done = QtWidgets.QLabel(f"{where}. The reviewer checks it on that revision.")
            done.setObjectName("muted")
            lay.addWidget(done)
        return card

    def _set_ticked(self, note_id: str, on: bool) -> None:
        ids = [n for n in self._pending() if n != note_id]
        if on:
            ids.append(note_id)
        self._host.set_pending(self._task_id, ids)
        self._render()

    def _poll_frame(self) -> None:
        try:
            frame = int(self._host.current_frame())
        except Exception:
            return
        if frame == self._frame:
            return
        self._frame = frame
        here = notes_at_frame(self._notes, frame)
        self._frame_now.setText(f"Frame {frame}" + (f" · {len(here)} here" if here else ""))
        here_ids = {n["id"] for n in here}
        first = None
        for card, note in self._cards:
            flag = note["id"] in here_ids
            if bool(card.property("here")) != flag:
                card.setProperty("here", flag)
                card.style().unpolish(card)
                card.style().polish(card)
                for child in card.findChildren(get_qt_modules()[0].QPushButton):
                    child.style().unpolish(child)
                    child.style().polish(child)
            if flag and first is None:
                first = card
        if first is not None:
            self._scroll.ensureWidgetVisible(first, 0, 24)

    # --- actions ------------------------------------------------------------------------

    def _step(self, direction: int) -> None:
        frame = adjacent_note_frame(self._notes, int(self._host.current_frame()), direction)
        if frame is None:
            self._status.setText("No more notes that way.")
            return
        self._status.setText("")
        self._host.go_to(frame, None)
        self._poll_frame()

    def _on_markers(self) -> None:
        self._host.show_markers(self._notes)
        self._status.setText("Notes placed on the timeline as markers.")

    def _on_submit(self) -> None:
        self._host.submit_review(self._task_id)

    def _on_send_now(self) -> None:
        ids, client, task_id = self._pending(), self._client, self._task_id
        self._send_now.setEnabled(False)
        self._status.setText(f"Marking {len(ids)} note(s) fixed…")
        runner = make_async_runner()
        self._fix_runner = runner  # keep alive until it reports

        def call() -> tuple[list[str], list[str]]:
            sent, failed = [], []
            for nid in ids:
                try:
                    client.update_review_note(nid, status="fixed", comment="Fixed (marked from the DCC)")
                    sent.append(nid)
                except Exception as exc:  # noqa: BLE001
                    failed.append(f"{exc}")
            return sent, failed

        def done(result: tuple[list[str], list[str]]) -> None:
            sent, failed = result
            self._host.set_pending(task_id, [n for n in self._host.pending(task_id) if n not in sent])
            self._notice = f"{len(sent)} note(s) marked fixed." + (
                f" {len(failed)} could not be updated and stay ticked: {failed[0]}" if failed else ""
            )
            self._load(with_tasks=False)

        runner.finished.connect(done)
        runner.failed.connect(self._on_failed)
        runner.submit(call)

    # --- host-facing --------------------------------------------------------------------

    def reload(self) -> None:
        """Refetch notes, e.g. after the DCC submitted a revision."""
        self._load(with_tasks=False)

    def task_id(self) -> str:
        return self._task_id

    def show_non_modal(self) -> None:
        self._dlg.show()
        try:
            self._dlg.raise_()
            self._dlg.activateWindow()
        except Exception:
            pass

    def widget(self) -> Any:
        return self._dlg


__all__ = ["ZenoReviewNotesDialog"]
