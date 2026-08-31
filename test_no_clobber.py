#!/usr/bin/env python3
"""
Proves import-all is additive: sessions already present on the target are
neither deleted nor modified.

Builds a fake target ~/.claude with a pre-existing session, imports a bundle
into the same project folder, then asserts the pre-existing file is untouched
byte for byte and still there.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claude_session_port as csp  # noqa: E402

WIN_CWD = r"D:\FakeProject"
POSIX_CWD = "/home/tester/FakeProject"
MAP = {WIN_CWD: POSIX_CWD}


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


tmp = tempfile.mkdtemp(prefix='nocl_')
try:
    home = os.path.join(tmp, 'claude_home')
    bundles = os.path.join(tmp, 'bundles')
    os.makedirs(bundles)

    # ---- a pre-existing session already on the target, in the SAME project dir
    proj = os.path.join(home, 'projects', csp.enc_project(POSIX_CWD))
    os.makedirs(proj)
    existing = os.path.join(proj, 'aaaaaaaa-1111-2222-3333-444444444444.jsonl')
    with open(existing, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps({"type": "user", "cwd": POSIX_CWD,
                             "sessionId": "aaaaaaaa-1111-2222-3333-444444444444",
                             "note": r"mentions D:\FakeProject on purpose",
                             "message": {"content": "i was here first"}}) + '\n')
    before_hash = sha(existing)
    before_size = os.path.getsize(existing)

    # ---- a bundle to import (source is the Windows path)
    sid = 'bbbbbbbb-5555-6666-7777-888888888888'
    src_jsonl = os.path.join(tmp, sid + '.jsonl')
    with open(src_jsonl, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps({"type": "user", "cwd": WIN_CWD, "sessionId": sid,
                             "f": WIN_CWD + r"\src\a.cs",
                             "message": {"content": "imported one"}}) + '\n')
    zpath = os.path.join(bundles, sid + '.zip')
    with zipfile.ZipFile(zpath, 'w') as z:
        z.write(src_jsonl, arcname=sid + '.jsonl')
        z.writestr('meta.json', json.dumps({"cliSessionId": sid, "cwd": WIN_CWD,
                                            "title": "imported one"}))
    json.dump([{"sessionId": sid, "project": "x", "cwd": WIN_CWD,
                "bundle": os.path.basename(zpath)}],
              open(os.path.join(bundles, 'manifest.json'), 'w'))
    json.dump(MAP, open(os.path.join(tmp, 'map.json'), 'w'))

    # ---- run the real CLI
    r = subprocess.run([sys.executable, os.path.join(HERE, 'batch.py'), 'import-all',
                        '--src', bundles, '--path-map', os.path.join(tmp, 'map.json'),
                        '--claude-home', home, '--no-app-index'],
                       capture_output=True, text=True)
    print(r.stdout[-600:])
    if r.returncode != 0:
        print("STDERR:", r.stderr[-800:])

    fails = []
    if not os.path.isfile(existing):
        fails.append("pre-existing session was DELETED")
    else:
        if sha(existing) != before_hash:
            fails.append("pre-existing session was MODIFIED (hash changed)")
        if os.path.getsize(existing) != before_size:
            fails.append("pre-existing session changed size")
        obj = json.loads(open(existing, encoding='utf-8').readline())
        if obj.get('note') != r"mentions D:\FakeProject on purpose":
            fails.append("pre-existing content was rewritten: %r" % obj.get('note'))

    imported = [f for f in os.listdir(proj)
                if f.endswith('.jsonl') and not f.startswith('aaaaaaaa')]
    if not imported:
        fails.append("imported session did not land in %s" % proj)
    else:
        o = json.loads(open(os.path.join(proj, imported[0]), encoding='utf-8').readline())
        if o.get('cwd') != POSIX_CWD:
            fails.append("imported cwd not remapped: %r" % o.get('cwd'))
        if o.get('f') != POSIX_CWD + "/src/a.cs":
            fails.append("imported path not converted: %r" % o.get('f'))

    print("\nfiles in project dir:", sorted(os.listdir(proj)))
    print()
    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS: pre-existing session intact (same hash), imported session remapped")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
