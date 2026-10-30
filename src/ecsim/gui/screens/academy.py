from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QRadioButton, QSplitter, QStackedWidget,
                               QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ecsim.app.services import AppContext
from ecsim.learning.content import Exercise
from ecsim.learning.engine import PolicyError
from ecsim.gui.widgets import CodeEditor, heading, mono_font

ROLE = Qt.UserRole


class LessonView(QWidget):
    def __init__(self, on_complete) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        lay.addWidget(self.browser, 1)
        self.done_btn = QPushButton("Mark lesson as read")
        self.done_btn.clicked.connect(lambda: on_complete(self.lesson_id))
        lay.addWidget(self.done_btn, 0, Qt.AlignRight)
        self.lesson_id: str | None = None

    def show_lesson(self, lesson, done: bool) -> None:
        self.lesson_id = lesson.id
        self.browser.setMarkdown(lesson.body)
        self.done_btn.setEnabled(not done)
        self.done_btn.setText("Read ✓" if done else "Mark lesson as read")


class ExerciseView(QWidget):
    def __init__(self, ctx: AppContext, learner_id: int, on_change) -> None:
        super().__init__()
        self.ctx, self.learner_id, self.on_change = ctx, learner_id, on_change
        self.ex: Exercise | None = None
        lay = QVBoxLayout(self)
        self.title = heading("", "h2")
        self.meta = QLabel(); self.meta.setObjectName("muted")
        lay.addWidget(self.title); lay.addWidget(self.meta)

        split = QSplitter(Qt.Vertical)
        self.prompt = QTextBrowser()
        split.addWidget(self.prompt)

        self.input_stack = QStackedWidget()
        self.numeric = QLineEdit(); self.numeric.setPlaceholderText("Your answer (e.g. 0.95 or 0x1F)")
        self.numeric.returnPressed.connect(self.submit)
        num_wrap = QWidget(); nl = QVBoxLayout(num_wrap); nl.addWidget(self.numeric); nl.addStretch(1)
        self.choice_wrap = QWidget(); self.choice_lay = QVBoxLayout(self.choice_wrap)
        self.choice_group = QButtonGroup(self)
        self.code = CodeEditor()
        for w in (num_wrap, self.choice_wrap, self.code):
            self.input_stack.addWidget(w)
        split.addWidget(self.input_stack)

        self.feedback = QPlainTextEdit(); self.feedback.setReadOnly(True); self.feedback.setFont(mono_font())
        self.feedback.setPlaceholderText("Grader feedback appears here.")
        split.addWidget(self.feedback)
        split.setSizes([260, 260, 160])
        lay.addWidget(split, 1)

        self.hints = QLabel(); self.hints.setWordWrap(True); self.hints.setTextFormat(Qt.PlainText)
        lay.addWidget(self.hints)

        bar = QHBoxLayout()
        self.ai_box = QCheckBox("I used AI/outside help on this attempt")
        bar.addWidget(self.ai_box)
        bar.addStretch(1)
        self.status = QLabel(); bar.addWidget(self.status)
        self.hint_btn = QPushButton("Next hint"); self.hint_btn.setObjectName("secondary")
        self.sol_btn = QPushButton("Show solution"); self.sol_btn.setObjectName("secondary")
        self.reset_btn = QPushButton("Reset starter"); self.reset_btn.setObjectName("secondary")
        self.slx_btn = QPushButton("Use Simulink model…"); self.slx_btn.setObjectName("secondary")
        self.slx_btn.setToolTip("Grade a .slx/.mdl with real Simulink (needs MATLAB + MATLAB Engine for Python)")
        self.slx_btn.clicked.connect(self._choose_slx)
        self.studio_btn = QPushButton("Open in Model Studio"); self.studio_btn.setObjectName("secondary")
        self.studio_btn.clicked.connect(self._to_studio)
        self.open_in_studio = None          # set by the main window
        self.submit_btn = QPushButton("Submit")
        self.hint_btn.clicked.connect(self.next_hint)
        self.sol_btn.clicked.connect(lambda: self.request_solution(confirm=True))
        self.reset_btn.clicked.connect(self._reset_code)
        self.submit_btn.clicked.connect(self.submit)
        for b in (self.studio_btn, self.slx_btn, self.reset_btn, self.hint_btn, self.sol_btn, self.submit_btn):
            bar.addWidget(b)
        lay.addLayout(bar)

    # -- display -------------------------------------------------------------
    def show_exercise(self, ex: Exercise) -> None:
        self.ex = ex
        self.title.setText(f"{ex.id} — {ex.title}")
        self.meta.setText(f"{ex.kind} · competency {ex.competency} · {ex.level} · {ex.points} pts"
                          + (" · practical" if ex.practical else ""))
        self.prompt.setMarkdown(ex.prompt)
        self.feedback.clear()
        self.ai_box.setChecked(False)
        last = self.ctx.learning.last_submission(self.learner_id, ex.id)
        if ex.kind == "numeric":
            self.input_stack.setCurrentIndex(0)
            self.numeric.setText(last or "")
        elif ex.kind == "choice":
            self.input_stack.setCurrentIndex(1)
            for b in self.choice_group.buttons():
                self.choice_group.removeButton(b); b.deleteLater()
            while self.choice_lay.count():
                item = self.choice_lay.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            for i, opt in enumerate(ex.spec["options"]):
                rb = QRadioButton(opt)
                self.choice_group.addButton(rb, i)
                self.choice_lay.addWidget(rb)
                if last is not None and last.strip() == str(i):
                    rb.setChecked(True)
            self.choice_lay.addStretch(1)
        else:
            self.input_stack.setCurrentIndex(2)
            self.code.setPlainText(last if last is not None else ex.spec["starter"])
        self.reset_btn.setVisible(ex.kind in ("c_function", "ecu_app", "model_mil"))
        self.slx_btn.setVisible(ex.kind == "model_mil")
        self.studio_btn.setVisible(ex.kind == "model_mil")
        self._refresh_state()

    def _refresh_state(self) -> None:
        st = self.ctx.learning.state(self.learner_id, self.ex.id)
        hints = self.ctx.learning.revealed_hints(self.learner_id, self.ex.id)
        self.hints.setText("\n".join(f"Hint {i}: {h}" for i, h in enumerate(hints, 1)))
        self.hint_btn.setEnabled(st.next_hint_allowed)
        self.hint_btn.setToolTip(st.next_hint_blocker or "")
        self.hint_btn.setText(f"Next hint ({st.hints_revealed}/{st.hints_total})")
        badge = "★ passed independently" if st.passed_independently else ("✓ passed (assisted)" if st.passed else "")
        self.status.setText(f"attempts: {st.attempts}   {badge}")

    def _reset_code(self) -> None:
        if self.ex and self.ex.kind in ("c_function", "ecu_app", "model_mil"):
            self.code.setPlainText(self.ex.spec["starter"])

    def _to_studio(self) -> None:
        text = self.code.toPlainText().strip()
        if self.open_in_studio and text.startswith("{"):
            self.open_in_studio(text)
        elif not text.startswith("{"):
            self.feedback.setPlainText("The editor holds a Simulink file path; reset to the starter to edit an ECSim model.")

    def _choose_slx(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Simulink model", "", "Simulink models (*.slx *.mdl)")
        if path:
            self.code.setPlainText(path)

    def current_submission(self) -> str:
        if self.ex.kind == "numeric":
            return self.numeric.text()
        if self.ex.kind == "choice":
            return str(self.choice_group.checkedId()) if self.choice_group.checkedId() >= 0 else ""
        return self.code.toPlainText()

    # -- actions -------------------------------------------------------------
    def submit(self) -> None:
        if not self.ex:
            return
        sub = self.current_submission()
        if not sub.strip():
            self.feedback.setPlainText("Nothing to submit.")
            return
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            rec = self.ctx.learning.submit(self.learner_id, self.ex.id, sub, ai_assisted=self.ai_box.isChecked())
        finally:
            QGuiApplication.restoreOverrideCursor()
        head = "PASS" if rec.passed else "FAIL"
        if rec.id > 0 and rec.passed:
            head += "  —  " + ("counts as independent evidence" if rec.independent
                               else "recorded, but not independent evidence (hints/solution/AI help)")
        elif rec.id > 0:
            head += "  —  attempt recorded; use the feedback, then try again"
        self.feedback.setPlainText(f"{head}\n\n{rec.feedback}")
        self._refresh_state()
        self.on_change()

    def next_hint(self) -> None:
        try:
            self.ctx.learning.reveal_hint(self.learner_id, self.ex.id)
        except PolicyError as exc:
            self.feedback.setPlainText(str(exc))
        self._refresh_state()

    def request_solution(self, confirm: bool = False, force: bool = False) -> str | None:
        st = self.ctx.learning.state(self.learner_id, self.ex.id)
        if not st.solution_allowed and not force:
            if not confirm:
                self.feedback.setPlainText("Submit at least one attempt before viewing the solution.")
                return None
            ans = QMessageBox.question(
                self, "View solution before attempting?",
                "You have not attempted this exercise yet.\n\nViewing the solution now means any later pass "
                "will not count as independent evidence. View it anyway?",
            )
            if ans != QMessageBox.Yes:
                return None
            force = True
        try:
            text = self.ctx.learning.reveal_solution(self.learner_id, self.ex.id, force=force)
        except PolicyError as exc:
            self.feedback.setPlainText(str(exc))
            return None
        self.prompt.setMarkdown(self.ex.prompt + "\n\n---\n\n## Solution\n\n" + text)
        self._refresh_state()
        return text


class AcademyScreen(QWidget):
    def __init__(self, ctx: AppContext, learner_id: int, on_change=lambda: None) -> None:
        super().__init__()
        self.ctx, self.learner_id, self.on_change = ctx, learner_id, on_change
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(heading("Learning Academy"))
        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._selected)
        split.addWidget(self.tree)
        self.stack = QStackedWidget()
        self.empty = QLabel("Select a lesson or exercise."); self.empty.setAlignment(Qt.AlignCenter)
        self.lesson_view = LessonView(self._complete_lesson)
        self.exercise_view = ExerciseView(ctx, learner_id, self._changed)
        for w in (self.empty, self.lesson_view, self.exercise_view):
            self.stack.addWidget(w)
        split.addWidget(self.stack)
        split.setSizes([360, 840])
        lay.addWidget(split, 1)
        self._build_tree()

    def _build_tree(self) -> None:
        self.tree.clear()
        depts = {d["code"]: d for d in self.ctx.catalog.departments}
        by_dept: dict[str, QTreeWidgetItem] = {}
        for m in self.ctx.catalog.modules:
            if m.department not in by_dept:
                d = depts[m.department]
                it = QTreeWidgetItem([f"Dept {d['code']} — {d['title']}"])
                self.tree.addTopLevelItem(it)
                by_dept[m.department] = it
            mi = QTreeWidgetItem([f"{m.id}  {m.title}"])
            by_dept[m.department].addChild(mi)
            for l in m.lessons:
                li = QTreeWidgetItem([f"📖 {l.title}"]); li.setData(0, ROLE, ("lesson", l.id))
                mi.addChild(li)
            for e in m.exercises:
                ei = QTreeWidgetItem([f"🛠 {e.title}"]); ei.setData(0, ROLE, ("exercise", e.id))
                mi.addChild(ei)
        planned = [d for d in self.ctx.catalog.departments if d["code"] not in by_dept]
        if planned:
            root = QTreeWidgetItem(["Departments arriving in later phases"])
            for d in planned:
                root.addChild(QTreeWidgetItem([f"Dept {d['code']} — {d['title']} (Phase {d['phase']})"]))
            self.tree.addTopLevelItem(root)
        self.tree.expandToDepth(1)
        self._decorate()

    def _decorate(self) -> None:
        done = self.ctx.learning.completed_lessons(self.learner_id)
        it = self.tree.invisibleRootItem()
        stack = [it]
        while stack:
            node = stack.pop()
            for i in range(node.childCount()):
                ch = node.child(i)
                stack.append(ch)
                data = ch.data(0, ROLE)
                if not data:
                    continue
                kind, ident = data
                if kind == "lesson":
                    l = self.ctx.catalog.lesson(ident)
                    ch.setText(0, f"{'✅' if ident in done else '📖'} {l.title}")
                else:
                    e = self.ctx.catalog.exercise(ident)
                    st = self.ctx.learning.state(self.learner_id, ident)
                    mark = "★" if st.passed_independently else ("✓" if st.passed else "🛠")
                    ch.setText(0, f"{mark} {e.title}")

    def select(self, ident: str) -> None:
        """Programmatic selection (used by tests and deep links)."""
        stack = [self.tree.invisibleRootItem()]
        while stack:
            node = stack.pop()
            for i in range(node.childCount()):
                ch = node.child(i)
                d = ch.data(0, ROLE)
                if d and d[1] == ident:
                    self.tree.setCurrentItem(ch)
                    return
                stack.append(ch)
        raise KeyError(ident)

    def _selected(self, item, _prev) -> None:
        data = item.data(0, ROLE) if item else None
        if not data:
            self.stack.setCurrentWidget(self.empty)
            return
        kind, ident = data
        if kind == "lesson":
            self.lesson_view.show_lesson(self.ctx.catalog.lesson(ident),
                                         ident in self.ctx.learning.completed_lessons(self.learner_id))
            self.stack.setCurrentWidget(self.lesson_view)
        else:
            self.exercise_view.show_exercise(self.ctx.catalog.exercise(ident))
            self.stack.setCurrentWidget(self.exercise_view)

    def _complete_lesson(self, lesson_id: str | None) -> None:
        if lesson_id:
            self.ctx.learning.complete_lesson(self.learner_id, lesson_id)
            self.lesson_view.show_lesson(self.ctx.catalog.lesson(lesson_id), True)
            self._changed()

    def _changed(self) -> None:
        self._decorate()
        self.on_change()

    def refresh(self) -> None:
        self._decorate()
