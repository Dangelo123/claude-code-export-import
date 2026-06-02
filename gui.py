#!/usr/bin/env python3
"""
Point-and-click GUI for claude-code-export-import.

Two tabs:
  * Export — pick a session from a list (by title), save a .zip to share/move.
  * Import — choose a .zip you received, click Import. Everything else is automatic.

Standard library only (Tkinter). Reuses the tested core in claude_session_port.py.
Run:  python gui.py     |     packaged:  double-click the .exe
"""
import io
import os
import sys
import contextlib
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

import claude_session_port as core


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


_EXPORT_DEFAULTS = dict(src=None, out=None, app_store=None, dry_run=False)
_IMPORT_DEFAULTS = dict(
    src=None, target_cwd=None, claude_home=None, keep_id=False, keep_paths=False,
    git_branch=None, title_suffix=None, title=None, app_store=None,
    no_app_index=False, bump_version=False, no_sidecar=False, with_history=False,
    dry_run=False,
)


def _run(fn, defaults, **kw):
    """Run a core handler, capturing its output. Returns (ok, log_text)."""
    args = _Args(**{**defaults, **kw})
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            fn(args)
        return True, buf.getvalue()
    except SystemExit as e:  # core uses sys.exit(msg) for user-facing errors
        out = buf.getvalue()
        if e.code not in (0, None):
            out += f"\n{e.code}"
        return False, out
    except Exception as e:
        return False, buf.getvalue() + f"\nERROR: {e}"


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.pack(fill="both", expand=True)
        self.rows = []  # list_sessions() result, indexed by Treeview iid
        self._busy = False

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.tab_export = ttk.Frame(nb, padding=10)
        self.tab_import = ttk.Frame(nb, padding=10)
        nb.add(self.tab_export, text="  Export a session  ")
        nb.add(self.tab_import, text="  Import a session  ")
        self._build_export(self.tab_export)
        self._build_import(self.tab_import)

        ttk.Label(self, text="Log:").pack(anchor="w", pady=(8, 0))
        self.log = scrolledtext.ScrolledText(self, height=9, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=False)

        self._refresh_sessions()

    # ----------------------------------------------------------------- export
    def _build_export(self, t):
        ttk.Label(t, text="Pick a session and save it as a .zip to move or share it.",
                  font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(t, text="The title and folder travel with it; on the other side just Import the .zip.",
                  foreground="#555").pack(anchor="w", pady=(0, 8))

        cols = ("title", "folder")
        tv = ttk.Treeview(t, columns=cols, show="headings", height=11, selectmode="browse")
        tv.heading("title", text="Session")
        tv.heading("folder", text="Project folder")
        tv.column("title", width=320, anchor="w")
        tv.column("folder", width=360, anchor="w")
        tv.pack(fill="both", expand=True)
        tv.bind("<Double-1>", lambda e: self._do_export())
        self.tv = tv

        bar = ttk.Frame(t)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="Refresh", command=self._refresh_sessions).pack(side="left")
        self.btn_export = ttk.Button(bar, text="Export selected…", command=self._do_export)
        self.btn_export.pack(side="right")

    def _refresh_sessions(self):
        try:
            self.rows = core.list_sessions()
        except Exception as e:
            self.rows = []
            self._append_log(f"Could not list sessions: {e}\n")
        self.tv.delete(*self.tv.get_children())
        for i, r in enumerate(self.rows):
            self.tv.insert("", "end", iid=str(i), values=(r["title"], r["cwd"] or ""))
        if not self.rows:
            self._append_log("No sessions found. Open the Claude desktop app and use a "
                             "Code session at least once.\n")

    def _do_export(self):
        if self._busy:
            return
        sel = self.tv.selection()
        if not sel:
            messagebox.showinfo("Pick a session", "Select a session in the list first.")
            return
        row = self.rows[int(sel[0])]
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in row["title"])[:40].strip()
        out = filedialog.asksaveasfilename(
            title="Save session bundle",
            defaultextension=".zip",
            initialfile=f"claude-session-{safe or row['cli_id'][:8]}.zip",
            filetypes=[("Zip bundle", "*.zip")],
        )
        if not out:
            return
        self._busy_run(
            lambda: _run(core.do_export, _EXPORT_DEFAULTS, src=row["jsonl"], out=out),
            ok_title="Exported",
            ok_msg=f"Saved:\n{out}\n\nSend this .zip to the other machine and use the Import tab there.",
        )

    # ----------------------------------------------------------------- import
    def _build_import(self, t):
        ttk.Label(t, text="Import a session .zip you received.",
                  font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(t, text="Tip: fully quit the Claude app before importing, then reopen it after.",
                  foreground="#555").pack(anchor="w", pady=(0, 10))

        r1 = ttk.Frame(t); r1.pack(fill="x", pady=4)
        ttk.Label(r1, text="Session .zip:", width=22).pack(side="left")
        self.zip_var = tk.StringVar()
        ttk.Entry(r1, textvariable=self.zip_var).pack(side="left", fill="x", expand=True)
        ttk.Button(r1, text="Browse…", command=self._pick_zip).pack(side="left", padx=(6, 0))

        r2 = ttk.Frame(t); r2.pack(fill="x", pady=4)
        ttk.Label(r2, text="Project folder (optional):", width=22).pack(side="left")
        self.cwd_var = tk.StringVar()
        ttk.Entry(r2, textvariable=self.cwd_var).pack(side="left", fill="x", expand=True)
        ttk.Button(r2, text="Browse…", command=self._pick_folder).pack(side="left", padx=(6, 0))
        ttk.Label(t, text="Leave the folder empty to keep the original path from the bundle.",
                  foreground="#555").pack(anchor="w")

        self.btn_import = ttk.Button(t, text="Import", command=self._do_import)
        self.btn_import.pack(anchor="e", pady=(12, 0))

    def _pick_zip(self):
        p = filedialog.askopenfilename(title="Choose the session .zip",
                                       filetypes=[("Zip bundle", "*.zip"), ("All files", "*.*")])
        if p:
            self.zip_var.set(p)

    def _pick_folder(self):
        p = filedialog.askdirectory(title="Project folder on this PC")
        if p:
            self.cwd_var.set(os.path.normpath(p))

    def _do_import(self):
        if self._busy:
            return
        src = self.zip_var.get().strip()
        if not src or not os.path.isfile(src):
            messagebox.showinfo("Choose a .zip", "Pick the session .zip to import first.")
            return
        cwd = self.cwd_var.get().strip() or None
        self._busy_run(
            lambda: _run(core.do_import, _IMPORT_DEFAULTS, src=src, target_cwd=cwd),
            ok_title="Imported",
            ok_msg="Done!\n\nNow QUIT the Claude app (menu → Quit) and reopen it.\n"
                   "The session will be in the project's Recents.",
        )

    # ------------------------------------------------------------------- util
    def _busy_run(self, work, ok_title, ok_msg):
        self._set_busy(True)
        self._append_log("\n" + "-" * 60 + "\nWorking…\n")

        def runner():
            ok, text = work()
            self.after(0, lambda: self._done(ok, text, ok_title, ok_msg))

        threading.Thread(target=runner, daemon=True).start()

    def _done(self, ok, text, ok_title, ok_msg):
        self._append_log(text + "\n")
        self._set_busy(False)
        if ok:
            messagebox.showinfo(ok_title, ok_msg)
        else:
            messagebox.showerror("Something went wrong",
                                 "It didn't complete. See the Log at the bottom for details.")

    def _set_busy(self, busy):
        self._busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.btn_export, self.btn_import):
            b.configure(state=state)

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")


def main():
    root = tk.Tk()
    root.title("Claude Code — Export / Import sessions")
    root.geometry("760x560")
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
