#!/usr/bin/env python3
"""
Safety check: run the rewriter over the REAL local transcripts and assert that
every line still parses as JSON afterwards, and that no Windows path from the
mapped roots survives. Read-only -- nothing is written.

Usage:  python check_corpus.py [--limit N]
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import batch  # noqa: E402
import claude_session_port as csp  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--limit', type=int, default=0, help='max sessions to check (0 = all)')
ap.add_argument('--claude-home', default=None)
args = ap.parse_args()

home = csp.default_claude_home(args.claude_home)

# discover the real roots, then build a plausible POSIX mapping
roots = {}
sessions = []
for proj, path, cwd in batch.iter_sessions(home):
    if cwd:
        sessions.append((path, cwd))
        roots[cwd] = roots.get(cwd, 0) + 1

def to_posix_guess(win):
    p = win.replace('\\', '/')
    if len(p) > 2 and p[1] == ':':
        p = '/mnt/' + p[0].lower() + p[2:]
    return p

MAP = dict((r, to_posix_guess(r)) for r in roots)
rw = batch.build_rewriter(MAP, to_posix=True)

if args.limit:
    sessions = sessions[:args.limit]

print("corpus check: %d sessions, %d roots\n" % (len(sessions), len(MAP)))

tot_lines = bad_json = rewritten = leftover = 0
bad_examples = []
leftover_examples = []

for i, (path, cwd) in enumerate(sessions, 1):
    try:
        with open(path, encoding='utf-8', errors='ignore') as fh:
            for lineno, raw in enumerate(fh, 1):
                raw = raw.rstrip('\n')
                if not raw.strip():
                    continue
                tot_lines += 1
                try:
                    json.loads(raw)
                except Exception:
                    continue          # already invalid at source: not our problem
                out = rw(raw)
                if out != raw:
                    rewritten += 1
                try:
                    json.loads(out)
                except Exception as e:
                    bad_json += 1
                    if len(bad_examples) < 3:
                        bad_examples.append("%s:%d  %s" % (os.path.basename(path), lineno, e))
                    continue
                # no mapped Windows root should remain
                for r in MAP:
                    if csp.jesc(r) in out:
                        leftover += 1
                        if len(leftover_examples) < 3:
                            leftover_examples.append("%s:%d  %s" % (os.path.basename(path), lineno, r))
                        break
    except Exception as e:
        print("[warn] %s: %s" % (os.path.basename(path), e))
    if i % 25 == 0:
        print("   ... %d/%d sessions, %d lines" % (i, len(sessions), tot_lines))

print("\n--- result ---")
print("lines checked      : %d" % tot_lines)
print("lines rewritten    : %d" % rewritten)
print("INVALID JSON after : %d" % bad_json)
print("leftover win paths : %d" % leftover)
for e in bad_examples:
    print("   bad:", e)
for e in leftover_examples:
    print("   left:", e)

sys.exit(1 if (bad_json or leftover) else 0)
