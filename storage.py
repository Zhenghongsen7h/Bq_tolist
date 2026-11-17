import hashlib
import sqlite3
from datetime import date, timedelta
from pathlib import Path


def monday(day):
    return day - timedelta(days=day.weekday())


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=1)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS task (
                id INTEGER PRIMARY KEY, day TEXT NOT NULL,
                title TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS task_by_day ON task(day, id);
            CREATE TABLE IF NOT EXISTS note (
                kind TEXT NOT NULL, day TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY (kind, day)
            );
            CREATE TABLE IF NOT EXISTS weekly_section (
                day TEXT PRIMARY KEY NOT NULL, body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checklist_copy (
                day TEXT NOT NULL, digest TEXT NOT NULL,
                PRIMARY KEY (day, digest)
            );
        """)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(task)")}
            if "parent_id" not in columns:
                self.db.execute("ALTER TABLE task ADD COLUMN parent_id INTEGER REFERENCES task(id) ON DELETE CASCADE")
            if "position" not in columns:
                self.db.execute("ALTER TABLE task ADD COLUMN position INTEGER")
            if "description" not in columns:
                self.db.execute("ALTER TABLE task ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            if "priority" not in columns:
                self.db.execute("ALTER TABLE task ADD COLUMN priority TEXT NOT NULL DEFAULT ''")
        self.db.execute("PRAGMA foreign_keys = ON")

    def tasks(self, day):
        groups = {}
        for task_id, title, done, parent_id, description, priority in self.db.execute("""
            SELECT id, title, done, parent_id, description, priority FROM task
            WHERE day = ? ORDER BY COALESCE(position, id), id
        """, (day,)):
            groups.setdefault(parent_id, []).append((task_id, title, done, description, priority))
        rows = []
        for index, (task_id, title, done, description, priority) in enumerate(groups.get(None, []), 1):
            rows.append((task_id, title, done, None, str(index), description, priority))
            for subindex, (child_id, child_title, child_done, child_description, child_priority) in enumerate(groups.get(task_id, []), 1):
                rows.append((child_id, child_title, child_done, task_id, f"{index}.{subindex}", child_description, child_priority))
        return rows

    def add(self, day, title, parent_id=None):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("""
                INSERT INTO task(day, title, parent_id, position)
                SELECT ?, ?, ?, COALESCE(MAX(COALESCE(position, id)), 0) + 1
                FROM task WHERE day = ? AND parent_id IS ?
            """, (day, title, parent_id, day, parent_id))

    def move(self, day, task_id, parent_id, position):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            siblings = [row[0] for row in self.db.execute("""
                SELECT id FROM task WHERE day = ? AND parent_id IS ?
                ORDER BY COALESCE(position, id), id
            """, (day, parent_id))]
            siblings.remove(task_id)
            siblings.insert(position - 1, task_id)
            self.db.executemany("UPDATE task SET position = ? WHERE id = ?",
                                enumerate(siblings, 1))

    def edit_task(self, task_id, title, priority):
        with self.db:
            self.db.execute("UPDATE task SET title = ?, priority = ? WHERE id = ?", (title, priority, task_id))

    def transfer_unfinished(self, source, target):
        source = date.fromisoformat(source).isoformat()
        target = date.fromisoformat(target).isoformat()
        if source == target:
            raise ValueError("目标日期不能与当前日期相同")
        parents = children = 0
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            rows = self.tasks(source)
            position = self.db.execute("""
                SELECT COALESCE(MAX(COALESCE(position, id)), 0) FROM task
                WHERE day = ? AND parent_id IS NULL
            """, (target,)).fetchone()[0]
            for task_id, title, done, parent_id, _, progress, priority in rows:
                if parent_id is not None:
                    continue
                subtasks = [row for row in rows if row[3] == task_id]
                pending = [row for row in subtasks if not row[2]]
                if done and not pending:
                    continue
                position += 1
                # Keep completed children and their parent on the original day.
                if any(row[2] for row in subtasks):
                    new_parent = self.db.execute("""
                        INSERT INTO task(day, title, done, parent_id, position, description, priority)
                        VALUES (?, ?, ?, NULL, ?, ?, ?)
                    """, (target, title, done, position, progress, priority)).lastrowid
                    self.db.execute("UPDATE task SET done = 1 WHERE id = ?", (task_id,))
                else:
                    new_parent = task_id
                    self.db.execute("UPDATE task SET day = ?, position = ? WHERE id = ?",
                                    (target, position, task_id))
                for subposition, child in enumerate(pending, 1):
                    self.db.execute("UPDATE task SET day = ?, parent_id = ?, position = ? WHERE id = ?",
                                    (target, new_parent, subposition, child[0]))
                parents += 1
                children += len(pending)
        return parents, children

    def toggle(self, task_id):
        with self.db:
            self.db.execute("UPDATE task SET done = 1 - done WHERE id = ?", (task_id,))

    def set_description(self, task_id, description):
        with self.db:
            self.db.execute("UPDATE task SET description = ? WHERE id = ?", (description, task_id))

    def delete(self, task_id):
        with self.db:
            self.db.execute("DELETE FROM task WHERE id = ?", (task_id,))

    def note(self, kind, day):
        if kind == "section":
            row = self.db.execute("SELECT body FROM weekly_section WHERE day = ?", (day,)).fetchone()
        else:
            row = self.db.execute(
                "SELECT body FROM note WHERE kind = ? AND day = ?", (kind, day)
            ).fetchone()
        return row[0] if row else ""

    def put_note(self, kind, day, body):
        if kind == "section":
            self.db.execute("UPDATE weekly_section SET body = ? WHERE day = ?", (body, day))
        else:
            self.db.execute("""
                INSERT INTO note(kind, day, body) VALUES (?, ?, ?)
                ON CONFLICT(kind, day) DO UPDATE SET body = excluded.body
            """, (kind, day, body))

    def save(self, notes):
        with self.db:
            for kind, day, body in notes:
                self.put_note(kind, day, body)

    def copy_checklist(self, day):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            tasks = self.tasks(day)
            if not tasks:
                return "empty"
            lines = ["待办清单"]
            for _, title, done, parent_id, number, progress, priority in tasks:
                indent = "  " if parent_id is not None else ""
                status = "已完成" if done else "未完成"
                title_line, *continuation = title.splitlines() or [title]
                lines.append(f"{indent}{number} 【{status}】{title_line}")
                lines.extend(f"{indent}  {line}" for line in continuation)
                priority_label = {"urgent": "紧急", "medium": "中等", "normal": "普通"}.get(priority)
                if priority_label:
                    lines.append(f"{indent}  紧急程度：{priority_label}")
                if progress:
                    first, *remaining = progress.splitlines()
                    lines.append(f"{indent}  进度：{first}")
                    lines.extend(f"{indent}    {line}" for line in remaining)
            snapshot = "\n".join(lines)
            digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO checklist_copy VALUES (?, ?)", (day, digest)
            ).rowcount
            if not inserted:
                return "duplicate"
            existing = self.note("daily", day)
            separator = "\n\n" if existing else ""
            self.put_note("daily", day, f"{existing}{separator}{snapshot}")
            return "copied"

    def week_sections(self, start):
        return self.db.execute("""
            SELECT day, body FROM weekly_section
            WHERE day BETWEEN ? AND ? ORDER BY day
        """, (start.isoformat(), (start + timedelta(days=6)).isoformat())).fetchall()

    def copy_reports(self, start, days):
        count = 0
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            for day in sorted(set(days)):
                if monday(day) != start:
                    raise ValueError("日报不在所选周内")
                key = day.isoformat()
                body = self.note("daily", key)
                if body.strip():
                    count += self.db.execute(
                        "INSERT OR IGNORE INTO weekly_section VALUES (?, ?)", (key, body)
                    ).rowcount
        return count
