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
        with patch.object(self.app, "edit_value", return_value="line 1\nline 2"):
            self.click(task_id, "describe")
        self.assertEqual(row.buttons["describe"].cget("style"), "HasDescription.TaskAction.TButton")
        with patch.object(self.app, "edit_value", return_value=""):
            self.click(task_id, "describe")
        self.assertEqual(row.buttons["describe"].cget("style"), "TaskAction.TButton")

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
