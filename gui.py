#!/usr/bin/env python3
"""
Point-and-click GUI for claude-code-export-import.

Two tabs:
  * Export — pick one, several, or ALL sessions (by title) and save .zip(s).
  * Import — choose one or more .zip files you received and click Import.

If no sessions show up, you can point the app at Claude's folders manually
(bottom of the Export tab).

Standard library only (Tkinter). Reuses the tested core in claude_session_port.py.
Run:  python gui.py     |     packaged:  double-click the app
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


def _safe_name(title, fallback):
    s = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (title or "")).strip()
    return (s[:48] or fallback)


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.pack(fill="both", expand=True)
        self.rows = []          # list_sessions() result, indexed by Treeview iid
        self.import_paths = []   # selected .zip paths
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
        self.log = scrolledtext.ScrolledText(self, height=8, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=False)

        self._refresh_sessions()

    # ----------------------------------------------------------------- export
    def _build_export(self, t):
        ttk.Label(t, text="Pick session(s) to export, then save as .zip to move or share.",
                  font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(t, text="Select one, several (Ctrl/Shift-click), or use Select all. "
                          "Each session becomes its own .zip.",
                  foreground="#555").pack(anchor="w", pady=(0, 8))

        cols = ("title", "folder")
        tv = ttk.Treeview(t, columns=cols, show="headings", height=10, selectmode="extended")
        tv.heading("title", text="Session")
        tv.heading("folder", text="Project folder")
        tv.column("title", width=320, anchor="w")
        tv.column("folder", width=360, anchor="w")
        tv.pack(fill="both", expand=True)
        tv.bind("<Double-1>", lambda e: self._do_export())
        self.tv = tv

        bar = ttk.Frame(t)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="Select all", command=self._select_all).pack(side="left")
        ttk.Button(bar, text="Clear", command=lambda: self.tv.selection_remove(self.tv.selection())).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Refresh", command=self._refresh_sessions).pack(side="left", padx=(6, 0))
        self.count_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.count_var, foreground="#555").pack(side="left", padx=(10, 0))
        self.btn_export = ttk.Button(bar, text="Export selected…", command=self._do_export)
        self.btn_export.pack(side="right")
        tv.bind("<<TreeviewSelect>>", lambda e: self._update_count())

        # Fallback: point at Claude's folders manually if nothing is found
        adv = ttk.LabelFrame(t, text="Not finding your sessions? Point Claude's folders manually", padding=8)
        adv.pack(fill="x", pady=(10, 0))
        self.home_var = tk.StringVar()
        self.store_var = tk.StringVar()
        self._folder_row(adv, "Claude home (your .claude folder):", self.home_var,
                         self._browse_home, hint=core.default_claude_home())
        self._folder_row(adv, "Claude app store (claude-code-sessions):", self.store_var,
                         self._browse_store, hint=(core.candidate_app_store_bases()[0]
                                                   if core.candidate_app_store_bases() else ""))
        ttk.Label(adv, text="Leave empty for auto-detect. After changing, click Refresh.",
                  foreground="#777").pack(anchor="w", pady=(4, 0))

    def _folder_row(self, parent, label, var, browse, hint=""):
        row = ttk.Frame(parent); row.pack(fill="x", pady=2)
        ttk.Label(row, text=label, width=34).pack(side="left")
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=browse).pack(side="left", padx=(6, 0))
        if hint:
            ttk.Label(parent, text=f"     default: {hint}", foreground="#999").pack(anchor="w")

    def _home_or_none(self):
        return self.home_var.get().strip() or None

    def _store_or_none(self):
        return self.store_var.get().strip() or None

    def _browse_home(self):
        p = filedialog.askdirectory(title="Select your .claude folder")
        if p:
            self.home_var.set(os.path.normpath(p))

    def _browse_store(self):
        p = filedialog.askdirectory(title="Select the claude-code-sessions folder")
        if p:
            self.store_var.set(os.path.normpath(p))

    def _select_all(self):
        self.tv.selection_set(self.tv.get_children())
        self._update_count()

    def _update_count(self):
        n = len(self.tv.selection())
        self.count_var.set(f"{n} selected" if n else "")

    def _refresh_sessions(self):
        home = self._home_or_none()
        store = self._store_or_none()
        try:
            self.rows = core.list_sessions(home, store)
        except Exception as e:
            self.rows = []
            self._append_log(f"Could not list sessions: {e}\n")
        self.tv.delete(*self.tv.get_children())
        for i, r in enumerate(self.rows):
            self.tv.insert("", "end", iid=str(i), values=(r["title"], r["cwd"] or ""))
        self._update_count()
        if not self.rows:
            self._append_log("No sessions found. If Claude is installed somewhere unusual, "
                             "point to its folders in the box below and click Refresh.\n")

    def _do_export(self):
        if self._busy:
            return
        sel = self.tv.selection()
        if not sel:
            messagebox.showinfo("Pick a session", "Select one or more sessions first (or Select all).")
            return
        rows = [self.rows[int(i)] for i in sel]
        store = self._store_or_none()
        if len(rows) == 1:
            r = rows[0]
            out = filedialog.asksaveasfilename(
                title="Save session bundle", defaultextension=".zip",
                initialfile=f"claude-session-{_safe_name(r['title'], r['cli_id'][:8])}.zip",
                filetypes=[("Zip bundle", "*.zip")])
            if not out:
                return
            self._busy_run(
                lambda: _run(core.do_export, _EXPORT_DEFAULTS, src=r["jsonl"], out=out, app_store=store),
                "Exported", f"Saved:\n{out}\n\nUse the Import tab on the other machine.")
        else:
            d = filedialog.askdirectory(title="Folder to save the .zip files")
            if not d:
                return
            self._busy_run(
                lambda: self._export_many(rows, d, store),
                "Exported", f"Saved {len(rows)} session(s) into:\n{d}")

    def _export_many(self, rows, folder, store):
        ok_all, logs = True, []
        used = set()
        for r in rows:
            name = _safe_name(r["title"], r["cli_id"][:8])
            base = f"claude-session-{name}"
            out = os.path.join(folder, base + ".zip")
            n = 2
            while out in used or os.path.basename(out) in used:
                out = os.path.join(folder, f"{base}-{n}.zip"); n += 1
            used.add(os.path.basename(out))
            ok, text = _run(core.do_export, _EXPORT_DEFAULTS, src=r["jsonl"], out=out, app_store=store)
            ok_all &= ok
            logs.append(text.strip())
        return ok_all, "\n".join(logs)

    # ----------------------------------------------------------------- import
    def _build_import(self, t):
        ttk.Label(t, text="Import session .zip(s) you received.",
                  font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(t, text="Tip: fully quit the Claude app before importing, then reopen it after.",
                  foreground="#555").pack(anchor="w", pady=(0, 10))

        r1 = ttk.Frame(t); r1.pack(fill="x", pady=4)
        ttk.Label(r1, text="Session .zip(s):", width=22).pack(side="left")
        self.zip_var = tk.StringVar()
        ttk.Entry(r1, textvariable=self.zip_var, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(r1, text="Browse…", command=self._pick_zips).pack(side="left", padx=(6, 0))

        r2 = ttk.Frame(t); r2.pack(fill="x", pady=4)
        ttk.Label(r2, text="Project folder (optional):", width=22).pack(side="left")
        self.cwd_var = tk.StringVar()
        ttk.Entry(r2, textvariable=self.cwd_var).pack(side="left", fill="x", expand=True)
        ttk.Button(r2, text="Browse…", command=self._pick_folder).pack(side="left", padx=(6, 0))
        ttk.Label(t, text="Leave the folder empty to keep each session's original path.",
                  foreground="#555").pack(anchor="w")

        self.btn_import = ttk.Button(t, text="Import", command=self._do_import)
        self.btn_import.pack(anchor="e", pady=(12, 0))

    def _pick_zips(self):
        ps = filedialog.askopenfilenames(title="Choose session .zip file(s)",
                                         filetypes=[("Zip bundle", "*.zip"), ("All files", "*.*")])
        if ps:
            self.import_paths = list(ps)
            self.zip_var.set(ps[0] if len(ps) == 1 else f"{len(ps)} files selected")

    def _pick_folder(self):
        p = filedialog.askdirectory(title="Project folder on this PC")
        if p:
            self.cwd_var.set(os.path.normpath(p))

    def _do_import(self):
        if self._busy:
            return
        paths = [p for p in self.import_paths if os.path.isfile(p)]
        if not paths:
            messagebox.showinfo("Choose a .zip", "Pick one or more session .zip files to import first.")
            return
        cwd = self.cwd_var.get().strip() or None
        self._busy_run(
            lambda: self._import_many(paths, cwd),
            "Imported",
            f"Done — {len(paths)} session(s) imported.\n\n"
            "Now QUIT the Claude app (menu → Quit) and reopen it.\n"
            "They will be in the projects' Recents.")

    def _import_many(self, paths, cwd):
        ok_all, logs = True, []
        for p in paths:
            ok, text = _run(core.do_import, _IMPORT_DEFAULTS, src=p, target_cwd=cwd)
            ok_all &= ok
            logs.append(text.strip())
        return ok_all, "\n".join(logs)

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
    root.geometry("780x620")
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
