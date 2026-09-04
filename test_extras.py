#!/usr/bin/env python3
"""
Tests for extras migration (per-project memory/ + home-level CLAUDE.md).

A transcript is not the whole install. Memories and CLAUDE.md live beside the
sessions, are never referenced by a .jsonl, and quote Windows paths in plain
markdown -- so they need their own transport and their own rewriter. The first
implementation shipped without them and left 187 memory files behind; the
second shipped with the JSON rewriter and left 105 of them with Windows paths.
Both regressions are covered here.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import batch  # noqa: E402
import claude_session_port as csp  # noqa: E402

WIN_ROOT = r"D:\Proj"
WIN_GTD = r"C:\Users\Someone\Documents\GTD_Project"
POSIX_ROOT = "/home/tester/proj"
POSIX_GTD = "/home/tester/gtd"
MAP = {WIN_ROOT: POSIX_ROOT, WIN_GTD: POSIX_GTD}

fails = []


def check(name, cond, detail=""):
    if cond:
        print("  %-46s ok" % name)
    else:
        fails.append("%s%s" % (name, (": " + detail) if detail else ""))
        print("  %-46s FAIL" % name)


tmp = tempfile.mkdtemp(prefix='extras_')
try:
    src_home = os.path.join(tmp, 'src', '.claude')
    dst_home = os.path.join(tmp, 'dst', '.claude')
    bundles = os.path.join(tmp, 'bundles')
    os.makedirs(bundles)

    # ---------------- source: memories + CLAUDE.md + a binary ---------------
    p_root = os.path.join(src_home, 'projects', csp.enc_project(WIN_ROOT), 'memory')
    p_wt = os.path.join(src_home, 'projects',
                        csp.enc_project(WIN_ROOT + r"\.claude\worktrees\wt-1"), 'memory')
    p_gtd = os.path.join(src_home, 'projects', csp.enc_project(WIN_GTD), 'memory')
    for d in (p_root, p_wt, p_gtd):
        os.makedirs(d)

    with open(os.path.join(p_root, 'MEMORY.md'), 'w', encoding='utf-8') as fh:
        fh.write("# Index\n"
                 "- [Setup](setup.md) — the repo lives in %s\\src\n"
                 "- unmapped path: E:\\Other\\Place\n" % WIN_ROOT)
    with open(os.path.join(p_root, 'setup.md'), 'w', encoding='utf-8') as fh:
        fh.write("run in `%s` and see %s\\docs\\a.md\n" % (WIN_ROOT, WIN_ROOT))
    with open(os.path.join(p_wt, 'wt.md'), 'w', encoding='utf-8') as fh:
        fh.write("worktree of %s\n" % WIN_ROOT)
    with open(os.path.join(p_gtd, 'gtd.md'), 'w', encoding='utf-8') as fh:
        fh.write("project in %s\n" % WIN_GTD)
    with open(os.path.join(src_home, 'CLAUDE.md'), 'w', encoding='utf-8') as fh:
        fh.write("global instructions\nmain project: %s\n" % WIN_ROOT)
    # binary: must not be corrupted by the rewrite
    with open(os.path.join(p_root, 'blob.bin'), 'wb') as fh:
        fh.write(bytes(range(256)))

    n = batch.export_extras(src_home, bundles)
    check("export packs every extra", n == 6, "packed %d, expected 6" % n)
    check("_extras.zip created", os.path.isfile(os.path.join(bundles, batch.EXTRAS_ZIP)))

    # ---------------- destination: a file that is already there -------------
    dst_proj = os.path.join(dst_home, 'projects', csp.enc_project(POSIX_ROOT), 'memory')
    os.makedirs(dst_proj)
    keep = os.path.join(dst_proj, 'MEMORY.md')
    with open(keep, 'w', encoding='utf-8') as fh:
        fh.write("I WAS ALREADY HERE\n")

    rw = batch.build_rewriter(MAP, to_posix=True)
    prw = batch.build_plain_rewriter(MAP, to_posix=True)
    slug_map = dict((csp.enc_project(o), csp.enc_project(nn)) for o, nn in MAP.items())

    def remap_slug(s):
        if s in slug_map:
            return slug_map[s]
        for o, nn in sorted(slug_map.items(), key=lambda kv: -len(kv[0])):
            if s.startswith(o):
                return nn + s[len(o):]
        return None

    batch.import_extras(dst_home, bundles, rw, remap_slug, plain_rewrite=prw)

    # ---------------- checks -------------------------------------------------
    def read(*parts):
        p = os.path.join(dst_home, *parts)
        return open(p, encoding='utf-8').read() if os.path.isfile(p) else None

    proj_slug = csp.enc_project(POSIX_ROOT)
    wt_slug = csp.enc_project(POSIX_ROOT + "/.claude/worktrees/wt-1")
    gtd_slug = csp.enc_project(POSIX_GTD)

    setup = read('projects', proj_slug, 'memory', 'setup.md')
    check("memory restored under the remapped slug", setup is not None)
    check("path converted in markdown (plain text)",
          setup is not None and POSIX_ROOT in setup and "D:\\" not in setup,
          repr(setup))

    wt = read('projects', wt_slug, 'memory', 'wt.md')
    check("worktree remapped by prefix", wt is not None and POSIX_ROOT in wt)

    gtd = read('projects', gtd_slug, 'memory', 'gtd.md')
    check("second root remapped", gtd is not None and POSIX_GTD in gtd)

    cl = read('CLAUDE.md')
    check("global CLAUDE.md restored", cl is not None)
    check("CLAUDE.md with the path converted",
          cl is not None and POSIX_ROOT in cl and "D:\\" not in cl)

    # a pre-existing file must not be overwritten
    check("does not overwrite a file that is already there",
          read('projects', proj_slug, 'memory', 'MEMORY.md') == "I WAS ALREADY HERE\n")

    # a path outside the map stays as it is (do not invent a destination)
    idx_src = open(os.path.join(p_root, 'MEMORY.md'), encoding='utf-8').read()
    check("the source kept the unmapped path", "E:\\Other\\Place" in idx_src)

    # binary intact
    blob = os.path.join(dst_home, 'projects', proj_slug, 'memory', 'blob.bin')
    check("binary not corrupted by the rewrite",
          os.path.isfile(blob) and open(blob, 'rb').read() == bytes(range(256)))

    print()
    if fails:
        print("FAILURES (%d):" % len(fails))
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("all tests passed")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
