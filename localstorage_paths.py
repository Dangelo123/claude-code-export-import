#!/usr/bin/env python3
"""
Rewrite the file paths stored in Claude Desktop's Local Storage.

Why this exists
---------------
The sidebar's state -- which sessions are pinned, how they are grouped, the
width -- lives in the app's Local Storage, which is a LevelDB. Copying it is
what makes pinning survive a migration, because `pinnedOrder` holds the
`local_<uuid>` id of each record.

Except Local Storage also holds file paths: there is one
`cc-session-cwd-local_<id>` key per session, plus JSON blobs with escaped paths
(the folder grouping). Copied raw from a Windows machine to a Linux one, the app
asks you to trust a C: or D: disk path that does not exist there.

Rewriting means actually reading the database: the values live inside
snappy-compressed blocks in the .ldb files, so scanning bytes does not reach
them. That depends on `plyvel`, which is optional: without it Local Storage is
still copied (pinning works), but the paths stay as they were.

    Arch/CachyOS:  pacman -S python-plyvel
    Debian/Ubuntu: apt install python3-plyvel
    pip:           pip install plyvel   (needs libleveldb-dev)
"""
import json
import re
import sys

# Chromium's Local Storage values carry a prefix byte saying how the rest is
# encoded: 0 = UTF-16LE, 1 = Latin-1
UTF16, LATIN1 = 0, 1


def available():
    try:
        import plyvel  # noqa: F401
        return True
    except Exception:
        return False


def _decode(v):
    """(text, encoding) or (None, None) when the value is not text."""
    if not v:
        return None, None
    tag, body = v[0], v[1:]
    try:
        if tag == UTF16:
            return body.decode('utf-16-le'), UTF16
        if tag == LATIN1:
            return body.decode('latin-1'), LATIN1
    except Exception:
        pass
    return None, None


def _encode(text, encoding):
    if encoding == UTF16:
        return bytes([UTF16]) + text.encode('utf-16-le')
    return bytes([LATIN1]) + text.encode('latin-1')


def build_rewriter(path_map, to_posix):
    """
    f(text) -> text, swapping mapped prefixes.

    Covers both shapes a path takes in here: raw, in a `cc-session-cwd-*` key,
    and escaped inside a JSON blob (the sidebar grouping). Without the second
    one, grouping keeps pointing at Windows folders.
    """
    BS = chr(92)
    variants = []
    for old in sorted(path_map, key=len, reverse=True):
        new = path_map[old]
        for form in (old, old.replace(BS, BS * 2), old.replace(BS, '/')):
            variants.append((re.compile(re.escape(form)), new))

    # After the prefix swap the tail is still left with Windows separators. Only
    # the tail that follows a freshly written destination is converted, so we do
    # not touch a backslash that belongs to code or to an escape sequence.
    # Inside a regex, a literal backslash is written as two.
    B2 = BS + BS
    tail = '((?:' + B2 + '{1,2}[^' + B2 + '"' + "'" + '<>|*?' + BS + 'r' + BS + 'n]+)+)'
    tails = [(re.compile(re.escape(d) + tail), d) for d in set(path_map.values())]

    def rewrite(text):
        for rx, dest in variants:
            text = rx.sub(lambda m, d=dest: d, text)
        if to_posix:
            for rx, dest in tails:
                text = rx.sub(
                    lambda m, d=dest: d + m.group(1).replace(BS * 2, '/').replace(BS, '/'),
                    text)
        return text

    return rewrite


def rewrite_db(leveldb_dir, path_map, to_posix=True, dry=False):
    """
    Walk the database and rewrite every text value that carries a mapped
    prefix. Returns (keys_examined, keys_changed, changed_key_names).
    """
    import plyvel

    rewrite = build_rewriter(path_map, to_posix)
    db = plyvel.DB(leveldb_dir, create_if_missing=False)
    examined = changed = 0
    changes = []
    try:
        for key, value in db.iterator():
            examined += 1
            text, encoding = _decode(value)
            if text is None:
                continue
            new = rewrite(text)
            if new != text:
                changed += 1
                changes.append((key, _encode(new, encoding),
                                key.decode('latin-1', 'replace')))
        if not dry:
            with db.write_batch() as wb:
                for key, value, _ in changes:
                    wb.put(key, value)
    finally:
        db.close()
    return examined, changed, [c[2] for c in changes]


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--leveldb', required=True, help='the Local Storage/leveldb folder')
    ap.add_argument('--path-map', required=True)
    ap.add_argument('--windows-target', action='store_true',
                    help='the destination uses Windows paths (do not convert separators)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not available():
        sys.exit("[error] plyvel is not installed; see the header of this file")

    with open(args.path_map, encoding='utf-8') as fh:
        path_map = json.load(fh)

    n, k, keys = rewrite_db(args.leveldb, path_map, not args.windows_target,
                            args.dry_run)
    print("  keys examined: %d" % n)
    print("  keys changed : %d%s" % (k, '  (dry-run)' if args.dry_run else ''))
    for c in keys[:25]:
        print("    %s" % c[:100])
    if len(keys) > 25:
        print("    ... and %d more" % (len(keys) - 25))


if __name__ == '__main__':
    main()
