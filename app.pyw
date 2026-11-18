import ctypes
import calendar
import os
import sqlite3
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from tkinter import font, messagebox, ttk

from storage import Store, monday


PAGES = {"tasks": "待办", "daily": "日报", "weekly": "周报", "draft": "随心记"}
WEEKDAYS = "一二三四五六日"
PRIORITIES = {
    "urgent": ("紧急", "#FF5F57"),
    "medium": ("中等", "#FEBC2E"),
    "normal": ("普通", "#28C840"),
}

BG = "#F4F6FA"
INK = "#18243A"
MUTED = "#616981"
ACCENT = "#3268D8"
RAIL = "#18243A"
EDGE = "#E2E7EF"


def priority_light(master, color, size):
    image = tk.PhotoImage(master=master, width=size + 2, height=size + 2)
    rgb = tuple(channel // 257 for channel in master.winfo_rgb(color))
    center = (size + 1) / 2
    rows = []
    for y in range(size + 2):
        pixels = []
        for x in range(size + 2):
            distance = abs(complex(x - center, y - center))
            coverage = max(0, min(1, size / 2 + 0.5 - distance))
            edge = max(0, min(1, distance - size / 2 + 1))
            channels = [round(255 * (1 - coverage) + c * (1 - 0.14 * edge) * coverage)
                        for c in rgb]
            pixels.append("#%02x%02x%02x" % tuple(channels))
        rows.append("{" + " ".join(pixels) + "}")
    image.put(" ".join(rows))
    return image


def date_label(day):
    return f"{day.isoformat()}  周{WEEKDAYS[day.weekday()]}"


class DeadlineTooltip:
    """One compact, non-interactive hint shared by all task rows."""

    def __init__(self, root):
        self.root = root
        self.job = self.window = self.widget = None
        root.bind("<Configure>", lambda event: self.hide() if event.widget == root else None, add="+")
        root.bind("<FocusOut>", lambda event: self.hide() if event.widget == root else None, add="+")

    def attach(self, widget, read_deadline):
        widget.bind("<Enter>", lambda event: self.schedule(widget, read_deadline), add="+")
        for sequence in ("<Leave>", "<ButtonPress>", "<Unmap>", "<Destroy>"):
            widget.bind(sequence, lambda event: self.hide(), add="+")

    def schedule(self, widget, read_deadline):
        self.hide()
        if read_deadline():
            self.widget = widget
            self.job = self.root.after(350, lambda: self.show(widget, read_deadline))

    def show(self, widget, read_deadline):
        self.job = None
        deadline = read_deadline()
        if not deadline or not widget.winfo_exists() or not widget.winfo_viewable():
            self.hide()
            return
        tip = self.window = tk.Toplevel(self.root)
        tip.withdraw()
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=f"截止日期：{deadline}", font=("Microsoft YaHei UI", 9),
                 bg="#FFFEF8", fg=INK, padx=8, pady=4,
                 highlightthickness=1, highlightbackground="#B6BFCD").pack()
        tip.update_idletasks()
        width, height = tip.winfo_reqwidth(), tip.winfo_reqheight()
        x = max(0, min(widget.winfo_rootx() + widget.winfo_width() - width,
                       tip.winfo_screenwidth() - width))
        y = widget.winfo_rooty() + widget.winfo_height() + 5
        if y + height > tip.winfo_screenheight():
            y = max(0, widget.winfo_rooty() - height - 5)
        tip.geometry(f"+{x}+{y}")
        tip.deiconify()

    def hide(self):
        if self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None
        if self.window is not None:
            self.window.destroy()
            self.window = None
        self.widget = None


class TaskRow:
    """Keep native widgets alive; callbacks always read the current row data."""

    def __init__(self, app, master, actions):
        self.app, self.actions = app, actions
        self.data = None
        self.frame = tk.Frame(master, bg="white", padx=9, pady=3,
                              highlightthickness=1, highlightbackground=EDGE)
        self.state = tk.BooleanVar(self.frame)
        self.check = ttk.Checkbutton(self.frame, variable=self.state, style="Task.TCheckbutton",
                                     command=lambda: self.invoke("toggle"))
        self.check.state(["!alternate"])
        self.check.pack(side="left")
        marker = tk.Frame(self.frame, width=18, height=16, bg="white")
        marker.pack(side="left")
        self.light = tk.Label(marker, bg="white", borderwidth=0, highlightthickness=0)
        self.light.place(relx=0.5, rely=0.5, anchor="center")
        self.number = ttk.Button(self.frame, width=5, style="Quiet.TButton",
                                 command=lambda: self.invoke("reorder"))
        self.number.pack(side="left")
        buttons = tk.Frame(self.frame, bg="white")
        buttons.pack(side="right")
        self.buttons = {}
        for key, caption in (("describe", "进度"), ("edit", "编辑"),
                             ("add_subtask", "＋子待办"), ("delete", "删除")):
            button = ttk.Button(buttons, text=caption, style="TaskAction.TButton",
                                command=lambda key=key: self.invoke(key))
            button.pack(side="left", padx=(4, 0))
            self.buttons[key] = button
        app.deadline_tooltip.attach(self.buttons["describe"], lambda: self.data[7] if self.data else "")
        self.label = tk.Label(self.frame, anchor="w", justify="left", bg="white")
        self.label.pack(side="left", fill="x", expand=True, padx=(3, 8))
        self.label.bind("<Double-Button-1>", lambda event: self.invoke("edit"))

        def wrap(event):
            width = max(80, event.width)
            if self.label.winfo_pixels(str(self.label.cget("wraplength"))) != width:
                self.label.configure(wraplength=width)

        self.label.bind("<Configure>", wrap)
        app.task_bind_scroll(self.frame)

    def invoke(self, action):
        task_id, title, _, parent_id, number, description, priority, deadline = self.data
        args = {
            "toggle": (task_id, self.state), "reorder": (task_id, parent_id, number),
            "describe": (task_id, number, description, deadline),
            "edit": (task_id, title, priority, parent_id),
            "add_subtask": (task_id,), "delete": (task_id, title),
        }
        self.app.run_action(lambda: self.actions[action](*args[action]))

    def update(self, data):
        old = self.data
        self.data = data
        _, title, done, parent_id, number, description, priority, deadline = data
        if old is None or old[1] != title:
            self.label.configure(text=title)
        if old is None or old[2] != done:
            self.state.set(bool(done))
            self.label.configure(font=("Microsoft YaHei UI", 11, "overstrike" if done else "normal"),
                                 fg=MUTED if done else INK)
        if old is None or old[4] != number:
            self.number.configure(text=number)
        if old is None or bool(old[5] or old[7]) != bool(description or deadline):
            self.buttons["describe"].configure(
                style="HasDescription.TaskAction.TButton" if description or deadline else "TaskAction.TButton")
        if old is None or old[6] != priority:
            self.light.configure(image=self.app.priority_lights[priority, 12] if priority in PRIORITIES else "")
        if old is None or (old[3] is None) != (parent_id is None):
            if parent_id is None:
                self.buttons["add_subtask"].pack(side="left", padx=(4, 0), before=self.buttons["delete"])
            else:
                self.buttons["add_subtask"].pack_forget()


class App:
    def __init__(self, root, store):
        self.root, self.store = root, store
        self.deadline_tooltip = DeadlineTooltip(root)
        self.page = "tasks"
        self.day = date.today()
        self.week = monday(self.day)
        self.editors = []
        self.save_job = None
        self.status = tk.StringVar(value="本地存储 · 自动保存已开启")
        self.task_drafts = {}
        self.task_canvas = None
        root.title("便签 ToList")
        root.geometry(f"{min(1040, root.winfo_screenwidth() - 80)}x{min(700, root.winfo_screenheight() - 100)}")
        root.minsize(780, 560)
        root.configure(background=BG)
        root.iconbitmap(str(Path(__file__).with_name("tolist.ico")))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Control-s>", lambda event: self.flush(announce=True))
        font.nametofont("TkDefaultFont").configure(family="Microsoft YaHei UI", size=10)

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("TCheckbutton", background=BG, foreground=INK)
        style.configure("TButton", padding=(12, 7), foreground=INK, background="#FFFFFF",
                        bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE)
        style.map("TButton", background=[("active", "#EEF0FA")])
        style.configure("Toolbar.TButton", padding=(8, 7), width=0)
        style.layout("Priority.TRadiobutton", style.layout("TButton"))
        style.configure("Priority.TRadiobutton", padding=(9, 5), background="white",
                        foreground=INK, borderwidth=1, relief="flat")
        style.map("Priority.TRadiobutton",
                  background=[("!disabled", "white")],
                  bordercolor=[("selected", ACCENT), ("active", MUTED), ("!selected", EDGE)],
                  foreground=[("selected", ACCENT)],
                  lightcolor=[("selected", ACCENT), ("!selected", EDGE)],
                  darkcolor=[("selected", ACCENT), ("!selected", EDGE)])
        self.priority_lights = {
            (key, size): priority_light(root, color, size)
            for key, (_, color) in PRIORITIES.items() for size in (12, 14)
        }
        style.configure("Accent.TButton", foreground="white", background=ACCENT,
                        bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT)
        style.map("Accent.TButton", background=[("disabled", "#E8E9F1"), ("active", "#2455BA")],
                  foreground=[("disabled", MUTED)])
        style.configure("Nav.TButton", anchor="w", padding=(13, 10), background=RAIL,
                        foreground="#B9C5DA", borderwidth=0, relief="flat")
        style.map("Nav.TButton", background=[("active", "#253752")], foreground=[("active", "white")])
        style.configure("Current.Nav.TButton", foreground="white", background="#304568")
        style.map("Current.Nav.TButton", background=[("active", "#3C5378")])
        style.configure("Quiet.TButton", padding=(7, 5), borderwidth=0, relief="flat", foreground=MUTED)
        style.configure("TaskAction.TButton", width=0, padding=(7, 4),
                        font=("Microsoft YaHei UI", 9), foreground=MUTED, background="#F4F6FA")
        style.configure("HasDescription.TaskAction.TButton", foreground=ACCENT)
        style.configure("TEntry", padding=9, fieldbackground="white", bordercolor=EDGE,
                        lightcolor=EDGE, darkcolor=EDGE)
        style.configure("Task.TCheckbutton", background="white", foreground=INK, padding=(5, 7))
        style.map("Task.TCheckbutton", background=[("active", "#EEF0FA")],
                  indicatorcolor=[("selected", ACCENT)])

        rail = tk.Frame(root, bg=RAIL, padx=18, pady=30, width=196)
        rail.pack_propagate(False)
        rail.pack(side="left", fill="y")
        self.brand_icon = tk.PhotoImage(file=str(Path(__file__).with_name("brand.png")))
        tk.Label(rail, text="便签 ToList", font=("Microsoft YaHei UI", 13, "bold"),
                 image=self.brand_icon, compound="left", padx=4,
                 bg=RAIL, fg="white").pack(pady=(0, 8), anchor="w")
        tk.Label(rail, text="让每一天，有条有理", bg=RAIL, fg="#9DAEC8",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(0, 32))
        tk.Label(rail, text="v0.2.3", bg=RAIL, fg="#9DAEC8",
                 font=("Microsoft YaHei UI", 9)).pack(side="bottom", anchor="w")
        self.nav = {}
        for key, label in PAGES.items():
            button = ttk.Button(rail, text={"tasks": "01   待办", "daily": "02   日报", "weekly": "03   周报", "draft": "04   随心记"}[key], width=13,
                                command=lambda key=key: self.run_action(lambda: self.go(key)))
            button.pack(fill="x", pady=4)
            self.nav[key] = button

        column = tk.Frame(root, bg=BG, padx=28, pady=28)
        column.pack(side="left", fill="both", expand=True)
        tk.Label(column, textvariable=self.status, bg=BG, fg=MUTED, anchor="w").pack(
            side="bottom", fill="x", pady=(9, 0)
        )
        self.panel = tk.Frame(column, bg=BG)
        self.panel.pack(fill="both", expand=True)
        self.render()

    def run_action(self, action):
        try:
            action()
        except sqlite3.Error as exc:
            messagebox.showerror("无法写入或读取数据", f"{exc}\n\n可以重试刚才的操作。", parent=self.root)

    def go(self, page, *, day=None, week=None, message=""):
        if not self.flush():
            return
        if day is not None:
            self.day = day
        if week is not None:
            self.week = week
        elif page == "weekly" and self.page != "weekly":
            self.week = monday(self.day)
        self.page = page
        self.render()
        self.status.set(message or "本地存储 · 自动保存已开启")

    def render(self):
        self.deadline_tooltip.hide()
        if self.page == "tasks" and self.task_canvas is not None:
            self.refresh_tasks()
            return
        self.task_canvas = None
        self.editors.clear()
        for child in self.panel.winfo_children():
            child.destroy()
        for key, button in self.nav.items():
            button.configure(style="Current.Nav.TButton" if key == self.page else "Nav.TButton")
        if self.page == "tasks":
            tasks = self.store.tasks(self.day.isoformat())
            self.task_header(tasks)
            self.tasks_page(self.navigation(), tasks)
            return
        tk.Label(self.panel, text=PAGES[self.page], bg=BG, fg=INK,
                 font=("Microsoft YaHei UI", 26, "bold")).pack(anchor="w", pady=(0, 4))
        descriptions = {"daily": "记录今天的成果，也留下一点思考。",
                        "weekly": "把一周的积累，整理成清晰的回顾。", "draft": "灵感、想法、临时记录，都放在这里。"}
        tk.Label(self.panel, text=descriptions[self.page], bg=BG, fg=MUTED).pack(anchor="w", pady=(0, 22))
        if self.page != "draft":
            self.navigation()
        if self.page == "daily":
            ttk.Button(self.panel, text="复制到周报", style="Accent.TButton",
                       command=lambda: self.run_action(self.choose_reports)).pack(anchor="e", pady=(0, 8))
            self.editor(self.panel, "daily", self.day.isoformat(), self.store.note("daily", self.day.isoformat()),
                        grow=True)
        elif self.page == "weekly":
            self.weekly_page()
        else:
            self.editor(self.panel, "draft", "", self.store.note("draft", ""), grow=True)

    def dialog_shell(self, title, description, width=640, height=420):
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title(title)
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.minsize(480, 320)
        body = tk.Frame(dialog, bg=BG, padx=26, pady=22)
        body.pack(fill="both", expand=True)
        heading = tk.Frame(body, name="heading", bg=BG)
        heading.pack(fill="x", pady=(0, 18))
        titles = tk.Frame(heading, bg=BG)
        titles.pack(side="left", fill="x", expand=True)
        tk.Label(titles, text=title, bg=BG, fg=INK,
                 font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w")
        tk.Label(titles, text=description, bg=BG, fg=MUTED,
                 anchor="w").pack(fill="x", pady=(7, 0))
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        dialog.update_idletasks()
        width = min(width, dialog.winfo_screenwidth() - 40)
        height = min(height, dialog.winfo_screenheight() - 80)
        x = max(0, min(self.root.winfo_rootx() + (self.root.winfo_width()-width)//2,
                       dialog.winfo_screenwidth()-width))
        y = max(0, min(self.root.winfo_rooty() + (self.root.winfo_height()-height)//2,
                       dialog.winfo_screenheight()-height-60))
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        return dialog, body

    def pick_deadline(self, parent, variable):
        picker = tk.Toplevel(parent)
        picker.title("选择截止日期")
        picker.transient(parent)
        picker.resizable(False, False)
        picker.configure(bg=BG)
        today = date.today()
        month = today.replace(day=1)
        header = tk.Frame(picker, bg=BG, padx=10, pady=10)
        header.pack(fill="x")
        caption = tk.StringVar(picker)
        grid = tk.Frame(picker, bg=BG, padx=10, pady=5)
        grid.pack()

        def choose(value):
            variable.set(value.isoformat())
            picker.destroy()

        def render():
            caption.set(f"{month.year} 年 {month.month} 月")
            previous.state(["disabled"] if month == date.min else ["!disabled"])
            following.state(["disabled"] if month.year == 9999 and month.month == 12 else ["!disabled"])
            for child in grid.winfo_children():
                child.destroy()
            for col, label in enumerate(WEEKDAYS):
                tk.Label(grid, text=label, bg=BG, fg=MUTED).grid(row=0, column=col, pady=5)
            for row, week in enumerate(calendar.monthcalendar(month.year, month.month), 1):
                for col, number in enumerate(week):
                    if number:
                        value = date(month.year, month.month, number)
                        ttk.Button(grid, text=str(number), width=3,
                                   style="Accent.TButton" if value == today else "TButton",
                                   command=lambda value=value: choose(value)).grid(row=row, column=col, padx=1, pady=1)

        def change(offset):
            nonlocal month
            year, index = divmod(month.year * 12 + month.month - 1 + offset, 12)
            month = date(year, index + 1, 1)
            render()

        previous = ttk.Button(header, text="‹", width=3, command=lambda: change(-1))
        previous.pack(side="left")
        following = ttk.Button(header, text="›", width=3, command=lambda: change(1))
        following.pack(side="right")
        tk.Label(header, textvariable=caption, bg=BG, fg=INK).pack(side="left", expand=True)
        ttk.Button(picker, text="今天", command=lambda: choose(date.today())).pack(pady=10)
        picker.bind("<Escape>", lambda event: picker.destroy())
        render()
        picker.update_idletasks()
        x = max(0, min(parent.winfo_rootx() + 35, picker.winfo_screenwidth() - picker.winfo_reqwidth()))
        y = max(0, min(parent.winfo_rooty() + 80, picker.winfo_screenheight() - picker.winfo_reqheight() - 60))
        picker.geometry(f"+{x}+{y}")
        picker.grab_set()
        picker.focus_set()
        parent.wait_window(picker)
        if parent.winfo_exists():
            parent.grab_set()

    def edit_value(self, title, description, initial, *, multiline=False, allow_delete=False, priority=None, deadline=None):
        dialog, body = self.dialog_shell(title, description, height=440 if multiline else 320)
        if deadline is not None:
            dialog.minsize(640, 400)
        result = None
        actions = tk.Frame(body, bg=BG)
        actions.pack(side="bottom", fill="x", pady=(18, 0))
        error = tk.StringVar()
        tk.Label(body, textvariable=error, bg=BG, fg="#B33B40", anchor="w").pack(side="bottom", fill="x")
        if deadline is not None:
            due = tk.StringVar(dialog, value=deadline)
            heading = body.nametowidget("heading")
            due_group = tk.Frame(heading, bg=BG)
            due_group.pack(side="right", anchor="n", padx=(16, 0), before=heading.winfo_children()[0])
            tk.Label(due_group, text="截止日期", bg=BG, fg=INK,
                     font=("Microsoft YaHei UI", 12)).pack(anchor="w", pady=(0, 6))
            due_row = tk.Frame(due_group, bg=BG)
            due_row.pack(anchor="w")
            date_box = tk.Frame(due_row, bg="white", highlightthickness=1, highlightbackground="#B6BFCD")
            date_box.pack(side="left")
            style = ttk.Style(dialog)
            style.configure("Deadline.TEntry", borderwidth=0, padding=(8, 5), fieldbackground="white")
            due_field = ttk.Entry(date_box, textvariable=due, width=12,
                                  style="Deadline.TEntry", font=("Microsoft YaHei UI", 12))
            due_field.pack(side="left")
            icon = tk.PhotoImage(master=dialog, width=24, height=24)
            for rectangle in ((3, 6, 21, 8), (3, 11, 21, 13), (3, 21, 21, 23),
                              (3, 6, 5, 23), (19, 6, 21, 23), (7, 2, 9, 10), (15, 2, 17, 10)):
                icon.put(MUTED, to=rectangle)
            style.configure("Calendar.TButton", padding=(3, 3), width=0, background="white",
                            bordercolor="white", lightcolor="white", darkcolor="white")
            calendar_button = ttk.Button(date_box, name="calendar_button", image=icon,
                                          style="Calendar.TButton", cursor="hand2",
                                          command=lambda: self.pick_deadline(dialog, due))
            calendar_button.calendar_image = icon
            calendar_button.pack(side="right", padx=(0, 4))
            style.configure("DeadlineClear.TButton", padding=(6, 4), width=0, foreground=ACCENT,
                            background=BG, bordercolor=BG, lightcolor=BG, darkcolor=BG)
            ttk.Button(due_row, text="清除", style="DeadlineClear.TButton", command=lambda: due.set("")).pack(side="left", padx=(6, 0))
        if multiline:
            frame = tk.Frame(body, bg="white", highlightthickness=1, highlightbackground=EDGE)
            frame.pack(fill="both", expand=True)
            field = tk.Text(frame, wrap="word", undo=True, font=("Microsoft YaHei UI", 12),
                            padx=15, pady=13, relief="flat", height=6, bg="white", fg=INK,
                            selectbackground="#DCE8FF", selectforeground=INK)
            bar = ttk.Scrollbar(frame, command=field.yview)
            bar.pack(side="right", fill="y")
            field.configure(yscrollcommand=bar.set)
            field.pack(fill="both", expand=True)
            field.insert("1.0", initial)
            field.edit_reset()
            field.bind("<Control-a>", lambda event: self.select_all(field))
            read = lambda: field.get("1.0", "end-1c").strip()
        else:
            field = ttk.Entry(body, font=("Microsoft YaHei UI", 13))
            field.pack(fill="x", pady=10)
            field.insert(0, initial)
            field.selection_range(0, "end")
            read = lambda: field.get().strip()

        if priority is not None:
            selected = tk.StringVar(dialog, value=priority)
            choices = tk.Frame(body, bg=BG)
            choices.pack(fill="x", pady=(12, 0))
            tk.Label(choices, text="优先级", bg=BG, fg=INK).pack(side="left", padx=(0, 10))
            ttk.Radiobutton(choices, text="不标记", value="", variable=selected,
                            style="Priority.TRadiobutton").pack(side="left", padx=(0, 8))
            for key, (caption, _) in PRIORITIES.items():
                ttk.Radiobutton(choices, text=f" {caption}", value=key, variable=selected,
                                image=self.priority_lights[key, 14], compound="left",
                                style="Priority.TRadiobutton").pack(side="left", padx=(0, 8))

        def submit(event=None):
            nonlocal result
            value = read()
            if deadline is not None:
                due_value = due.get().strip()
                try:
                    if due_value:
                        parsed = date.fromisoformat(due_value)
                        if parsed.isoformat() != due_value:
                            raise ValueError
                except ValueError:
                    error.set("截止日期无效，请选择日期或填写 YYYY-MM-DD。")
                    due_field.focus_set()
                    return "break"
            if not value and deadline is None:
                error.set("内容不能为空，请填写后再保存。")
                field.focus_set()
                return "break"
            result = ((value, due_value) if deadline is not None else
                      (value, selected.get()) if priority is not None else value)
            dialog.destroy()
            return "break"

        ttk.Button(actions, text="保存" if multiline else "确定", style="Accent.TButton",
                   command=submit).pack(side="right")
        ttk.Button(actions, text="取消", command=dialog.destroy).pack(side="right", padx=(0, 10))
        if allow_delete and initial:
            def delete_value():
                nonlocal result
                if messagebox.askyesno("删除进度", "删除这条进度？", parent=dialog):
                    result = ("", deadline) if deadline is not None else ""
                    dialog.destroy()

            ttk.Button(actions, text="删除进度", command=delete_value).pack(side="left")
        else:
            tk.Label(actions, text="Ctrl+Enter 保存" if multiline else "Enter 确定", bg=BG,
                     fg=MUTED).pack(side="left")
        dialog.bind("<Control-Return>", submit)
        if not multiline:
            field.bind("<Return>", submit)
        dialog.deiconify()
        dialog.grab_set()
        field.focus_set()
        self.root.wait_window(dialog)
        return result

    def navigation(self):
        weekly = self.page == "weekly"
        period = self.week if weekly else self.day
        step = timedelta(days=7 if weekly else 1)
        row = tk.Frame(self.panel, bg=BG)
        row.pack(fill="x", pady=(0, 7 if self.page == "tasks" else 11))
        dates = tk.Frame(row, bg=BG)
        if self.page == "tasks":
            dates.grid(row=0, column=0, sticky="w")
            row.columnconfigure(1, weight=1)
        else:
            dates.pack(side="left")
        button_style = "Toolbar.TButton" if self.page == "tasks" else "TButton"

        def select(value):
            if value:
                kwargs = {"week": monday(value)} if weekly else {"day": value}
                self.run_action(lambda: self.go(self.page, **kwargs))

        def pick_date():
            current = self.day if self.page == "tasks" else period
            result = self.edit_value("选择日期", "输入日期，格式为 YYYY-MM-DD", current.isoformat())
            if result is None:
                return
            try:
                value = date.fromisoformat(result.strip())
            except ValueError:
                messagebox.showwarning("日期格式有误", "请输入有效日期，例如 2026-11-17。", parent=self.root)
                return
            select(value)

        current_period = lambda: self.day if self.page == "tasks" else period
        ttk.Button(dates, text="‹", width=2, style=button_style, command=lambda: select(current_period() - step)).pack(side="left")
        label = (f"{period.isoformat()} 至 {(period + timedelta(days=6)).isoformat()}"
                 if weekly else date_label(period))
        date_button = ttk.Button(dates, text=label, style=button_style, command=pick_date)
        date_button.pack(side="left", padx=5)
        if self.page == "tasks":
            self.task_date_button = date_button
        ttk.Button(dates, text="›", width=2, style=button_style, command=lambda: select(current_period() + step)).pack(side="left")
        ttk.Button(dates, text="本周" if weekly else "今天", style=button_style,
                   command=lambda: select(date.today())).pack(side="left", padx=7)

        return row

    def scroll_panel(self):
        frame = tk.Frame(self.panel, bg=BG)
        frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(frame, bg=BG, highlightthickness=0)
        if self.page == "tasks":
            self.task_canvas = canvas
        bar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        def scrolled(first, last):
            self.deadline_tooltip.hide()
            bar.set(first, last)
        canvas.configure(yscrollcommand=scrolled)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG)
        window = canvas.create_window(0, 0, anchor="nw", window=inner)
        def resize_inner(event):
            region = canvas.bbox("all")
            if region != resize_inner.region:
                resize_inner.region = region
                canvas.configure(scrollregion=region)

        resize_inner.region = None

        def resize_canvas(event):
            if int(float(canvas.itemcget(window, "width"))) != event.width:
                canvas.itemconfigure(window, width=event.width)

        inner.bind("<Configure>", resize_inner)
        canvas.bind("<Configure>", resize_canvas)

        def scroll(event):
            self.deadline_tooltip.hide()
            if canvas.yview() != (0.0, 1.0):
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

        def bind_scroll(widget):
            if not isinstance(widget, tk.Text):
                widget.bind("<MouseWheel>", scroll)
            for child in widget.winfo_children():
                bind_scroll(child)

        bind_scroll(frame)
        if self.page == "tasks":
            self.task_bind_scroll = bind_scroll
        return inner

    def task_header(self, tasks):
        header = tk.Frame(self.panel, bg=BG)
        header.pack(fill="x", pady=(0, 4))
        tk.Label(header, text=PAGES["tasks"], bg=BG, fg=INK,
                 font=("Microsoft YaHei UI", 19, "bold")).pack(side="left")
        completed = sum(row[2] for row in tasks)
        self.task_counts = []
        stats = tk.Frame(header, bg=BG)
        stats.pack(side="right")
        for caption, count, color in (
            ("全部待办", len(tasks), INK),
            ("等待完成", len(tasks) - completed, ACCENT),
            ("已经完成", completed, "#248572"),
        ):
            card = tk.Frame(stats, bg="white", padx=8, pady=2,
                            highlightthickness=1, highlightbackground=EDGE)
            card.pack(side="left", padx=(6, 0))
            tk.Label(card, text=caption, bg="white", fg=MUTED,
                     font=("Microsoft YaHei UI", 9)).pack(side="left")
            value = tk.StringVar(card, value=str(count))
            self.task_counts.append(value)
            tk.Label(card, textvariable=value, bg="white", fg=color,
                     font=("Microsoft YaHei UI", 11, "bold")).pack(side="left", padx=(5, 0))

    def tasks_page(self, navigation, tasks):
        day = self.day.isoformat()
        dates = navigation.winfo_children()[0]
        transfer = tk.Frame(navigation, bg=BG)
        transfer.grid(row=0, column=1, sticky="e", padx=(16, 0))
        tk.Label(transfer, text="未完成事项", bg=BG, fg=MUTED).pack(side="left")
        transfer_state = "normal" if any(not row[2] for row in tasks) else "disabled"
        next_button = ttk.Button(transfer, text="移至次日", state=transfer_state, style="Toolbar.TButton",
                   command=lambda: self.run_action(
                       lambda: self.transfer_tasks(self.day + timedelta(days=1))))
        next_button.pack(side="left", padx=(10, 7))
        date_button = ttk.Button(transfer, text="移至指定日期…", state=transfer_state, style="Toolbar.TButton",
                                 command=lambda: self.run_action(self.choose_transfer_date))
        date_button.pack(side="left")
        copy_button = ttk.Button(transfer, text="复制到日报", state="normal" if tasks else "disabled",
                   style="Toolbar.TButton",
                   command=lambda: self.run_action(self.copy_checklist))
        copy_button.pack(side="left", padx=(16, 0))

        def arrange_toolbar(event):
            wide = event.width >= dates.winfo_reqwidth() + transfer.winfo_reqwidth() + 16
            if wide == arrange_toolbar.wide:
                return
            arrange_toolbar.wide = wide
            transfer.grid_configure(row=0 if wide else 1, column=1 if wide else 0,
                                    columnspan=1 if wide else 2,
                                    padx=(16, 0) if wide else 0,
                                    pady=0 if wide else (7, 0))

        arrange_toolbar.wide = None
        navigation.bind("<Configure>", arrange_toolbar)
        controls = tk.Frame(self.panel, bg=BG)
        controls.pack(fill="x", pady=(0, 9))
        if day not in self.task_drafts:
            self.task_drafts[day] = tk.StringVar()
        draft = self.task_drafts[day]
        entry = ttk.Entry(controls, textvariable=draft)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 7))

        def add():
            title = entry.get().strip()
            if title:
                self.store.add(day, title)
                draft.set("")
                refresh()
                self.status.set("已添加")

        def refresh():
            nonlocal tasks, day, draft
            self.deadline_tooltip.hide()
            changed_day = day != self.day.isoformat()
            if changed_day:
                day = self.day.isoformat()
                if day not in self.task_drafts:
                    self.task_drafts[day] = tk.StringVar()
                draft = self.task_drafts[day]
                entry.configure(textvariable=draft)
                self.task_date_button.configure(text=date_label(self.day))
            tasks = self.store.tasks(day)
            new_ids = [row[0] for row in tasks]
            new_id_set = set(new_ids)
            # Reuse row widgets on date switches as well as ordinary updates.
            available = [row for key, row in row_views.items() if key not in new_id_set] + spare_rows[:]
            spare_rows.clear()
            updated_views = {}
            for data in tasks:
                row = row_views.get(data[0])
                if row is None:
                    row = available.pop() if available else TaskRow(self, items, actions)
                row.update(data)
                updated_views[data[0]] = row
            for row in available:
                row.frame.pack_forget()
                if len(spare_rows) < 200:
                    spare_rows.append(row)
                else:
                    row.frame.destroy()
            if list(row_views) != new_ids:
                following = None
                for data in reversed(tasks):
                    row = updated_views[data[0]]
                    options = dict(fill="x", padx=(26 if data[3] is not None else 0, 0), pady=2)
                    if following is not None:
                        options["before"] = following
                    row.frame.pack(**options)
                    following = row.frame
            row_views.clear()
            row_views.update(updated_views)
            if tasks:
                empty.pack_forget()
            else:
                empty.pack(fill="x", pady=(12, 0))
            completed = sum(row[2] for row in tasks)
            for variable, value in zip(self.task_counts, (len(tasks), len(tasks) - completed, completed)):
                if variable.get() != str(value):
                    variable.set(str(value))
            pending = any(not row[2] for row in tasks)
            for button in (next_button, date_button):
                button.state(["!disabled"] if pending else ["disabled"])
            copy_button.state(["!disabled"] if tasks else ["disabled"])
            if changed_day:
                self.task_canvas.yview_moveto(0)

        def edit(task_id, title, priority, parent_id):
            updated = self.edit_value("编辑待办" if parent_id is None else "编辑子待办",
                                      "可修改内容，也可手动设置或取消优先级。",
                                      title, multiline=True, priority=priority)
            if updated is not None:
                self.store.edit_task(task_id, *updated)
                refresh()
                self.status.set("已保存")

        def add_subtask(task_id):
            title = self.edit_value("添加子待办", "输入具体事项，编号会自动生成。", "", multiline=True)
            if title and title.strip():
                self.store.add(day, title.strip(), task_id)
                refresh()
                self.status.set("已添加子待办")

        def describe(task_id, number, description, deadline):
            updated = self.edit_value("待办进度", f"事项 {number} · 支持多行文字",
                                      description, multiline=True, allow_delete=True, deadline=deadline)
            if updated is not None and updated != (description, deadline):
                self.store.set_progress(task_id, *updated)
                refresh()
                self.status.set("已保存进度和截止日期")

        def reorder(task_id, parent_id, number):
            count = sum(row[3] == parent_id for row in tasks)
            old_position = number.split(".")[-1]
            result = self.edit_value("调整编号", f"输入同级排序位置（1–{count}）。", old_position)
            if result is None:
                return
            try:
                position = int(result.strip())
            except ValueError:
                position = 0
            if not 1 <= position <= count:
                messagebox.showwarning("编号无效", f"请输入 1–{count} 之间的整数。", parent=self.root)
                return
            self.store.move(day, task_id, parent_id, position)
            refresh()
            self.status.set("已调整排序")

        def delete(task_id, title):
            detail = "\n其下的子待办也会删除。" if any(row[3] == task_id for row in tasks) else ""
            if messagebox.askyesno("删除待办", f"删除「{title}」？{detail}", parent=self.root):
                self.store.delete(task_id)
                refresh()
                self.status.set("已删除")

        def toggle(task_id, state):
            try:
                self.store.toggle(task_id)
            except sqlite3.Error:
                state.set(not state.get())
                raise
            refresh()
            self.status.set("已保存")

        entry.bind("<Return>", lambda event: self.run_action(add))
        entry.focus_set()
        ttk.Button(controls, text="＋ 添加", style="Accent.TButton", command=lambda: self.run_action(add)).pack(side="left")
        items = self.scroll_panel()
        empty = tk.Frame(items, bg="white", highlightthickness=1, highlightbackground=EDGE)
        tk.Label(empty, text="这一天还没有待办", bg="white", fg=INK,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(pady=(38, 6))
        tk.Label(empty, text="在上方输入内容，按 Enter 添加。", bg="white", fg=MUTED).pack(pady=(0, 38))
        self.task_bind_scroll(empty)
        row_views = {}
        spare_rows = []
        actions = {"toggle": toggle, "edit": edit, "describe": describe,
                   "reorder": reorder, "delete": delete, "add_subtask": add_subtask}
        self.task_rows = row_views
        self.refresh_tasks = refresh
        refresh()

    def choose_transfer_date(self):
        result = self.edit_value("转移未完成事项", "输入目标日期（YYYY-MM-DD），未完成子待办会带上父项。",
                                 (self.day + timedelta(days=1)).isoformat())
        if result is None:
            return
        try:
            target = date.fromisoformat(result.strip())
        except ValueError:
            messagebox.showwarning("日期格式有误", "请输入有效日期，例如 2026-11-19。", parent=self.root)
            return
        self.transfer_tasks(target)

    def transfer_tasks(self, target):
        if target == self.day:
            messagebox.showwarning("目标日期无效", "请选择与当前日期不同的日期。", parent=self.root)
            return
        if not self.flush():
            return
        parents, children = self.store.transfer_unfinished(self.day.isoformat(), target.isoformat())
        if not parents:
            self.status.set("当前日期没有未完成事项")
            return
        self.go("tasks", day=target,
                message=f"已转移至 {target.isoformat()}：{parents} 项待办、{children} 项子待办，已重新编号")

    def copy_checklist(self):
        result = self.store.copy_checklist(self.day.isoformat())
        if result != "empty":
            message = "已复制待办" if result == "copied" else "这份待办已复制"
            self.go("daily", message=message)

    def choose_reports(self):
        if not self.flush():
            return
        start = monday(self.day)
        existing = dict(self.store.week_sections(start))
        dialog, body = self.dialog_shell("选择要复制的日报", "勾选本周日报，按日期汇总到周报。", height=510)
        dialog.minsize(520, 510)
        available = {}
        confirm = ttk.Button(body, text="复制到周报", style="Accent.TButton")

        def sync_button():
            confirm.state(["!disabled"] if any(v.get() for v in available.values()) else ["disabled"])

        def submit():
            selected = [day for day, state in available.items() if state.get()]
            if not selected:
                return
            count = self.store.copy_reports(start, selected)
            dialog.destroy()
            self.go("weekly", week=start, message=f"已复制 {count} 篇日报" if count else "所选日报已复制")

        for offset in range(7):
            day = start + timedelta(days=offset)
            key = day.isoformat()
            label = date_label(day)
            state = tk.BooleanVar(dialog)
            check = ttk.Checkbutton(body, variable=state, command=sync_button)
            if key in existing:
                check.configure(text=f"{label}  ·  已复制")
                check.state(["disabled"])
            elif not self.store.note("daily", key).strip():
                check.configure(text=f"{label}  ·  无日报")
                check.state(["disabled"])
            else:
                check.configure(text=label)
                state.set(day == self.day)
                available[day] = state
            check.state(["!alternate"])
            check.pack(anchor="w", pady=8)
        confirm.configure(command=lambda: self.run_action(submit))
        confirm.pack(side="right", anchor="s", pady=(18, 0))
        ttk.Button(body, text="取消", command=dialog.destroy).pack(side="right", anchor="s", padx=10, pady=(18, 0))
        sync_button()
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        dialog.update_idletasks()
        dialog.deiconify()
        dialog.grab_set()
        dialog.focus_set()

    def weekly_page(self):
        inner = self.scroll_panel()
        week_key = self.week.isoformat()
        tk.Label(inner, text="周报正文", bg=BG, fg=INK).pack(anchor="w", pady=(0, 6))
        self.editor(inner, "weekly", week_key, self.store.note("weekly", week_key), height=6)
        sections = self.store.week_sections(self.week)
        if not sections:
            tk.Label(inner, text="还没有导入日报 · 在日报页选择「复制到周报」即可汇总。",
                     bg=BG, fg=MUTED, wraplength=440, justify="left").pack(anchor="w", pady=18)
        for day, body in sections:
            tk.Label(inner, text=date_label(date.fromisoformat(day)), bg=BG, fg=INK).pack(
                anchor="w", pady=(13, 6)
            )
            self.editor(inner, "section", day, body, height=6)

    def editor(self, container, kind, day, body, *, height=8, grow=False):
        frame = tk.Frame(container, bg="white", highlightthickness=1, highlightbackground=EDGE, highlightcolor=ACCENT)
        frame.pack(fill="both" if grow else "x", expand=grow)
        text = tk.Text(frame, height=height, undo=True, font=("Microsoft YaHei UI", 11),
                       wrap="word", padx=20, pady=15, spacing1=3, spacing3=3,
                       selectbackground="#E1DEFF", selectforeground=INK, fg=INK, bg="white", relief="flat",
                       borderwidth=0, insertbackground=INK)
        bar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        bar.pack(side="right", fill="y")
        text.configure(yscrollcommand=bar.set)
        text.pack(side="left", expand=True, fill="both")
        text.insert("1.0", body)
        text.edit_reset()
        text.edit_modified(False)
        self.editors.append((text, kind, day, body))
        text.bind("<<Modified>>", self.schedule_save)
        text.bind("<FocusIn>", lambda event: frame.configure(highlightbackground=ACCENT))
        text.bind("<FocusOut>", lambda event: frame.configure(highlightbackground=EDGE))
        text.bind("<Control-a>", lambda event: self.select_all(text))
        if grow:
            text.focus_set()
        return text

    @staticmethod
    def select_all(text):
        text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def schedule_save(self, event):
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            if self.save_job is not None:
                self.root.after_cancel(self.save_job)
            self.status.set("保存中…")
            self.save_job = self.root.after(450, self.autosave)

    def autosave(self):
        self.save_job = None
        self.flush(interactive=False)

    def flush(self, *, interactive=True, announce=False):
        if self.save_job is not None:
            self.root.after_cancel(self.save_job)
            self.save_job = None
        snapshots = [(text, kind, day, text.get("1.0", "end-1c")) for text, kind, day, _ in self.editors]
        changed = [new[1:] for new, old in zip(snapshots, self.editors) if new[3] != old[3]]
        try:
            if changed:
                self.store.save(changed)
        except sqlite3.Error as exc:
            self.status.set("保存失败，编辑内容仍在。按 Ctrl+S 重试。")
            if interactive:
                messagebox.showerror("保存失败", f"{exc}\n\n编辑内容仍在，可按 Ctrl+S 重试。", parent=self.root)
            return False
        self.editors = snapshots
        if changed or announce or self.status.get() == "保存中…":
            self.status.set("已保存")
        return True

    def close(self):
        if self.flush():
            self.deadline_tooltip.hide()
            self.store.db.close()
            self.root.destroy()


def main():
    # A single editor prevents two windows from overwriting each other's notes.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.CreateMutexW(None, False, "Local\\TodoListMemo.Editor")
    already_open = ctypes.get_last_error() == 183
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if already_open:
            user32 = ctypes.windll.user32
            user32.FindWindowW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
            user32.FindWindowW.restype = ctypes.c_void_p
            user32.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)
            user32.SetForegroundWindow.argtypes = (ctypes.c_void_p,)
            window = user32.FindWindowW(None, "便签 ToList")
            if window:
                user32.ShowWindow(window, 9)
                user32.SetForegroundWindow(window)
            return
        root = tk.Tk()
        try:
            path = Path(os.environ["LOCALAPPDATA"]) / "TodoListMemo" / "notes.sqlite3"
            App(root, Store(path))
        except (OSError, sqlite3.Error, tk.TclError) as exc:
            root.withdraw()
            messagebox.showerror("无法启动便签 ToList", str(exc), parent=root)
            root.destroy()
            return
        root.mainloop()
    finally:
        kernel.CloseHandle(handle)


if __name__ == "__main__":
    main()
