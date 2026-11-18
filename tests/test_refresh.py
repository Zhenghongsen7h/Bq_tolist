"""GUI regressions using temporary data; run with unittest discovery."""
import importlib.machinery
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from storage import Store

loader = importlib.machinery.SourceFileLoader("tolist_gui", str(PROJECT / "app.pyw"))
spec = importlib.util.spec_from_loader(loader.name, loader)
gui = importlib.util.module_from_spec(spec)
loader.exec_module(gui)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.sqlite3")
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = gui.App(self.root, self.store)
        self.app.go("tasks", day=gui.date(2026, 11, 18))
        self.root.update()

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def add(self, title="parent", parent=None):
        self.store.add(self.app.day.isoformat(), title, parent)
        task_id = self.store.db.execute("SELECT MAX(id) FROM task").fetchone()[0]
        self.app.render()
        self.root.update()
        return task_id

    def click(self, task_id, action):
        row = self.app.task_rows[task_id]
        (row.number if action == "reorder" else row.buttons[action]).invoke()
        self.root.update()

    def test_toggle_preserves_widgets_focus_and_scroll(self):
        for i in range(30):
            self.store.add(self.app.day.isoformat(), f"row {i}")
        self.app.render(); self.root.update()
        canvas = self.app.task_canvas
        canvas.yview_moveto(.4)
        self.root.update()
        before = canvas.yview()
        frames = {key: row.frame for key, row in self.app.task_rows.items()}
        for _ in range(6):
            self.app.task_rows[1].check.invoke(); self.root.update()
        self.assertIs(self.app.task_canvas, canvas)
        self.assertEqual(frames, {key: row.frame for key, row in self.app.task_rows.items()})
        self.assertEqual(canvas.yview(), before)
        self.assertEqual([v.get() for v in self.app.task_counts], ["30", "30", "0"])

    def test_edit_progress_priority_and_fresh_callback_data(self):
        task_id = self.add()
        row = self.app.task_rows[task_id]
        with patch.object(self.app, "edit_value", return_value=("new title", "urgent")):
            self.click(task_id, "edit")
        with patch.object(self.app, "edit_value", return_value=("newer title", "")) as edit:
            self.click(task_id, "edit")
            self.assertEqual(edit.call_args.args[2], "new title")
            self.assertEqual(edit.call_args.kwargs["priority"], "urgent")
        self.assertIs(row, self.app.task_rows[task_id])
        self.assertEqual(row.label.cget("text"), "newer title")
        self.assertFalse(row.light.cget("image"))
        with patch.object(self.app, "edit_value", return_value=("line 1\nline 2", "")):
            self.click(task_id, "describe")
        self.assertEqual(row.buttons["describe"].cget("style"), "HasDescription.TaskAction.TButton")
        with patch.object(self.app, "edit_value", return_value=("", "")):
            self.click(task_id, "describe")
        self.assertEqual(row.buttons["describe"].cget("style"), "TaskAction.TButton")

    def dialog_widgets(self, parent):
        return [child for widget in parent.winfo_children()
                for child in [widget, *self.dialog_widgets(widget)]]

    def progress_dialog(self):
        return next(w for w in self.root.winfo_children()
                    if isinstance(w, tk.Toplevel) and w.title() == "待办进度")

    def wait_for_hover(self):
        ready = tk.BooleanVar(self.root)
        self.root.after(420, lambda: ready.set(True))
        self.root.wait_variable(ready)
        self.root.update()

    def test_deadline_tooltip_hover_parent_child_and_cleanup(self):
        parent = self.add()
        child = self.add("child", parent)
        hint = self.app.deadline_tooltip
        button = self.app.task_rows[parent].buttons["describe"]
        button.event_generate("<Enter>")
        self.wait_for_hover()
        self.assertIsNone(hint.window)
        for task_id, deadline in ((parent, "2026-11-20"), (child, "2026-11-21")):
            self.store.set_progress(task_id, "", deadline)
        self.app.render()
        self.root.update()
        for task_id, deadline in ((parent, "2026-11-20"), (child, "2026-11-21")):
            button = self.app.task_rows[task_id].buttons["describe"]
            button.event_generate("<Enter>")
            self.assertIsNone(hint.window)
            self.wait_for_hover()
            self.assertIsNotNone(hint.window)
            label = hint.window.winfo_children()[0]
            self.assertEqual(label.cget("text"), f"截止日期：{deadline}")
            self.assertEqual(int(label.cget("padx")), 8)
            self.assertEqual(int(label.cget("pady")), 4)
            button.event_generate("<Leave>")
            self.assertIsNone(hint.window)
        button.event_generate("<Enter>")
        button.event_generate("<Leave>")
        self.wait_for_hover()
        self.assertIsNone(hint.window)
        button.event_generate("<Enter>")
        self.wait_for_hover()
        button.event_generate("<MouseWheel>", delta=-120)
        self.assertIsNone(hint.window)
        button.event_generate("<Enter>")
        self.wait_for_hover()
        self.app.go("tasks", day=gui.date(2026, 11, 19))
        self.root.update()
        self.assertIsNone(hint.window)
        self.assertIsNone(hint.job)
        self.app.go("tasks", day=gui.date(2026, 11, 18))
        self.store.set_progress(child, "", "")
        self.app.render()
        self.root.update()
        self.app.task_rows[child].buttons["describe"].event_generate("<Enter>")
        self.wait_for_hover()
        self.assertIsNone(hint.window)
        self.app.task_rows[parent].buttons["describe"].event_generate("<Enter>")
        self.app.go("daily")
        self.wait_for_hover()
        self.assertIsNone(hint.window)
        self.assertIsNone(hint.job)

    def test_calendar_opens_on_today_with_blue_highlight(self):
        class FixedDate(gui.date):
            @classmethod
            def today(cls):
                return cls(2026, 11, 18)

        variable = tk.StringVar(self.root, value="2027-02-23")
        for original in ("2027-02-23", ""):
            variable.set(original)
            observed = []

            def inspect():
                picker = next(w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel))
                try:
                    widgets = self.dialog_widgets(picker)
                    observed.extend(w.cget("text") for w in widgets
                                    if isinstance(w, gui.ttk.Button) and w.cget("style") == "Accent.TButton")
                    self.assertTrue(any(isinstance(w, tk.Label) and w.cget("text") == "2026 年 11 月"
                                        for w in widgets))
                finally:
                    picker.destroy()

            self.root.after(50, inspect)
            with patch.object(gui, "date", FixedDate):
                self.app.pick_deadline(self.root, variable)
            self.assertEqual(observed, ["18"])
            self.assertEqual(variable.get(), original)

    def test_deadline_dialog_calendar_validation_cancel_and_clear(self):
        task_id = self.add()

        def operate():
            dialog = self.progress_dialog()
            widgets = self.dialog_widgets(dialog)
            field = next(w for w in widgets if isinstance(w, gui.ttk.Entry))
            text = next(w for w in widgets if isinstance(w, tk.Text))
            save = next(w for w in widgets if isinstance(w, gui.ttk.Button) and w.cget("text") == "保存")
            field.insert(0, "2026-02-30")
            save.invoke()
            self.assertTrue(dialog.winfo_exists())
            self.assertTrue(any(isinstance(w, tk.Label) and w.cget("textvariable") and
                                "截止日期无效" in str(w.cget("text")) for w in widgets))
            field.delete(0, "end")
            field.insert(0, "2026-11-20")

            def select():
                picker = next(w for w in dialog.winfo_children() if isinstance(w, tk.Toplevel))
                # Move from the current month to November 2026 before choosing.
                today = gui.date.today()
                offset = (2026 - today.year) * 12 + 11 - today.month
                for _ in range(abs(offset)):
                    next(w for w in self.dialog_widgets(picker)
                         if isinstance(w, gui.ttk.Button) and w.cget("text") == ("›" if offset > 0 else "‹")).invoke()
                next(w for w in self.dialog_widgets(picker)
                     if isinstance(w, gui.ttk.Button) and w.cget("text") == "21").invoke()

            self.root.after(50, select)
            next(w for w in widgets if isinstance(w, gui.ttk.Button) and w.winfo_name() == "calendar_button").invoke()
            self.assertEqual(field.get(), "2026-11-21")
            self.assertEqual(dialog.grab_current(), dialog)
            self.assertLess(field.winfo_rooty() + field.winfo_height(), text.winfo_rooty())
            title = next(w for w in widgets if isinstance(w, tk.Label) and w.cget("text") == "待办进度")
            self.assertGreater(field.winfo_rootx(), title.winfo_rootx() + title.winfo_width())
            self.assertLess(field.winfo_rooty(), save.winfo_rooty())
            save.invoke()

        self.root.after(50, operate)
        self.click(task_id, "describe")
        self.assertEqual(self.store.tasks("2026-11-18")[0][5:8], ("", "", "2026-11-21"))
        self.assertEqual(self.app.task_rows[task_id].buttons["describe"].cget("style"), "HasDescription.TaskAction.TButton")

        def cancel():
            dialog = self.progress_dialog()
            widgets = self.dialog_widgets(dialog)
            field = next(w for w in widgets if isinstance(w, gui.ttk.Entry))
            self.assertEqual(field.get(), "2026-11-21")
            next(w for w in widgets if isinstance(w, gui.ttk.Button) and w.cget("text") == "清除").invoke()
            next(w for w in widgets if isinstance(w, gui.ttk.Button) and w.cget("text") == "取消").invoke()

        self.root.after(50, cancel)
        self.click(task_id, "describe")
        self.assertEqual(self.store.tasks("2026-11-18")[0][7], "2026-11-21")

        def clear():
            widgets = self.dialog_widgets(self.progress_dialog())
            next(w for w in widgets if isinstance(w, gui.ttk.Button) and w.cget("text") == "清除").invoke()
            next(w for w in widgets if isinstance(w, gui.ttk.Button) and w.cget("text") == "保存").invoke()

        self.root.after(50, clear)
        self.click(task_id, "describe")
        self.assertEqual(self.store.tasks("2026-11-18")[0][7], "")

    def test_deadline_persistence_and_split_parent_transfer(self):
        parent = self.add()
        child = self.add("pending", parent)
        completed = self.add("completed", parent)
        self.store.toggle(completed)
        self.store.set_progress(parent, "parent progress", "2026-11-23")
        self.store.set_progress(child, "child progress", "2026-11-24")
        with self.assertRaises(ValueError):
            self.store.set_progress(child, "invalid", "2026-02-30")
        self.store.transfer_unfinished("2026-11-18", "2026-11-19")
        reopened = Store(Path(self.temp.name) / "test.sqlite3")
        try:
            self.assertEqual([row[7] for row in reopened.tasks("2026-11-19")], ["2026-11-23", "2026-11-24"])
            self.assertEqual(reopened.tasks("2026-11-18")[0][7], "2026-11-23")
            self.assertEqual(reopened.db.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            reopened.db.close()

    def test_add_child_reorder_delete_and_empty_state(self):
        first = self.add("first")
        frame = self.app.task_rows[first].frame
        second = self.add("second")
        with patch.object(self.app, "edit_value", return_value="child"):
            self.click(first, "add_subtask")
        child = max(self.app.task_rows)
        self.assertEqual(self.app.task_rows[child].data[4], "1.1")
        with patch.object(self.app, "edit_value", return_value="2"):
            self.click(first, "reorder")
        self.assertEqual(list(self.app.task_rows), [second, first, child])
        self.assertEqual(self.app.task_rows[child].data[4], "2.1")
        self.assertIs(self.app.task_rows[first].frame, frame)
        with patch.object(gui.messagebox, "askyesno", return_value=True):
            self.click(first, "delete")
            self.assertNotIn(child, self.app.task_rows)
            self.click(second, "delete")
        self.assertFalse(self.app.task_rows)
        self.assertEqual([v.get() for v in self.app.task_counts], ["0", "0", "0"])

    def test_date_reuse_drafts_and_navigation(self):
        task_id = self.add()
        frame = self.app.task_rows[task_id].frame
        canvas = self.app.task_canvas
        self.app.task_drafts["2026-11-18"].set("unsent draft")
        self.app.go("tasks", day=gui.date(2026, 11, 19)); self.root.update()
        self.assertFalse(self.app.task_rows)
        self.app.go("tasks", day=gui.date(2026, 11, 18)); self.root.update()
        self.assertIs(self.app.task_canvas, canvas)
        self.assertIs(self.app.task_rows[task_id].frame, frame)
        self.assertEqual(self.app.task_drafts["2026-11-18"].get(), "unsent draft")
        dates = self.app.task_date_button.master
        next_button = next(w for w in dates.winfo_children() if w.cget("text") == "›")
        next_button.invoke(); self.root.update()
        next_button.invoke(); self.root.update()
        self.assertEqual(self.app.day.isoformat(), "2026-11-20")
        self.assertIn("2026-11-20", self.app.task_date_button.cget("text"))

    def test_transfer_uses_target_day_and_latest_actions(self):
        parent = self.add()
        child = self.add("child", parent)
        canvas = self.app.task_canvas
        self.app.transfer_tasks(gui.date(2026, 11, 19)); self.root.update()
        self.assertIs(self.app.task_canvas, canvas)
        self.assertEqual(self.store.tasks("2026-11-18"), [])
        self.assertEqual(self.app.task_rows[child].data[4], "1.1")
        with patch.object(self.app, "edit_value", return_value="second child"):
            self.click(parent, "add_subtask")
        self.assertEqual(len(self.store.tasks("2026-11-19")), 3)
        self.assertEqual(self.store.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_write_failure_restores_checkbox(self):
        task_id = self.add()
        with patch.object(self.store, "toggle", side_effect=sqlite3.OperationalError("test")), \
                patch.object(gui.messagebox, "showerror") as error:
            self.app.task_rows[task_id].check.invoke(); self.root.update()
        self.assertFalse(self.app.task_rows[task_id].state.get())
        error.assert_called_once()

    def test_page_switch_save_and_toolbar_sizes(self):
        self.add()
        for width in (780, 1040, 1300, 780, 1040):
            self.root.geometry(f"{width}x700"); self.root.update()
            self.assertGreater(self.app.task_canvas.winfo_height(), 300)
        self.app.go("daily"); self.root.update()
        self.app.editors[0][0].insert("1.0", "daily test")
        self.app.go("weekly"); self.root.update()
        self.assertEqual(self.store.note("daily", "2026-11-18"), "daily test")
        self.app.go("draft"); self.root.update()
        self.app.go("tasks"); self.root.update()
        self.assertEqual(len(self.app.task_rows), 1)


if __name__ == "__main__":
    unittest.main()
