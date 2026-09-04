#!/usr/bin/env python3
"""
Tests for the separator-aware path rewriter (Windows -> POSIX).

Each case builds a real JSON line, runs it through the rewriter, parses it back
and compares the *decoded values* -- comparing the serialized JSON would be
wrong, since a legitimate regex like \\d survives as \\\\d once re-encoded.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import batch  # noqa: E402

MAP = {
    r"D:\AcmeWorkspace": "/home/user/cowork",
    r"D:\AcmeWorkspace\.claude\worktrees\cranky-shannon-33fb8e":
        "/home/user/cowork/.claude/worktrees/cranky-shannon-33fb8e",
    r"C:\temp\payment-gateway": "/home/user/pg",
}

rw = batch.build_rewriter(MAP, to_posix=True)
fails = []


def check(name, src_obj, expected):
    """Round-trip a JSON line and compare decoded values field by field."""
    out = rw(json.dumps(src_obj, ensure_ascii=False))
    try:
        back = json.loads(out)
    except Exception as e:
        fails.append("%s: produced INVALID JSON (%s)\n     %s" % (name, e, out))
        print("  %-42s FAIL (invalid json)" % name)
        return
    bad = []
    for k, want in expected.items():
        got = back.get(k)
        if got != want:
            bad.append("      %s\n        got:  %r\n        want: %r" % (k, got, want))
    if bad:
        fails.append("%s:\n%s" % (name, "\n".join(bad)))
        print("  %-42s FAIL" % name)
    else:
        print("  %-42s ok" % name)


print("rewriter tests (Windows -> POSIX)\n")

check("bare cwd",
      {"cwd": r"D:\AcmeWorkspace"},
      {"cwd": "/home/user/cowork"})

check("sub-path separators flip",
      {"file": r"D:\AcmeWorkspace\src\Core\Foo.cs"},
      {"file": "/home/user/cowork/src/Core/Foo.cs"})

check("longest prefix wins",
      {"cwd": r"D:\AcmeWorkspace\.claude\worktrees\cranky-shannon-33fb8e"},
      {"cwd": "/home/user/cowork/.claude/worktrees/cranky-shannon-33fb8e"})

check("forward-slash source",
      {"p": "D:/AcmeWorkspace/src/app.ts"},
      {"p": "/home/user/cowork/src/app.ts"})

# the important one: rewrite the path, leave unrelated escapes alone
check("regex + literal backslash survive",
      {"cwd": r"D:\AcmeWorkspace",
       "pattern": r"\d+\s*(\w+)",
       "win": "a\\b"},
      {"cwd": "/home/user/cowork",
       "pattern": r"\d+\s*(\w+)",
       "win": "a\\b"})

check("unmapped path untouched",
      {"other": r"E:\Something\Else\file.txt"},
      {"other": r"E:\Something\Else\file.txt"})

check("second root",
      {"cwd": r"C:\temp\payment-gateway\PaymentSvc"},
      {"cwd": "/home/user/pg/PaymentSvc"})

check("path inside prose",
      {"text": r"open D:\AcmeWorkspace\README.md and look"},
      {"text": "open /home/user/cowork/README.md and look"})

check("path followed by quote-delimited text",
      {"a": r"D:\AcmeWorkspace\x.txt", "b": r"D:\AcmeWorkspace"},
      {"a": "/home/user/cowork/x.txt", "b": "/home/user/cowork"})

# Windows -> Windows must NOT flip separators
rw_win = batch.build_rewriter({r"D:\old": r"E:\new"}, to_posix=False)
out = json.loads(rw_win(json.dumps({"cwd": r"D:\old\sub\f.cs"})))
if out["cwd"] != r"E:\new\sub\f.cs":
    fails.append("windows->windows keeps backslashes:\n        got:  %r\n        want: %r"
                 % (out["cwd"], r"E:\new\sub\f.cs"))
    print("  %-42s FAIL" % "windows->windows keeps backslashes")
else:
    print("  %-42s ok" % "windows->windows keeps backslashes")

print()
if fails:
    print("FAILURES (%d):" % len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("all tests passed")

# ---------------------------------------------------------------------------
# PLAIN TEXT rewriter (.md/.txt) -- a different grammar from JSON.
# Regression: the first version used the JSON rewriter here and 105 memory
# files were left with their Windows paths intact.
print("\nplain-text rewriter (.md)\n")

prw = batch.build_plain_rewriter(MAP, to_posix=True)


def checkp(name, src, want):
    got = prw(src)
    if got != want:
        fails.append("%s:\n        got:  %r\n        want: %r" % (name, got, want))
        print("  %-42s FAIL" % name)
    else:
        print("  %-42s ok" % name)


checkp("literal path in markdown",
       r"local folder `D:\AcmeWorkspace`",
       "local folder `/home/user/cowork`")

checkp("literal sub-path",
       r"see D:\AcmeWorkspace\src\Core\Foo.cs now",
       "see /home/user/cowork/src/Core/Foo.cs now")

checkp("longest prefix wins",
       r"D:\AcmeWorkspace\.claude\worktrees\cranky-shannon-33fb8e",
       "/home/user/cowork/.claude/worktrees/cranky-shannon-33fb8e")

checkp("unmapped path left alone",
       r"the repo lives in E:\Other\Place",
       r"the repo lives in E:\Other\Place")

checkp("second root",
       r"- [PG](C:\temp\payment-gateway\README.md) — hook",
       "- [PG](/home/user/pg/README.md) — hook")

checkp("text with no path at all",
       r"no path here, just prose with \n and a regex \d+",
       r"no path here, just prose with \n and a regex \d+")

print()
if fails:
    print("FAILURES (%d):" % len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("all tests passed")
