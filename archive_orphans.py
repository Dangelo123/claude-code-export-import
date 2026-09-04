#!/usr/bin/env python3
"""
Archive the sidebar entries whose transcript is already gone.

A session's record outlives the 30-day prune that deletes its transcript, so the
list accumulates entries that open empty: 49 out of 129 on the install this
script was written for. Archiving takes them off the list without deleting
anything -- one click on "Unarchive" puts them back.

## What this script deliberately does NOT do

Pinning and grouping look like record state too, but they are not. The app reads
both from IndexedDB; the `local_*.json` file is a mirror it writes, never reads.
Measured: after writing `isStarred: false` into 13 records and building 4 groups
with 76 assignments in Local Storage, the app's own counter still read
`code.pinned: 31` and showed only the group created through the interface.
`isArchived` in the record, on the other hand, IS honoured -- which is why this
script exists at all.

IndexedDB will not be written from outside either: it uses Chromium's `idb_cmp1`
comparator, which common leveldb libraries refuse to open.

For pinning and grouping the way through is to organise once in the interface:
faithful mode copies IndexedDB byte for byte, so that survives the migration
intact (verified with 32 pinned sessions arriving in the right order).

Close the app before running this.
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claude_session_port as csp          # noqa: E402


def archive(claude_home=None, app_store=None, dry=False):
    base = next((b for b in csp.candidate_app_store_bases(app_store)
                 if os.path.isdir(b)), None)
    if not base:
        sys.exit('[error] could not find claude-code-sessions')

    home = csp.default_claude_home(claude_home)
    alive = {os.path.splitext(os.path.basename(f))[0]
             for f in glob.glob(os.path.join(home, 'projects', '*', '*.jsonl'))}

    total = n = already = with_transcript = 0
    orphans = []
    for f in sorted(glob.glob(os.path.join(base, '**', 'local_*.json'),
                              recursive=True)):
        try:
            o = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue                      # unreadable at the source: leave it
        total += 1
        if o.get('isArchived'):
            already += 1
            continue
        if o.get('cliSessionId') in alive:
            with_transcript += 1
            continue
        orphans.append(o.get('title') or os.path.basename(f))
        n += 1
        if not dry:
            o['isArchived'] = True
            with open(f, 'w', encoding='utf-8', newline='\n') as fh:
                json.dump(o, fh, ensure_ascii=False, indent=2)

    print("  records              : %d" % total)
    print("  with transcript      : %d" % with_transcript)
    print("  already archived     : %d" % already)
    print("  archived now%s: %d" % ('  (dry-run)' if dry else '         ', n))
    for t in orphans[:15]:
        print("     %s" % t[:66])
    if len(orphans) > 15:
        print("     ... and %d more" % (len(orphans) - 15))
    return n


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--claude-home', default=None)
    ap.add_argument('--app-store', default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    archive(args.claude_home, args.app_store, args.dry_run)


if __name__ == '__main__':
    main()
