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
            # cauda do caminho: barras normais/invertidas e caracteres comuns
            # de nome de arquivo; para em espaco, aspas ou fim de linha
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
            keep_id=args.keep_id,
            keep_paths=True,
            git_branch=None, title_suffix=None, title=None,
            app_store=args.app_store, no_app_index=args.no_app_index,
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
        # slug antigo -> slug novo, derivado do mesmo mapa de prefixos
        slug_map = {}
        for old_cwd in mapping:
            slug_map[csp.enc_project(old_cwd)] = csp.enc_project(mapping[old_cwd])

        def remap_slug(old_slug):
            if old_slug in slug_map:
                return slug_map[old_slug]
            # worktrees: o slug do filho comeca com o slug do pai
            for o, n in sorted(slug_map.items(), key=lambda kv: -len(kv[0])):
                if old_slug.startswith(o):
                    return n + old_slug[len(o):]
            return None

        import_extras(home, src_dir, rewrite, remap_slug,
                      plain_rewrite=build_plain_rewriter(mapping, to_posix))

    print("\n[done] imported %d, failed/skipped %d" % (ok, fail))


# ------------------------------------------------------------------- extras
# Um transcript nao e tudo. Ao lado das sessoes vivem artefatos que o usuario
# espera reencontrar do outro lado: as memorias por projeto e o CLAUDE.md
# global. Eles nao sao referenciados pelo .jsonl, entao viajam a parte -- e
# tambem precisam do rewrite de caminho, porque citam paths do Windows.
EXTRAS_ZIP = '_extras.zip'
PROJECT_EXTRA_DIRS = ('memory', 'plans')
HOME_EXTRA_FILES = ('CLAUDE.md',)


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
    print("[ok] extras: %d arquivos -> %s" % (n, path))
    return n


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
            # .jsonl/.json carregam o caminho JSON-escapado; .md/.txt o carregam
            # literal -- sao gramaticas diferentes e precisam de rewriters
            # diferentes, senao o padrao nunca casa no markdown.
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
    Apply the separator-aware rewrite to specific files only.

    `names` comes from a before/after diff of the destination directory, so
    pre-existing sessions on the target machine are never read or rewritten.
    """
    for fn in names:
        if not fn.endswith('.jsonl'):
            continue
        p = os.path.join(dest_dir, fn)
        try:
            with open(p, encoding='utf-8') as fh:
                txt = fh.read()
            new = rewrite(txt)
            if new != txt:
                with open(p, 'w', encoding='utf-8', newline='\n') as fh:
                    fh.write(new)
        except Exception as e:
            print("[warn] rewrite failed on %s: %s" % (fn, e))


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
