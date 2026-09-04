#!/usr/bin/env python3
"""
Smoke test for the Migrate tab.

Tkinter needs a display, so this is skipped when there is none (headless CI on
Linux). Where a display exists it builds the real window, drives the widgets and
checks the wiring: the path-map editor loads rows from a bundle, suggestions
fill only empty fields, and the collected map is what the core would receive.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _skip(why):
    """
    Skip in a way that works under BOTH entry points.

    Run as a script this prints and exits 0. Under `unittest discover` an
    exit(0) at import time surfaces as an ImportError and the whole suite goes
    red on any machine without tkinter -- raising SkipTest reports a skip
    instead.
    """
    if __name__ == '__main__':
        print("SKIP: " + why)
        sys.exit(0)
    raise unittest.SkipTest(why)


try:
    import tkinter as tk
except Exception as e:                                   # noqa: BLE001
    _skip("tkinter unavailable (%s)" % e)

try:
    _root = tk.Tk()
    _root.withdraw()
except Exception as e:                                   # noqa: BLE001
    _skip("no display (%s)" % e)

import gui  # noqa: E402

fails = []


def check(name, cond, detail=""):
    if cond:
        print("  %-46s ok" % name)
    else:
        fails.append("%s%s" % (name, (": " + detail) if detail else ""))
        print("  %-46s FAIL" % name)


tmp = tempfile.mkdtemp(prefix='guimig_')
try:
    app = gui.App(_root)
    check("app builds with the new tab", app.tab_migrate is not None)
    check("the map editor starts empty", app.map_entries == [])

    # a fake bundle, shaped the way export-all would leave one
    bundle = os.path.join(tmp, 'bundle')
    os.makedirs(bundle)
    tmpl = {r"D:\Work\Alpha": "<FILL IN destination path>",
            r"C:\Users\X\Documents\Beta": "<FILL IN destination path>"}
    with open(os.path.join(bundle, 'path-map.template.json'), 'w', encoding='utf-8') as fh:
        json.dump(tmpl, fh)

    app._load_path_map(bundle)
    check("loads one row per source path", len(app.map_entries) == 2,
          "loaded %d" % len(app.map_entries))

    # nothing filled in -> empty map (the button must refuse)
    check("empty map when nothing was typed", app._collect_map() == {})

    # the suggestion fills the blanks
    app._suggest_dests()
    m = app._collect_map()
    check("suggestion fills both fields", len(m) == 2, str(m))
    check("suggestion uses the final folder name",
          any(v.replace("\\", "/").endswith("/Alpha") for v in m.values()), str(m))

    # the suggestion does NOT overwrite what the user typed
    src0, e0 = app.map_entries[0]
    e0.delete(0, "end")
    e0.insert(0, "/home/me/picked-by-hand")
    app._suggest_dests()
    check("suggestion respects a value already typed",
          app._collect_map()[src0] == "/home/me/picked-by-hand")

    # a blank field = project skipped
    src1, e1 = app.map_entries[1]
    e1.delete(0, "end")
    m = app._collect_map()
    check("an empty field leaves the project out", src1 not in m and src0 in m, str(m))

    # a folder with no template should warn, not blow up
    empty = os.path.join(tmp, 'empty')
    os.makedirs(empty)
    app._load_path_map(empty)
    check("a folder with no template does not break", app.map_entries == [])

    # --- the new options ---
    # "reproduce the sidebar" ships ON deliberately: without it the ids change
    # and every pin points at nothing, which was the real migration's defect
    check("reproduce the sidebar ships on", app.mig_faithful.get() is True)
    check("list hidden sessions ships off", app.mig_index_all.get() is False)
    check("carry config ships off", app.mig_with_config.get() is False)

    check("import-all aceita faithful", 'faithful' in gui._IMPORT_ALL_DEFAULTS)
    check("import-all aceita index_all", 'index_all' in gui._IMPORT_ALL_DEFAULTS)
    check("export-all aceita with_config", 'with_config' in gui._EXPORT_ALL_DEFAULTS)

    # o que a GUI monta tem de casar com o que o nucleo le
    import inspect

    import batch
    for fn, padroes in ((batch.do_export_all, gui._EXPORT_ALL_DEFAULTS),
                        (batch.do_import_all, gui._IMPORT_ALL_DEFAULTS)):
        fonte = inspect.getsource(fn)
        faltando = [k for k in padroes if ('args.' + k) not in fonte]
        check("%s usa tudo que a GUI passa" % fn.__name__, not faltando, str(faltando))

    print()
    if fails:
        print("FAILURES (%d):" % len(fails))
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("all tests passed")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        _root.destroy()
    except Exception:
        pass
