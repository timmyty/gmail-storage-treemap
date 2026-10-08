"""Gmail Storage Treemap desktop viewer. Run with Python 3.10+ (standard library only)."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import urllib.parse
import webbrowser

from gmail_storage_core import (Cancelled, authorize, demo_snapshot, group_key, groups,
                          import_mbox, readable_size, scan_gmail, treemap, validate_snapshot)

BASE = Path(__file__).resolve().parent
COLORS = ["#236983", "#69509f", "#217b69", "#9b5b32", "#8e466b", "#466a9c", "#65732e", "#8d5050"]
BG, INK, MUTED = "#f2f5fa", "#18283e", "#53637b"


class StorageTreemap(tk.Tk):
    def __init__(self, demo=False):
        super().__init__()
        self.title("Gmail Storage Treemap 1.1")
        self.geometry("1280x880")
        self.minsize(960, 690)
        self.configure(bg=BG)
        self.option_add("*Font", ("Segoe UI", 10))
        self.snapshot = None
        self.mode = tk.StringVar(value="Sender")
        self.query = tk.StringVar()
        self.minimum = tk.StringVar(value="Any size")
        self.status = tk.StringVar(value="Connect Gmail to scan your mailbox, or explore the demo.")
        self.stats = tk.StringVar(value="No mailbox loaded")
        self.note = tk.StringVar(value="Each tile’s area represents message bytes. Larger tiles use more space.")
        self.breadcrumb = tk.StringVar(value="All mail")
        self.hover = tk.StringVar(value="Select a group to explore its messages.")
        self.stop = threading.Event()
        self.events = queue.Queue()
        self.busy = False
        self.scan_dialog = None
        self.scan_started = 0
        self.scan_phase = tk.StringVar()
        self.scan_detail = tk.StringVar()
        self.scan_counts = tk.StringVar()
        self.scan_elapsed = tk.StringVar()
        self.last_scan_progress = None
        self.elapsed_job = None
        self.selected_group = None
        self.extra_focus = None
        self.canvas_items = []
        self.current_groups = []
        self.current_messages = []
        self.table_rows = {}
        self.page = 0
        self.page_size = 150
        self.redraw_job = None
        self.filter_job = None
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(150, self.poll)
        if demo:
            self.load_demo()

    def build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("TButton", font=("Segoe UI", 10), padding=(11, 7))
        style.configure("Treeview", font=("Segoe UI", 10), rowheight=29, background="white", fieldbackground="white", foreground=INK)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), padding=(7, 8))
        style.map("Treeview", background=[("selected", "#d8e8f4")], foreground=[("selected", INK)])
        header = tk.Frame(self, bg="#16263b", padx=22, pady=15)
        header.pack(fill="x")
        tk.Label(header, text="▦  Gmail Storage Treemap", font=("Segoe UI", 18, "bold"), bg="#16263b", fg="white").pack(side="left")
        tk.Label(header, text="GMAIL STORAGE EXPLORER", font=("Segoe UI", 10), bg="#16263b", fg="#bbcadb").pack(side="left", padx=22)
        self.connect_button = ttk.Button(header, text="Connect Gmail…", command=self.connect)
        self.connect_button.pack(side="right")
        ttk.Button(header, text="Setup guide", command=self.guide).pack(side="right", padx=8)
        actions = ttk.Frame(self, padding=(18, 10))
        actions.pack(fill="x")
        self.load_button = ttk.Button(actions, text="Open snapshot…", command=self.load_snapshot)
        self.load_button.pack(side="left")
        self.save_button = ttk.Button(actions, text="Save snapshot…", command=self.save_snapshot)
        self.save_button.pack(side="left", padx=6)
        self.resume_button = ttk.Button(actions, text="Resume scan…", command=lambda: self.connect(resume=True))
        self.resume_button.pack(side="left", padx=(0, 6))
        self.export_button = ttk.Button(actions, text="Export visible CSV…", command=self.export_csv)
        self.export_button.pack(side="left")
        self.demo_button = ttk.Button(actions, text="Demo", command=self.load_demo)
        self.demo_button.pack(side="left", padx=6)
        self.cancel_button = ttk.Button(actions, text="Stop scan", command=self.stop_scan, state="disabled")
        self.cancel_button.pack(side="right")
        self.progress_button = ttk.Button(actions, text="Scan progress…", command=self.show_scan_dialog, state="disabled")
        self.progress_button.pack(side="right", padx=6)
        summary = ttk.Frame(self, padding=(22, 4))
        summary.pack(fill="x")
        ttk.Label(summary, textvariable=self.stats, font=("Segoe UI", 19, "bold")).pack(anchor="w")
        ttk.Label(summary, textvariable=self.note, foreground=MUTED, wraplength=1190).pack(anchor="w", pady=(3, 9))
        controls = ttk.Frame(self, padding=(22, 4))
        controls.pack(fill="x")
        ttk.Label(controls, text="Group by").pack(side="left")
        combo = ttk.Combobox(controls, textvariable=self.mode, values=["Sender", "Domain", "Year", "Location", "Label set"], state="readonly", width=13)
        combo.pack(side="left", padx=(7, 16))
        combo.bind("<<ComboboxSelected>>", lambda _: self.reset_focus())
        ttk.Label(controls, text="Find").pack(side="left")
        search = ttk.Entry(controls, textvariable=self.query, width=32)
        search.pack(side="left", padx=(7, 16), fill="x", expand=True)
        self.query.trace_add("write", self.schedule_filter)
        ttk.Label(controls, text="Message size").pack(side="left")
        size = ttk.Combobox(controls, textvariable=self.minimum, values=["Any size", "1 MiB+", "5 MiB+", "10 MiB+", "25 MiB+"], state="readonly", width=12)
        size.pack(side="left", padx=(7, 0))
        size.bind("<<ComboboxSelected>>", lambda _: self.reset_focus())
        self.panes = ttk.Panedwindow(self, orient="vertical")
        self.panes.pack(fill="both", expand=True, padx=20, pady=10)
        upper = ttk.Panedwindow(self.panes, orient="horizontal")
        self.panes.add(upper, weight=3)
        left = ttk.Frame(upper)
        upper.add(left, weight=1)
        ttk.Label(left, text="Storage groups", font=("Segoe UI", 11, "bold"), padding=(0, 7)).pack(anchor="w")
        self.tree = ttk.Treeview(left, columns=("bytes", "count"), show="tree headings", selectmode="browse", height=8)
        self.tree.heading("#0", text="Group")
        self.tree.heading("bytes", text="Size ↓")
        self.tree.heading("count", text="Messages")
        self.tree.column("#0", width=220, minwidth=100)
        self.tree.column("bytes", width=105, stretch=False, anchor="e")
        self.tree.column("count", width=85, stretch=False, anchor="e")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.select_group)
        right = ttk.Frame(upper, padding=(12, 0, 0, 0))
        upper.add(right, weight=2)
        navigation = ttk.Frame(right)
        navigation.pack(fill="x")
        ttk.Button(navigation, text="All mail", command=self.reset_focus).pack(side="left", pady=(0, 5))
        ttk.Label(navigation, textvariable=self.breadcrumb, padding=(10, 0), foreground=MUTED).pack(side="left", fill="x", expand=True)
        self.canvas = tk.Canvas(right, bg="#e1e7ef", highlightthickness=0, height=345)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.schedule_redraw)
        self.canvas.bind("<Button-1>", self.tile_click)
        self.canvas.bind("<Motion>", self.tile_hover)
        ttk.Label(right, textvariable=self.hover, foreground=MUTED, wraplength=770).pack(fill="x", pady=(5, 0))
        lower = ttk.Frame(self.panes)
        self.panes.add(lower, weight=2)
        row = ttk.Frame(lower)
        row.pack(fill="x", pady=(8, 6))
        self.list_caption = ttk.Label(row, text="Largest messages", font=("Segoe UI", 11, "bold"))
        self.list_caption.pack(side="left")
        ttk.Button(row, text="Open selected in Gmail", command=self.open_message).pack(side="right")
        self.messages_tree = ttk.Treeview(lower, columns=("sender", "subject", "date", "size"), show="headings", height=7, selectmode="browse")
        for name, title, width, stretch, anchor in [("sender", "Sender", 250, True, "w"), ("subject", "Subject", 470, True, "w"),
                                                    ("date", "Date (UTC for Gmail)", 155, False, "w"), ("size", "Size ↓", 115, False, "e")]:
            self.messages_tree.heading(name, text=title)
            self.messages_tree.column(name, width=width, minwidth=80, stretch=stretch, anchor=anchor)
        list_scroll = ttk.Scrollbar(lower, orient="vertical", command=self.messages_tree.yview)
        self.messages_tree.configure(yscrollcommand=list_scroll.set)
        list_scroll.pack(side="right", fill="y")
        self.messages_tree.pack(fill="both", expand=True)
        self.messages_tree.bind("<Double-1>", lambda _: self.open_message())
        self.messages_tree.bind("<Return>", lambda _: self.open_message())
        paging = ttk.Frame(self, padding=(22, 0, 22, 6))
        paging.pack(fill="x")
        self.page_text = tk.StringVar()
        ttk.Label(paging, textvariable=self.page_text, foreground=MUTED).pack(side="left")
        ttk.Button(paging, text="Next", command=lambda: self.change_page(1)).pack(side="right")
        ttk.Button(paging, text="Previous", command=lambda: self.change_page(-1)).pack(side="right", padx=6)
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x")
        ttk.Label(self, textvariable=self.status, padding=(20, 9), wraplength=1200).pack(fill="x")
        self.update_actions()
        self.after(200, self.draw)

    def guide(self):
        webbrowser.open((BASE / "Setup.html").as_uri())

    def update_actions(self):
        for button in (self.connect_button, self.load_button, self.demo_button):
            button.configure(state="disabled" if self.busy else "normal")
        for button in (self.save_button, self.export_button):
            button.configure(state="normal" if self.snapshot and not self.busy else "disabled")
        self.cancel_button.configure(state="normal" if self.busy else "disabled")
        self.progress_button.configure(state="normal" if self.busy else "disabled")
        resumable = (self.snapshot and self.snapshot.get("source") == "Gmail size estimates"
                     and not self.snapshot.get("complete") and not self.busy)
        self.resume_button.configure(state="normal" if resumable else "disabled")

    def connect(self, resume=False):
        filename = filedialog.askopenfilename(title="Select your Google Desktop app OAuth client JSON", filetypes=[("Google OAuth client", "*.json")])
        if not filename:
            return
        previous = self.snapshot if resume else None
        def work():
            client = authorize(filename, self.stop, self.report)
            return scan_gmail(client, self.stop, self.report, self.partial,
                              resume=previous, scan_progress=self.report_scan)
        self.start_work(work)

    def report(self, text):
        self.events.put(("status", text))

    def report_scan(self, data):
        self.events.put(("scan_progress", data))

    def show_scan_dialog(self):
        if self.scan_dialog is not None and self.scan_dialog.winfo_exists():
            self.scan_dialog.deiconify()
            self.scan_dialog.lift()
            return
        dialog = self.scan_dialog = tk.Toplevel(self)
        dialog.title("Gmail Storage Treemap — Scan progress")
        dialog.transient(self)
        dialog.geometry("590x330")
        dialog.minsize(510, 300)
        dialog.configure(bg=BG)
        dialog.protocol("WM_DELETE_WINDOW", dialog.withdraw)
        frame = ttk.Frame(dialog, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, textvariable=self.scan_phase, font=("Segoe UI", 16, "bold"), wraplength=535).pack(anchor="w", pady=(0, 12))
        ttk.Label(frame, textvariable=self.scan_detail, wraplength=535).pack(anchor="w", fill="x", pady=(0, 14))
        self.scan_bar = ttk.Progressbar(frame, mode="indeterminate", maximum=100)
        self.scan_bar.pack(fill="x", pady=(0, 10))
        self.scan_bar.start(15)
        ttk.Label(frame, textvariable=self.scan_counts, wraplength=535).pack(anchor="w")
        ttk.Label(frame, textvariable=self.scan_elapsed, foreground=MUTED).pack(anchor="w", pady=(8, 14))
        buttons = ttk.Frame(frame)
        buttons.pack(side="bottom", fill="x")
        self.dialog_stop = ttk.Button(buttons, text="Stop scan", command=self.stop_scan)
        self.dialog_stop.pack(side="left")
        self.dialog_hide = ttk.Button(buttons, text="Hide", command=dialog.withdraw)
        self.dialog_hide.pack(side="right")
        self.tick_scan_elapsed()

    def tick_scan_elapsed(self):
        if not self.scan_dialog or not self.scan_dialog.winfo_exists():
            return
        if self.busy:
            elapsed = int(time.monotonic() - self.scan_started)
            hours, remainder = divmod(elapsed, 3600)
            minutes, seconds = divmod(remainder, 60)
            self.scan_elapsed.set(f"Elapsed {hours:d}:{minutes:02d}:{seconds:02d} · You can stop and keep partial results.")
            self.elapsed_job = self.after(1000, self.tick_scan_elapsed)

    def update_scan_progress(self, data):
        self.last_scan_progress = data
        self.scan_phase.set(data["title"])
        if data["phase"] == "listing":
            self.scan_counts.set(f"{data['listed']:,} messages found · determining the total…")
            return
        self.scan_bar.stop()
        self.scan_bar.configure(mode="determinate")
        total, completed = data["total"], data["completed"]
        percent = completed / total * 100 if total else 100
        self.scan_bar["value"] = percent
        counts = f"{completed:,} of {total:,} processed · {percent:.1f}%\n{readable_size(data['bytes'])} collected · {data['failed']:,} unavailable"
        if data.get("reused"):
            counts += f" · {data['reused']:,} reused from snapshot"
        self.scan_counts.set(counts)
        self.scan_detail.set("Reading size estimates and headers. No email bodies or attachments are downloaded.")

    def finish_scan_dialog(self, kind):
        if self.scan_dialog is None:
            return
        self.scan_bar.stop()
        if self.elapsed_job:
            self.after_cancel(self.elapsed_job)
            self.elapsed_job = None
        self.dialog_stop.configure(state="disabled")
        self.dialog_hide.configure(text="Close")
        complete = kind == "done" and self.snapshot and self.snapshot.get("complete")
        self.scan_phase.set("Scan complete" if complete else "Scan stopped")
        self.scan_detail.set(self.status.get())
        if complete:
            self.scan_bar.configure(mode="determinate")
            self.scan_bar["value"] = 100
        elapsed = int(time.monotonic() - self.scan_started)
        self.scan_elapsed.set(f"Elapsed {elapsed // 3600:d}:{elapsed // 60 % 60:02d}:{elapsed % 60:02d}")

    def partial(self, data):
        copy = dict(data)
        copy["messages"] = list(data["messages"])
        self.events.put(("partial", copy))

    def start_work(self, function):
        self.busy = True
        self.stop.clear()
        self.pending = None
        self.last_scan_progress = None
        self.scan_started = time.monotonic()
        self.scan_phase.set("Preparing scan")
        self.scan_detail.set("Starting connection. Google sign-in will open in your browser.")
        self.scan_counts.set("Waiting for mailbox access…")
        if self.elapsed_job:
            self.after_cancel(self.elapsed_job)
            self.elapsed_job = None
        if self.scan_dialog is not None:
            self.scan_dialog.destroy()
            self.scan_dialog = None
        self.show_scan_dialog()
        self.progress.start(15)
        self.update_actions()
        self.status.set("Preparing connection…")
        def run():
            try:
                self.events.put(("done", function()))
            except Cancelled:
                self.events.put(("cancelled", None))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def stop_scan(self):
        self.stop.set()
        self.status.set("Stopping after current requests… partial results will remain available.")

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.set(value)
                    self.scan_detail.set(value)
                    if value.startswith("Gmail quota pause"):
                        self.scan_phase.set("Waiting for Gmail quota")
                    elif "Google sign-in" in value:
                        self.scan_phase.set("Waiting for Google sign-in")
                elif kind == "scan_progress":
                    self.update_scan_progress(value)
                elif kind == "partial":
                    self.pending = value
                else:
                    self.busy = False
                    self.progress.stop()
                    if kind == "done":
                        self.set_snapshot(value)
                    elif self.pending:
                        self.set_snapshot(self.pending)
                    if kind == "error":
                        self.status.set("Scan stopped: " + value)
                        messagebox.showerror("Gmail Storage Treemap", value + "\n\nAny collected results are marked partial. Save a snapshot to keep them, then use Resume scan to continue.")
                    elif kind == "cancelled":
                        self.status.set("Connection cancelled.")
                    self.update_actions()
                    self.finish_scan_dialog(kind)
        except queue.Empty:
            pass
        self.after(150, self.poll)

    def set_snapshot(self, data):
        self.snapshot = validate_snapshot(data)
        self.query.set("")
        self.minimum.set("Any size")
        self.reset_focus()
        self.update_actions()
        complete = data.get("complete", False)
        self.status.set("Scan complete. Select a group or tile to explore." if complete else
                        f"PARTIAL SCAN — {data.get('failed', 0):,} messages unavailable; totals cover collected messages only. Save a snapshot, or use Resume scan to continue.")

    def load_demo(self):
        if not self.busy:
            self.set_snapshot(demo_snapshot())
            self.status.set("DEMO — fictional messages. Connect Gmail to see your own storage.")

    def load_snapshot(self):
        filename = filedialog.askopenfilename(title="Open Gmail Storage Treemap snapshot", filetypes=[("Gmail Storage Treemap snapshot", "*.json"), ("Gmail Takeout MBOX", "*.mbox")])
        if not filename:
            return
        if filename.lower().endswith(".mbox"):
            self.start_work(lambda: import_mbox(filename, self.stop, self.report))
            return
        try:
            with open(filename, encoding="utf-8") as stream:
                self.set_snapshot(json.load(stream))
        except Exception as exc:
            messagebox.showerror("Cannot load snapshot", str(exc))

    def save_snapshot(self):
        if not self.snapshot:
            return
        filename = filedialog.asksaveasfilename(title="Save local mailbox metadata", defaultextension=".json", initialfile="gmail-storage-snapshot.json", filetypes=[("Gmail Storage Treemap snapshot", "*.json")])
        if filename:
            try:
                with open(filename, "w", encoding="utf-8") as stream:
                    json.dump(self.snapshot, stream, ensure_ascii=False)
                self.status.set("Snapshot saved locally. It contains message metadata, never sign-in tokens.")
            except OSError as exc:
                messagebox.showerror("Cannot save snapshot", str(exc))

    def export_csv(self):
        filename = filedialog.asksaveasfilename(title="Export all messages in current view", defaultextension=".csv", initialfile="gmail-storage-messages.csv", filetypes=[("CSV", "*.csv")])
        if not filename:
            return
        def safe_cell(value):
            value = str(value)
            return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value
        try:
            with open(filename, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(["Sender", "Subject", "Date", "Bytes", "Labels"])
                for row in self.current_messages:
                    writer.writerow([safe_cell(row["sender"]), safe_cell(row["subject"]), row["date"], row["size"], safe_cell("; ".join(row.get("labels", [])))])
            self.status.set(f"Exported {len(self.current_messages):,} messages from this view.")
        except OSError as exc:
            messagebox.showerror("Cannot export", str(exc))

    def schedule_filter(self, *_):
        if self.filter_job:
            self.after_cancel(self.filter_job)
        self.filter_job = self.after(250, self.reset_focus)

    def reset_focus(self):
        self.selected_group, self.extra_focus, self.page = None, None, 0
        self.refresh()

    def filtered(self):
        if not self.snapshot:
            return []
        query = self.query.get().strip().casefold()
        minimum = 0 if self.minimum.get() == "Any size" else int(self.minimum.get().split()[0]) * 1024 * 1024
        return [m for m in self.snapshot["messages"] if m["size"] >= minimum and
                (not query or query in (m["sender"] + " " + m["subject"] + " " + " ".join(m.get("labels", []))).casefold())]

    def refresh(self):
        messages = self.filtered()
        self.current_groups = groups(messages, self.mode.get())
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.current_groups):
            self.tree.insert("", "end", iid=str(index), text=item["name"], values=(readable_size(item["size"]), f"{len(item['messages']):,}"))
        if self.selected_group is not None:
            messages = [m for m in messages if group_key(m, self.mode.get()) == self.selected_group]
        if self.extra_focus is not None:
            messages = [m for m in messages if m["id"] in self.extra_focus]
        self.current_messages = sorted(messages, key=lambda m: (-m["size"], m["id"]))
        self.update_summary()
        self.render_table()
        self.draw()

    def update_summary(self):
        total = sum(m["size"] for m in self.current_messages)
        count = len(self.current_messages)
        if self.snapshot:
            demo = str(self.snapshot.get("source", "")).startswith("DEMO")
            prefix = "DEMO · " if demo else ("PARTIAL · " if not self.snapshot.get("complete") else "")
            self.stats.set(f"{prefix}{readable_size(total)}    ·    {count:,} messages")
            source = self.snapshot.get("source", "Message bytes")
            account = self.snapshot.get("account", "")
            self.note.set(f"{account + ' · ' if account else ''}{source}. Totals may differ from Google’s storage quota. Each message is counted once.")
            if self.snapshot.get("resumedFrom"):
                self.note.set(self.note.get() + " Resumed entries keep their previously scanned labels and sizes.")
        self.breadcrumb.set("All mail" + ("  /  " + self.selected_group[:65] if self.selected_group is not None else "") + ("  /  Other messages" if self.extra_focus is not None else ""))

    def select_group(self, _=None):
        selected = self.tree.selection()
        if not selected:
            return
        index = int(selected[0])
        if index >= len(self.current_groups):
            return
        item = self.current_groups[index]
        self.selected_group, self.extra_focus, self.page = item["name"], None, 0
        self.current_messages = sorted(item["messages"], key=lambda m: (-m["size"], m["id"]))
        self.update_summary()
        self.render_table()
        self.draw()

    def change_page(self, direction):
        maxpage = max(0, (len(self.current_messages) - 1) // self.page_size)
        self.page = max(0, min(maxpage, self.page + direction))
        self.render_table()

    def render_table(self):
        self.messages_tree.delete(*self.messages_tree.get_children())
        self.table_rows = {}
        start = self.page * self.page_size
        rows = self.current_messages[start:start + self.page_size]
        for i, row in enumerate(rows):
            key = str(i)
            self.table_rows[key] = row
            self.messages_tree.insert("", "end", iid=key, values=(row["sender"], row["subject"], row["date"], readable_size(row["size"])))
        self.page_text.set(f"{start + 1 if rows else 0:,}–{start + len(rows):,} of {len(self.current_messages):,} messages · sorted by size")
        self.list_caption.configure(text="Largest messages" if self.selected_group is None else "Messages in selected group")

    def schedule_redraw(self, _=None):
        if self.redraw_job:
            self.after_cancel(self.redraw_job)
        self.redraw_job = self.after(80, self.draw)

    def draw(self):
        self.canvas.delete("all")
        self.canvas_items = []
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 5:
            return
        if not self.current_messages:
            text = "Connect Gmail to map your storage\n\nOr choose Demo to explore the viewer." if not self.snapshot else "No messages match these filters."
            self.canvas.create_text(width / 2, height / 2, text=text, fill=MUTED, font=("Segoe UI", 13), justify="center", width=max(200, width - 60))
            return
        if self.selected_group is None and self.extra_focus is None:
            items = self.current_groups
        else:
            items = [{"name": m["subject"], "size": m["size"], "messages": [m], "message": m} for m in self.current_messages]
        if len(items) > 160:
            others = [m for item in items[160:] for m in item["messages"]]
            items = items[:160] + [{"name": f"Other · {len(others):,} messages", "size": sum(m["size"] for m in others), "messages": others, "other": True}]
        total = sum(i["size"] for i in items)
        for item, x, y, w, h in treemap(items, 0, 0, width, height):
            key = item.get("message", {}).get("sender", item["name"])
            color = COLORS[int(hashlib.md5(key.encode()).hexdigest()[:8], 16) % len(COLORS)]
            self.canvas.create_rectangle(x + 1, y + 1, x + w - 1, y + h - 1, fill=color, outline="")
            if w > 70 and h > 41:
                limit = max(6, int((w - 16) / 7.5))
                name = item["name"].replace("\n", " ")
                name = name if len(name) <= limit else name[:limit - 1] + "…"
                self.canvas.create_text(x + 9, y + 10, text=name, anchor="nw", fill="white", font=("Segoe UI", 10, "bold"))
                label = readable_size(item["size"])
                if h > 67 and w > 120:
                    label += f"  ·  {item['size'] / total:.1%}"
                self.canvas.create_text(x + 9, y + 30, text=label, anchor="nw", fill="#eef4fc", font=("Segoe UI", 10))
            self.canvas_items.append((item, x, y, w, h))

    def hit(self, event):
        for item, x, y, w, h in self.canvas_items:
            if x <= event.x < x + w and y <= event.y < y + h:
                return item

    def tile_hover(self, event):
        item = self.hit(event)
        if item:
            name = item["name"].replace("\n", " ")
            self.hover.set(f"{name[:95]}  ·  {readable_size(item['size'])}  ·  {len(item['messages']):,} messages")

    def tile_click(self, event):
        item = self.hit(event)
        if not item:
            return
        if item.get("other"):
            self.extra_focus = {m["id"] for m in item["messages"]}
            self.page = 0
            self.refresh()
        elif "message" in item:
            row = item["message"]
            index = next((i for i, m in enumerate(self.current_messages) if m["id"] == row["id"]), 0)
            self.page = index // self.page_size
            self.render_table()
            key = str(index % self.page_size)
            self.messages_tree.selection_set(key)
            self.messages_tree.see(key)
            self.status.set(row["subject"] + " · " + row["sender"])
        else:
            index = next(i for i, group in enumerate(self.current_groups) if group["name"] == item["name"])
            self.tree.selection_set(str(index))
            self.tree.see(str(index))

    def open_message(self):
        selected = self.messages_tree.selection()
        if not selected:
            self.status.set("Select a message in the list first.")
            return
        row = self.table_rows[selected[0]]
        if row["id"].startswith("demo-"):
            self.status.set("Demo messages are fictional. Connect Gmail to open real messages.")
            return
        account = self.snapshot.get("account", "")
        base = "https://mail.google.com/mail/u/" + ("?authuser=" + urllib.parse.quote(account, safe="") if account else "0/")
        if row.get("messageId"):
            search = "in:anywhere rfc822msgid:" + row["messageId"].strip("<>")
            url = base + "#search/" + urllib.parse.quote(search, safe="")
        elif row.get("threadId"):
            url = base + "#all/" + urllib.parse.quote(row["threadId"], safe="")
        else:
            self.status.set("This exported message has no Gmail link identifier.")
            return
        webbrowser.open(url)

    def close(self):
        self.stop.set()
        self.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gmail Storage Treemap — local Gmail storage viewer")
    parser.add_argument("--demo", action="store_true", help="Open with clearly labeled fictional data")
    args = parser.parse_args()
    StorageTreemap(demo=args.demo).mainloop()
