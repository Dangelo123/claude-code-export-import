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
    r"D:\ClaudeCowork_MeepGreenfield": "/home/fernando/cowork",
    r"D:\ClaudeCowork_MeepGreenfield\.claude\worktrees\cranky-shannon-33fb8e":
        "/home/fernando/cowork/.claude/worktrees/cranky-shannon-33fb8e",
    r"C:\temp\payment-gateway-gh": "/home/fernando/pg",
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
      {"cwd": r"D:\ClaudeCowork_MeepGreenfield"},
      {"cwd": "/home/fernando/cowork"})

check("sub-path separators flip",
      {"file": r"D:\ClaudeCowork_MeepGreenfield\src\Meep\Foo.cs"},
      {"file": "/home/fernando/cowork/src/Meep/Foo.cs"})

check("longest prefix wins",
      {"cwd": r"D:\ClaudeCowork_MeepGreenfield\.claude\worktrees\cranky-shannon-33fb8e"},
      {"cwd": "/home/fernando/cowork/.claude/worktrees/cranky-shannon-33fb8e"})

check("forward-slash source",
      {"p": "D:/ClaudeCowork_MeepGreenfield/src/app.ts"},
      {"p": "/home/fernando/cowork/src/app.ts"})

# the important one: rewrite the path, leave unrelated escapes alone
check("regex + literal backslash survive",
      {"cwd": r"D:\ClaudeCowork_MeepGreenfield",
       "pattern": r"\d+\s*(\w+)",
       "win": "a\\b"},
      {"cwd": "/home/fernando/cowork",
       "pattern": r"\d+\s*(\w+)",
       "win": "a\\b"})

check("unmapped path untouched",
      {"other": r"E:\Something\Else\file.txt"},
      {"other": r"E:\Something\Else\file.txt"})

check("second root",
      {"cwd": r"C:\temp\payment-gateway-gh\MeepPayment"},
      {"cwd": "/home/fernando/pg/MeepPayment"})

check("path inside prose",
      {"text": r"abra D:\ClaudeCowork_MeepGreenfield\README.md e veja"},
      {"text": "abra /home/fernando/cowork/README.md e veja"})

check("path followed by quote-delimited text",
      {"a": r"D:\ClaudeCowork_MeepGreenfield\x.txt", "b": r"D:\ClaudeCowork_MeepGreenfield"},
      {"a": "/home/fernando/cowork/x.txt", "b": "/home/fernando/cowork"})

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
       r"local folder `D:\ClaudeCowork_MeepGreenfield`",
       "local folder `/home/fernando/cowork`")

checkp("literal sub-path",
       r"see D:\ClaudeCowork_MeepGreenfield\src\Meep\Foo.cs now",
       "see /home/fernando/cowork/src/Meep/Foo.cs now")

checkp("longest prefix wins",
       r"D:\ClaudeCowork_MeepGreenfield\.claude\worktrees\cranky-shannon-33fb8e",
       "/home/fernando/cowork/.claude/worktrees/cranky-shannon-33fb8e")

checkp("unmapped path left alone",
       r"the repo lives in E:\Other\Place",
       r"the repo lives in E:\Other\Place")

checkp("second root",
       r"- [PG](C:\temp\payment-gateway-gh\README.md) — hook",
       "- [PG](/home/fernando/pg/README.md) — hook")

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
