#!/usr/bin/env python3
"""
Batch export/import for claude-code-export-import.

Adds two commands on top of claude_session_port:

    export-all   walk ~/.claude/projects, bundle every session, emit a path-map template
    import-all   import a whole bundle, remapping cwd prefixes (Windows -> POSIX aware)

Why this exists: the single-session commands take one --target-cwd. Migrating a
whole install means N source roots -> N destination roots, and going from Windows
to Linux additionally requires flipping path separators *inside* the rewritten
paths -- without touching backslashes that belong to code, regexes or escapes.

Standard library only, same as the main script.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claude_session_port as csp  # noqa: E402


# --------------------------------------------------------------- path rewrite
def is_windows_path(p):
    return bool(re.match(r'^[A-Za-z]:[\\/]', p or ''))


def build_plain_rewriter(mapping, to_posix):
    """
    Same idea as build_rewriter, but for PLAIN TEXT (.md, .txt).

    In a transcript the path is JSON-escaped (D:\\\\proj); in markdown it is
    literal (D:\\proj). Using the JSON form here would never match -- that bug
    left 105 memory files with Windows paths on the first run.
    """
    pairs = sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True)
    rules = []
    for old, new in pairs:
        for variant in sorted({old, old.replace('\\', '/')}):
            esc = re.escape(variant)
            # the path's tail: slashes/backslashes and characters common in
            # filenames; stops at a space, a quote or the end of the line
            tail = r'(?P<tail>(?:[\\/][^\s"\'<>|:*?\r\n]+)*)'
            rules.append((re.compile(esc + tail), new))

    def rewrite(text):
        for rx, new in rules:
            def sub(m):
                tail = m.group('tail')
                if to_posix:
                    tail = tail.replace('\\', '/')
                return new + tail
            text = rx.sub(sub, text)
        return text

    return rewrite


def build_rewriter(mapping, to_posix):
    """
    Return f(text) that rewrites every mapped prefix inside JSON text.

    Longest prefix first, so D:\\a\\b wins over D:\\a. When the destination is
    POSIX we also flip separators, but ONLY within the path we just matched --
    a backslash elsewhere on the line (code, regex, escape) is left alone.
    """
    pairs = sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True)
    rules = []
    for old, new in pairs:
        for variant in sorted({old, old.replace('\\', '/')}):
            esc = re.escape(csp.jesc(variant))
            # trailing path chars: escaped backslashes, slashes, and anything
            # that is not a quote, a backslash or a control char
            tail = r'(?P<tail>(?:\\\\|/|[^"\\\x00-\x1f])*)'
            rules.append((re.compile(esc + tail), new))

    def rewrite(text):
        for rx, new in rules:
            def sub(m):
                tail = m.group('tail')
                if to_posix:
                    # inside JSON a literal backslash is stored as \\ -> /
                    tail = tail.replace('\\\\', '/')
                return csp.jesc(new) + tail
            text = rx.sub(sub, text)
        return text

    return rewrite


# ------------------------------------------------------------------ discovery
def iter_sessions(claude_home):
    """Yield (project_dir, jsonl_path, cwd) for every top-level session."""
    root = os.path.join(claude_home, 'projects')
    if not os.path.isdir(root):
        sys.exit("[error] not found: " + root)
    for proj in sorted(os.listdir(root)):
        pdir = os.path.join(root, proj)
        if not os.path.isdir(pdir):
            continue
        for fn in sorted(os.listdir(pdir)):
            if not fn.endswith('.jsonl'):
                continue
            full = os.path.join(pdir, fn)
            lines = []
            try:
                with open(full, encoding='utf-8', errors='ignore') as fh:
                    for i, ln in enumerate(fh):
                        lines.append(ln)
                        if i >= 40:
                            break
            except Exception:
                continue
            yield proj, full, csp.detect_old_cwd(lines)


# ---------------------------------------------------------------- export-all
def do_export_all(args):
    home = csp.default_claude_home(args.claude_home)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    found, cwds, skipped = [], {}, 0
    for proj, path, cwd in iter_sessions(home):
        if cwd is None:
            skipped += 1
            continue
        found.append((proj, path, cwd))
        cwds[cwd] = cwds.get(cwd, 0) + 1

    print("[scan] %d sessions, %d distinct cwd, %d without cwd"
          % (len(found), len(cwds), skipped))

    if args.dry_run:
        for c, n in sorted(cwds.items(), key=lambda kv: -kv[1]):
            print("   %4d  %s" % (n, c))
        return

    manifest = []
    for i, (proj, path, cwd) in enumerate(found, 1):
        sid = os.path.splitext(os.path.basename(path))[0]
        zpath = os.path.join(out_dir, sid + '.zip')
        ns = argparse.Namespace(src=path, out=zpath,
                                app_store=args.app_store, dry_run=False)
        try:
            csp.do_export(ns)
            manifest.append({"sessionId": sid, "project": proj, "cwd": cwd,
                             "bundle": os.path.basename(zpath)})
        except SystemExit as e:
            print("[warn] skipped %s: %s" % (sid, e))
        if i % 25 == 0:
            print("   ... %d/%d" % (i, len(found)))

    with open(os.path.join(out_dir, 'manifest.json'), 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    tmpl = dict((c, "<FILL IN destination path>") for c in sorted(cwds))
    tmpl_path = os.path.join(out_dir, 'path-map.template.json')
    with open(tmpl_path, 'w', encoding='utf-8') as fh:
        json.dump(tmpl, fh, ensure_ascii=False, indent=2)

    export_extras(home, out_dir)
    export_app_profile(out_dir, args.app_store)
    if args.with_config:
        export_config(home, out_dir, args.app_store)

    print("\n[ok] %d bundles -> %s" % (len(manifest), out_dir))
    print("[ok] fill in %s and pass it to import-all --path-map" % tmpl_path)


# ---------------------------------------------------------------- import-all
def do_import_all(args):
    src_dir = os.path.abspath(args.src)
    mpath = os.path.join(src_dir, 'manifest.json')
    if not os.path.isfile(mpath):
        sys.exit("[error] manifest.json not found in " + src_dir)
    with open(mpath, encoding='utf-8') as fh:
        manifest = json.load(fh)

    with open(args.path_map, encoding='utf-8') as fh:
        mapping = json.load(fh)
    bad = [k for k, v in mapping.items() if not v or v.startswith('<')]
    if bad:
        sys.exit("[error] unfilled entries in the path map:\n  " + "\n  ".join(bad))

    to_posix = not is_windows_path(list(mapping.values())[0])
    print("[map] %d prefixes | destination style: %s"
          % (len(mapping), 'POSIX' if to_posix else 'Windows'))
    rewrite = build_rewriter(mapping, to_posix)

    def remap(cwd):
        for old in sorted(mapping, key=len, reverse=True):
            if cwd == old or cwd.startswith(old + '\\') or cwd.startswith(old + '/'):
                tail = cwd[len(old):]
                if to_posix:
                    tail = tail.replace('\\', '/')
                return mapping[old] + tail
        return None

    home = csp.default_claude_home(args.claude_home)

    # Faithful mode: instead of synthesising new records, restore the originals
    # and keep the session ids. That is what makes pinning survive, because
    # pinnedOrder holds each record's local_<uuid> id, not the session's.
    faithful = args.faithful and os.path.isfile(os.path.join(src_dir, PROFILE_ZIP))
    if args.faithful and not faithful:
        print("[warn] --faithful asked for, but the bundle has no " + PROFILE_ZIP
              + " (exported by an older version); continuing in normal mode)")
    if faithful:
        print("[info] faithful mode: ids preserved; records and sidebar"
              " come from the source")
    if not args.dry_run:
        ensure_retention(home, args.retention_days)

    ok = fail = 0
    for i, ent in enumerate(manifest, 1):
        target = remap(ent['cwd'])
        if target is None:
            print("[warn] no mapping for %s -- skipping %s" % (ent['cwd'], ent['sessionId']))
            fail += 1
            continue
        bundle = os.path.join(src_dir, ent['bundle'])
        if not os.path.isfile(bundle):
            print("[warn] missing bundle " + ent['bundle'])
            fail += 1
            continue

        if args.dry_run:
            print("[dry] %s  %s  ->  %s" % (ent['sessionId'][:8], ent['cwd'], target))
            ok += 1
            continue

        # import with the single-session logic, then apply the separator-aware
        # rewrite ourselves (keep_paths=True disables the naive one)
        ns = argparse.Namespace(
            src=bundle, target_cwd=target, claude_home=args.claude_home,
            keep_id=args.keep_id or faithful,
            keep_paths=True,
            git_branch=None, title_suffix=None, title=None,
            app_store=args.app_store, no_app_index=args.no_app_index or faithful, index_all=args.index_all,
            bump_version=False, no_sidecar=False, with_history=args.with_history,
            dry_run=False)
        # snapshot the destination so the deep rewrite only ever touches the
        # file this import created -- sessions already there are not ours
        dest = os.path.join(home, 'projects', csp.enc_project(target))
        before = set(os.listdir(dest)) if os.path.isdir(dest) else set()
        try:
            csp.do_import(ns)
            after = set(os.listdir(dest)) if os.path.isdir(dest) else set()
            _deep_rewrite_files(dest, sorted(after - before), rewrite)
            ok += 1
        except SystemExit as e:
            print("[warn] failed %s: %s" % (ent['sessionId'], e))
            fail += 1
        if i % 25 == 0:
            print("   ... %d/%d" % (i, len(manifest)))

    if not args.dry_run:
        # old slug -> new slug, derived from the same prefix map
        slug_map = {}
        for old_cwd in mapping:
            slug_map[csp.enc_project(old_cwd)] = csp.enc_project(mapping[old_cwd])

        def remap_slug(old_slug):
            if old_slug in slug_map:
                return slug_map[old_slug]
            # worktrees: the child's slug starts with the parent's slug
            for o, n in sorted(slug_map.items(), key=lambda kv: -len(kv[0])):
                if old_slug.startswith(o):
                    return n + old_slug[len(o):]
            return None

        import_extras(home, src_dir, rewrite, remap_slug,

                      plain_rewrite=build_plain_rewriter(mapping, to_posix))

        if faithful:
            import_app_profile(src_dir, remap, args.app_store, args.dry_run,
                               raw_map=mapping, to_posix=to_posix)

        restored = import_config(home, src_dir, rewrite, args.app_store,
                                 args.dry_run)
        if restored and not args.dry_run:
            # the source's settings.json overwrote what ensure_retention wrote;
            # reapply it so transcripts older than 30 days are not pruned again
            ensure_retention(home, args.retention_days)
            print("     >>> close and reopen the app: Local Storage is only"
                  " re-read at startup")

    print("\n[done] imported %d, failed/skipped %d" % (ok, fail))


# ------------------------------------------------------------------- extras
# A transcript is not everything. Next to the sessions live artefacts the user
# expects to find again on the other side: the per-project memories and the
# global CLAUDE.md. Nothing in the .jsonl points at them, so they travel
# separately -- and they need the path rewrite too, because they cite Windows
# paths.
EXTRAS_ZIP = '_extras.zip'
PROJECT_EXTRA_DIRS = ('memory', 'plans')
HOME_EXTRA_FILES = ('CLAUDE.md',)

CONFIG_ZIP = '_config.zip'
# ~/.claude.json holds the MCP servers and the per-project permissions; without
# it the new machine starts with every integration left to reconfigure
CONFIG_HOME_FILES = ('.claude.json',)
CONFIG_CLAUDE_FILES = ('settings.json', 'settings.local.json')
# app profile files that belong to the DEVICE, not to the user: carrying a
# session token or the device registration to another machine does not help and
# spreads credentials around
CONFIG_PROFILE_SKIP = ('buddy-tokens.json', 'ant-device-registry.json')


def _has_credential(text):
    """Heuristic: does this file carry a secret that would travel in the bundle?"""
    return bool(re.search(r'"[^"]*(secret|token|password|api_?key)[^"]*"\s*:\s*"[^"]{8,}"',
                          text, re.I))


def export_config(home, out_dir, app_store=None):
    """
    Carry the configuration: MCP servers, permissions, app settings.

    This stays off the normal path (--with-config) because these files often
    hold real credentials -- MCP server environment variables, for one -- and
    the bundle tends to end up on an external disk. Whoever asks for it is told
    what travelled along.
    """
    import zipfile
    path = os.path.join(out_dir, CONFIG_ZIP)
    home_dir = os.path.expanduser('~')
    prof = csp.app_profile_dir(app_store)
    with_secret, n = [], 0

    def store(z, source, arcname):
        nonlocal n
        if not os.path.isfile(source):
            return
        z.write(source, arcname=arcname)
        n += 1
        try:
            if _has_credential(open(source, encoding='utf-8', errors='ignore').read()):
                with_secret.append(arcname)
        except Exception:
            pass

    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for fn in CONFIG_HOME_FILES:
            store(z, os.path.join(home_dir, fn), 'home/' + fn)
        for fn in CONFIG_CLAUDE_FILES:
            store(z, os.path.join(home, fn), 'claude/' + fn)
        if prof and os.path.isdir(prof):
            for fn in sorted(os.listdir(prof)):
                if not fn.endswith('.json') or fn in CONFIG_PROFILE_SKIP:
                    continue
                store(z, os.path.join(prof, fn), 'profile/' + fn)

    print("[ok] config: %d files -> %s" % (n, path))
    if with_secret:
        print("[!!] these carry credentials and travel in the bundle:")
        for a in with_secret:
            print("        " + a)
    return n


def import_config(home, src_dir, rewrite, app_store=None, dry=False):
    """Restore the configuration, rewriting the paths it holds."""
    import zipfile
    path = os.path.join(src_dir, CONFIG_ZIP)
    if not os.path.isfile(path):
        return 0
    home_dir = os.path.expanduser('~')
    prof = csp.app_profile_dir(app_store)
    n = 0
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.startswith('home/'):
                dest = os.path.join(home_dir, name[len('home/'):])
            elif name.startswith('claude/'):
                dest = os.path.join(home, name[len('claude/'):])
            elif name.startswith('profile/') and prof:
                dest = os.path.join(prof, name[len('profile/'):])
            else:
                continue
            data = z.read(name)
            try:
                # these are .json: the path shows up escaped, including in the
                # KEYS of "projects" -- rewriting the whole text covers both
                data = rewrite(data.decode('utf-8')).encode('utf-8')
            except UnicodeDecodeError:
                pass
            if not dry:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, 'wb') as fh:
                    fh.write(data)
            n += 1
    print("[ok] config: %d files restored" % n)
    return n


def export_extras(home, out_dir):
    """Bundle per-project extra dirs + home-level files into _extras.zip."""
    import zipfile
    path = os.path.join(out_dir, EXTRAS_ZIP)
    n = 0
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        proj_root = os.path.join(home, 'projects')
        for proj in sorted(os.listdir(proj_root)) if os.path.isdir(proj_root) else []:
            for sub in PROJECT_EXTRA_DIRS:
                d = os.path.join(proj_root, proj, sub)
                if not os.path.isdir(d):
                    continue
                for root, _, files in os.walk(d):
                    for fn in files:
                        full = os.path.join(root, fn)
                        rel = os.path.relpath(full, proj_root).replace('\\', '/')
                        z.write(full, arcname='projects/' + rel)
                        n += 1
        for fn in HOME_EXTRA_FILES:
            f = os.path.join(home, fn)
            if os.path.isfile(f):
                z.write(f, arcname='home/' + fn)
                n += 1
    print("[ok] extras: %d files -> %s" % (n, path))
    return n


PROFILE_ZIP = '_app-profile.zip'


def _skip_in_leveldb(name):
    """
    LOCK does not travel.

    It is an empty file leveldb creates for itself, and on Windows the app keeps
    it open with exclusive access. Trying to zip it fails on the read -- but
    ZipFile.write has already written the entry header by then, so the package
    was left with an empty entry nobody could read and the count never saw.
    """
    return name == 'LOCK'


def export_app_profile(out_dir, app_store=None):
    """
    Carry the app profile: the local_*.json records, Local Storage and
    IndexedDB.

    Rebuilding the records from scratch loses what only exists in them, and the
    two browser stores split the sidebar's state between them: Local Storage
    holds order, width and grouping; IndexedDB holds the session index the
    interface actually reads -- including which ones are pinned. Without
    IndexedDB, 32 sessions stay flagged on disk and the sidebar shows an empty
    "Pinned" (measured). All of them point at the record's 'local_<uuid>' id, so
    the import has to preserve the ids (--keep-id).
    """
    import zipfile
    base, _, _ = csp.find_record_dir(app_store)
    if not base:
        bases = csp.candidate_app_store_bases(app_store)
        base = bases[0] if bases else None
    if not base or not os.path.isdir(base):
        print("[warn] app profile: no claude-code-sessions folder found")
        return 0

    ls = csp.local_storage_dir(app_store)
    path = os.path.join(out_dir, PROFILE_ZIP)
    n_rec = n_ls = n_idb = 0
    info = {"platform": sys.platform, "accounts": []}
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(base):
            for fn in files:
                if not (fn.startswith('local_') and fn.endswith('.json')):
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, base).replace(chr(92), '/')
                z.write(full, arcname='records/' + rel)
                n_rec += 1
                account = rel.split('/')[0]
                if account not in info["accounts"]:
                    info["accounts"].append(account)
        if ls:
            for fn in sorted(os.listdir(ls)):
                full = os.path.join(ls, fn)
                if not os.path.isfile(full) or _skip_in_leveldb(fn):
                    continue
                z.write(full, arcname='local-storage/' + fn)
                n_ls += 1
        for idb in csp.indexeddb_dirs(app_store):
            origin = os.path.basename(idb)
            for fn in sorted(os.listdir(idb)):
                full = os.path.join(idb, fn)
                if not os.path.isfile(full) or _skip_in_leveldb(fn):
                    continue
                z.write(full, arcname='indexeddb/%s/%s' % (origin, fn))
                n_idb += 1
        info["records"] = n_rec
        info["localStorage"] = n_ls
        info["indexedDB"] = n_idb
        z.writestr('profile.json', json.dumps(info, ensure_ascii=False, indent=2))
    print("[ok] app profile: %d records, %d from Local Storage, %d from IndexedDB -> %s"
          % (n_rec, n_ls, n_idb, path))
    return n_rec


def _rewrite_local_storage(ls_dir, path_map, to_posix):
    """
    Fix the paths inside the copied LevelDB.

    This has to read the database for real -- the values sit in snappy-compressed
    blocks, so poking at the bytes does not reach them -- and for that it depends
    on plyvel. Without it Local Storage is still worth copying: pinning survives.
    Only the paths stay as they were, and the warning says how to fix them.
    """
    try:
        import localstorage_paths as lsp
    except Exception:
        return
    if not lsp.available():
        print("[warn] plyvel missing: Local Storage was copied (pinning")
        print("       survives), but the paths stored in it still point")
        print("       at the source machine. To fix:")
        print("         pacman -S python-plyvel   (or apt install python3-plyvel)")
        print("         python3 localstorage_paths.py --leveldb '%s' --path-map ..."
              % ls_dir)
        return
    try:
        n, k, _ = lsp.rewrite_db(ls_dir, path_map, to_posix)
        print("[ok] Local Storage: %d keys, %d with the path rewritten" % (n, k))
    except Exception as e:
        print("[warn] could not rewrite the Local Storage paths: %s" % e)


def _prepare_leveldb(dest):
    """
    Keep a copy of the destination's LevelDB and empty the folder.

    The copy because the replacement is total: if the app stored anything there
    encrypted by the system vault (DPAPI on Windows, keyring on Linux), a value
    coming from another machine will not decrypt. The emptying because mixing
    files from both sides leaves orphan .ldb files and a CURRENT pointing at the
    other set's MANIFEST -- the destination has to end up with exactly the
    source's files.
    """
    if not os.path.isdir(dest):
        os.makedirs(dest, exist_ok=True)
        return
    bak = dest + '.before-import'
    if not os.path.isdir(bak):
        import shutil
        shutil.copytree(dest, bak, ignore_dangling_symlinks=True)
        print("[ok] copy of the destination at %s" % bak)
    for fn in os.listdir(dest):
        target = os.path.join(dest, fn)
        if os.path.isfile(target):
            try:
                os.remove(target)
            except OSError:
                pass


def import_app_profile(src_dir, remap, app_store=None, dry=False,
                       raw_map=None, to_posix=True):
    """
    Restore the records with the cwd rewritten, plus both browser stores.

    The ids do not change -- that is what keeps pinning valid. The LevelDBs go
    file by file; in Local Storage the paths are rewritten afterwards (see
    _rewrite_local_storage). In IndexedDB they are not: the values use Blink's
    serialisation, with length-prefixed strings, and swapping one path for
    another of a different length would corrupt the record. The paths a session
    actually uses come from the local_*.json, which is rewritten.
    """
    import zipfile
    src = os.path.join(src_dir, PROFILE_ZIP)
    if not os.path.isfile(src):
        return 0, 0

    bases = csp.candidate_app_store_bases(app_store)
    base = next((b for b in bases if os.path.isdir(b)), None)
    if not base:
        print("[warn] app profile: destination has no claude-code-sessions; "
              "open the app and sign in first")
        return 0, 0
    ls_dest = os.path.join(csp.app_profile_dir(app_store), 'Local Storage', 'leveldb')

    if not dry:
        _prepare_leveldb(ls_dest)

    n_rec = n_ls = n_idb = 0
    idb_root = os.path.join(csp.app_profile_dir(app_store), 'IndexedDB')
    idb_ready = set()
    unreadable = []
    with zipfile.ZipFile(src) as z:
        for name in z.namelist():
            if name.startswith('records/') and name.endswith('.json'):
                raw = z.read(name)
                try:
                    o = json.loads(raw.decode('utf-8'))
                except Exception:
                    # Record already unreadable at the source: an unclean
                    # shutdown leaves the file at the right size and full of
                    # zeros. Copying it as-is keeps the mirror faithful (there
                    # is no path to rewrite) -- but it is worth warning about,
                    # because it may be a pinned session that does not open on
                    # the source machine either.
                    unreadable.append(os.path.basename(name))
                    if not dry:
                        dest = os.path.join(base, *name[len('records/'):].split('/'))
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with open(dest, 'wb') as fh:
                            fh.write(raw)
                    n_rec += 1
                    continue
                for field in ('cwd', 'originCwd', 'worktreePath', 'planPath'):
                    v = o.get(field)
                    if isinstance(v, str) and v:
                        new_v = remap(v)
                        if new_v:
                            o[field] = new_v
                dest = os.path.join(base, *name[len('records/'):].split('/'))
                if not dry:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, 'w', encoding='utf-8', newline=chr(10)) as fh:
                        json.dump(o, fh, ensure_ascii=False, indent=2)
                n_rec += 1
            elif name.startswith('local-storage/') and not name.endswith('/'):
                if not dry:
                    os.makedirs(ls_dest, exist_ok=True)
                    with open(os.path.join(ls_dest, os.path.basename(name)), 'wb') as fh:
                        fh.write(z.read(name))
                n_ls += 1
            elif name.startswith('indexeddb/') and not name.endswith('/'):
                parts = name.split('/')
                if len(parts) != 3:
                    continue
                origin, fn = parts[1], parts[2]
                dest_dir = os.path.join(idb_root, origin)
                if not dry and dest_dir not in idb_ready:
                    _prepare_leveldb(dest_dir)
                    idb_ready.add(dest_dir)
                if not dry:
                    with open(os.path.join(dest_dir, fn), 'wb') as fh:
                        fh.write(z.read(name))
                n_idb += 1

    # Local Storage holds file paths too: one cc-session-cwd-* key per session
    # plus JSON blobs with the folder grouping. Copied raw, the app asks you to
    # trust a path that does not exist here.
    if n_ls and not dry:
        _rewrite_local_storage(ls_dest, raw_map, to_posix)

    print("[ok] app profile: %d records, %d from Local Storage, %d from IndexedDB"
          % (n_rec, n_ls, n_idb))
    if unreadable:
        print("[warn] %d record(s) unreadable at the source, copied as-is:"
              % len(unreadable))
        for fn in unreadable[:5]:
            print("        " + fn)
    return n_rec, n_ls


def import_extras(home, src_dir, rewrite, remap_slug, plain_rewrite=None):
    """
    Restore _extras.zip, remapping project slugs and rewriting paths inside
    text files. Never overwrites an existing file on the target.
    """
    import zipfile
    path = os.path.join(src_dir, EXTRAS_ZIP)
    if not os.path.isfile(path):
        print("[info] sem %s no bundle (nada a restaurar)" % EXTRAS_ZIP)
        return 0
    written = skipped = 0
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if name.startswith('projects/'):
                rest = name[len('projects/'):]
                old_slug, _, tail = rest.partition('/')
                new_slug = remap_slug(old_slug)
                if new_slug is None:
                    skipped += 1
                    continue
                dest = os.path.join(home, 'projects', new_slug, *tail.split('/'))
            elif name.startswith('home/'):
                dest = os.path.join(home, name[len('home/'):])
            else:
                continue

            if os.path.exists(dest):      # nunca sobrescreve o que ja esta la
                skipped += 1
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            data = z.read(info)
            low = dest.lower()
            # .jsonl/.json carry the path JSON-escaped; .md/.txt carry it
            # literal -- different grammars, so they need different rewriters,
            # otherwise the pattern never matches in the markdown.
            fn_rw = rewrite if low.endswith(('.json', '.jsonl')) else (plain_rewrite or rewrite)
            if low.endswith(('.md', '.json', '.txt', '.jsonl')):
                try:
                    data = fn_rw(data.decode('utf-8')).encode('utf-8')
                except UnicodeDecodeError:
                    pass
            with open(dest, 'wb') as fh:
                fh.write(data)
            written += 1
    print("[ok] extras restaurados: %d (ignorados: %d)" % (written, skipped))
    return written


def ensure_retention(home, days=999999):
    """
    Claude Code prunes transcripts older than `cleanupPeriodDays` (default 30)
    at startup. Importing an archive without raising it first silently destroys
    everything older than a month, so write it BEFORE any transcript lands.
    """
    os.makedirs(home, exist_ok=True)
    sp = os.path.join(home, 'settings.json')
    data = {}
    if os.path.isfile(sp):
        try:
            with open(sp, encoding='utf-8') as fh:
                data = json.load(fh)
        except Exception:
            print("[warn] settings.json unreadable; leaving it alone")
            return
    cur = data.get('cleanupPeriodDays')
    if cur is not None and cur >= days:
        print("[ok] cleanupPeriodDays already %s" % cur)
        return
    data['cleanupPeriodDays'] = days
    with open(sp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print("[ok] cleanupPeriodDays %s -> %d in %s" % (cur, days, sp))


def _deep_rewrite_files(dest_dir, names, rewrite):
    """
    Apply the separator-aware rewrite to what this import just created.

    `names` comes from a before/after diff of the destination directory, so
    pre-existing sessions on the target machine are never read or rewritten.

    A new entry is a FILE (the transcript) or a DIRECTORY -- the session's
    sidecar, holding the subagent and workflow transcripts. Skipping anything
    that did not end in .jsonl silently skipped those directories: measured on
    a real migration, 2774 of 2959 nested transcripts kept the source machine's
    paths. Resuming still worked (the cwd a session resumes with lives in the
    top-level file) but the history inside the log was wrong.
    """
    def one(p, label):
        try:
            with open(p, encoding='utf-8') as fh:
                txt = fh.read()
            new = rewrite(txt)
            if new != txt:
                with open(p, 'w', encoding='utf-8', newline='\n') as fh:
                    fh.write(new)
        except UnicodeDecodeError:
            pass          # binary in the sidecar (pdf, jpg): nothing to rewrite
        except Exception as e:
            print("[warn] rewrite failed on %s: %s" % (label, e))

    for fn in names:
        p = os.path.join(dest_dir, fn)
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                for a in files:
                    if a.endswith('.jsonl'):
                        full = os.path.join(root, a)
                        one(full, os.path.relpath(full, dest_dir))
        elif fn.endswith('.jsonl'):
            one(p, fn)


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(
        description="batch export/import of Claude Code sessions")
    sub = ap.add_subparsers(dest='cmd')

    pe = sub.add_parser('export-all',
                        help='bundle every session + emit a path-map template')
    pe.add_argument('--out', required=True, help='output directory for the bundles')
    pe.add_argument('--claude-home', default=None)
    pe.add_argument('--app-store', default=None)
    pe.add_argument('--with-config', action='store_true',
                    help='also carry ~/.claude.json, settings.json and the app'
                         ' config (MCP servers, permissions). May contain'
                         ' credentials: the command names which files.')
    pe.add_argument('--dry-run', action='store_true')
    pe.set_defaults(func=do_export_all)

    pi = sub.add_parser('import-all',
                        help='import a whole bundle, remapping cwd prefixes')
    pi.add_argument('--src', required=True, help='directory produced by export-all')
    pi.add_argument('--path-map', required=True,
                    help='JSON: {"old prefix": "new prefix"}')
    pi.add_argument('--claude-home', default=None)
    pi.add_argument('--app-store', default=None)
    pi.add_argument('--keep-id', action='store_true')
    pi.add_argument('--no-app-index', action='store_true')
    pi.add_argument('--faithful', action='store_true',
                    help='reproduce the source sidebar: keep the ids, restore'
                         ' the original records and the pinned state (needs the'
                         ' app closed on the destination)')
    pi.add_argument('--index-all', action='store_true',
                    help='also create a record for sessions that did not show'
                         ' up in the source interface')
    pi.add_argument('--with-history', action='store_true')
    pi.add_argument('--retention-days', type=int, default=999999,
                    help='cleanupPeriodDays to write before importing '
                         '(default 999999; the app prunes >30d otherwise)')
    pi.add_argument('--dry-run', action='store_true')
    pi.set_defaults(func=do_import_all)

    args = ap.parse_args()
    if not getattr(args, 'func', None):
        ap.print_help()
        sys.exit(2)
    args.func(args)


if __name__ == '__main__':
    main()
