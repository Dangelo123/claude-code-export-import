#!/usr/bin/env python3
"""
claude_session_port.py — export/import Claude Code sessions (.jsonl) between
different installations, machines, accounts, and app builds.

Reverse-engineered model (see docs/HOW-IT-WORKS.md):
  A session is TWO pieces, and you need both for it to show up seamlessly:

  PIECE 1 - transcript:
    ~/.claude/projects/<ENC(cwd)>/<sessionId>.jsonl
    ENC(cwd) = re.sub(r'[^A-Za-z0-9]', '-', cwd)
    Per-line structural fields: cwd (absolute path) and sessionId
    (== the filename). The sibling folder <sessionId>/ holds cache
    (tool-results/subagents/workflows) and travels along.

  PIECE 2 - desktop app index record (the Electron app is index-driven):
    <app-store>/<accountUuid>/<group>/local_<uuid>.json
    Key fields: sessionId (local_<uuid>, the app id), cliSessionId
    (-> the jsonl id, the mapping), cwd, title, titleSource, model, ...
    <app-store> is auto-detected per platform/build (Win32 / MSIX-Store /
    macOS / Linux).

Usage:
  # 1) On the SOURCE machine: bundle the session (jsonl + sidecar + meta) into a .zip
  python claude_session_port.py export --src <session.jsonl> [--out bundle.zip]

  # 2) On the TARGET machine: retarget the cwd and install both pieces
  python claude_session_port.py import --src <bundle.zip|session.jsonl> [--target-cwd "D:\\Work\\Project"]

This is an unofficial tool that manipulates undocumented local files. It is not
affiliated with or endorsed by Anthropic. Back up ~/.claude before using it.
"""
import argparse, json, os, re, sys, shutil, zipfile, uuid, tempfile, time, glob
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

def enc_project(cwd: str) -> str:
    """Encode an absolute cwd into the project folder name Claude Code uses."""
    return re.sub(r'[^A-Za-z0-9]', '-', cwd)

def jesc(s: str) -> str:
    """The string as it would appear escaped INSIDE JSON double-quotes."""
    return json.dumps(s, ensure_ascii=False)[1:-1]

def detect_old_cwd(lines):
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if isinstance(o, dict) and o.get('cwd'):
            return o['cwd']
    return None

def detect_session_id(lines, fallback):
    for ln in lines:
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if isinstance(o, dict) and o.get('sessionId'):
            return o['sessionId']
    return fallback

# --------------------------------------------------------- desktop app store
def candidate_app_store_bases(explicit=None):
    """Candidate 'claude-code-sessions' bases to probe (Win32 + MSIX/Store + other OS)."""
    if explicit:
        return [explicit]
    bases = []
    if sys.platform == 'win32':
        appdata = os.environ.get('APPDATA', '')
        local = os.environ.get('LOCALAPPDATA', '')
        if appdata:
            bases.append(os.path.join(appdata, 'Claude', 'claude-code-sessions'))  # Win32 install
        if local:
            # Packaged (MSIX/Store) build: AppData is virtualized inside the package
            for pkg in glob.glob(os.path.join(local, 'Packages', 'Claude_*',
                                              'LocalCache', 'Roaming', 'Claude',
                                              'claude-code-sessions')):
                bases.append(pkg)
    elif sys.platform == 'darwin':
        bases.append(os.path.expanduser('~/Library/Application Support/Claude/claude-code-sessions'))
    else:
        bases.append(os.path.expanduser('~/.config/Claude/claude-code-sessions'))
    # On Windows with the Store build, %APPDATA%\Claude is a junction into the
    # package's LocalCache: both bases are the SAME folder, and without this
    # every record is read and counted twice.
    unique, seen = [], set()
    for b in bases:
        key = os.path.normcase(os.path.realpath(b))
        if key not in seen:
            seen.add(key)
            unique.append(b)
    return unique

def find_record_by_cli(cli_id, explicit=None):
    """Find the app record (local_*.json) whose cliSessionId == cli_id, in any base."""
    for base in candidate_app_store_bases(explicit):
        for f in glob.glob(os.path.join(base, '**', 'local_*.json'), recursive=True):
            try:
                o = json.load(open(f, encoding='utf-8'))
            except Exception:
                continue
            if isinstance(o, dict) and o.get('cliSessionId') == cli_id:
                return o, f
    return None, None

def find_record_dir(explicit=None):
    """Search all candidate bases for a folder with a local_*.json (template).
       Returns (base, rec_dir, template_path). If none has a record, returns
       the first existing base as (base, None, None)."""
    fallback = (None, None, None)
    for base in candidate_app_store_bases(explicit):
        recs = glob.glob(os.path.join(base, '**', 'local_*.json'), recursive=True)
        if recs:
            newest = max(recs, key=lambda p: os.path.getmtime(p))
            return base, os.path.dirname(newest), newest
        if fallback[0] is None and os.path.isdir(base):
            fallback = (base, None, None)
    return fallback

def find_account_dir(explicit=None):
    """
    Locate <base>/<accountUuid>[/<groupUuid>] for a target that has never run a
    Code session -- exactly the migration case: the app is freshly installed and
    holds zero local_*.json to clone.

    Signing in is enough to create the account folder (verified on a clean
    install), so that folder is the one thing we cannot invent: the app only
    reads records under the UUID of the account that is signed in.

    Returns (base, rec_dir) or (None, None).
    """
    for base in candidate_app_store_bases(explicit):
        if not os.path.isdir(base):
            continue
        accounts = [d for d in glob.glob(os.path.join(base, '*')) if os.path.isdir(d)]
        if not accounts:
            continue
        acc = max(accounts, key=os.path.getmtime)
        groups = [d for d in glob.glob(os.path.join(acc, '*')) if os.path.isdir(d)]
        rec_dir = max(groups, key=os.path.getmtime) if groups else             os.path.join(acc, str(uuid.uuid4()))
        return base, rec_dir
    return None, None


def app_profile_dir(explicit=None):
    """Claude Desktop's profile folder (the parent of claude-code-sessions).

    This is where the two browser stores the sidebar relies on live: Local
    Storage (width, order, grouping) and IndexedDB (the session index, including
    which ones are pinned)."""
    for base in candidate_app_store_bases(explicit):
        if os.path.isdir(base):
            return os.path.dirname(base)
    bases = candidate_app_store_bases(explicit)
    return os.path.dirname(bases[0]) if bases else None


def local_storage_dir(explicit=None):
    """<profile>/Local Storage/leveldb, or None if it does not exist yet."""
    prof = app_profile_dir(explicit)
    if not prof:
        return None
    d = os.path.join(prof, "Local Storage", "leveldb")
    return d if os.path.isdir(d) else None


def indexeddb_dirs(explicit=None):
    """
    The app's IndexedDB folders, one per origin.

    This is where the session index the interface reads lives, including which
    ones are pinned. The local_*.json files are a mirror: restoring only those
    leaves 33 sessions flagged on disk and the sidebar showing one.
    """
    prof = app_profile_dir(explicit)
    if not prof:
        return []
    root = os.path.join(prof, "IndexedDB")
    if not os.path.isdir(root):
        return []
    return sorted(d for d in glob.glob(os.path.join(root, '*'))
                  if os.path.isdir(d) and d.endswith('.leveldb'))


def default_claude_home(explicit=None):
    return os.path.abspath(os.path.expanduser(
        explicit or os.path.join(os.path.expanduser('~'), '.claude')))

def list_sessions(claude_home=None, app_store=None, include_archived=False):
    """Enumerate local sessions for a friendly picker: join app records with their
       jsonl transcript. Returns a list of dicts sorted by lastFocusedAt (desc):
       {title, cwd, cli_id, jsonl, last, archived}."""
    home = default_claude_home(claude_home)
    out, seen = [], set()
    for base in candidate_app_store_bases(app_store):
        for f in glob.glob(os.path.join(base, '**', 'local_*.json'), recursive=True):
            try:
                o = json.load(open(f, encoding='utf-8'))
            except Exception:
                continue
            cli = o.get('cliSessionId')
            cwd = o.get('cwd')
            if not cli or cli in seen:
                continue
            if o.get('isArchived') and not include_archived:
                continue
            jsonl = None
            if cwd:
                cand = os.path.join(home, 'projects', enc_project(cwd), cli + '.jsonl')
                if os.path.isfile(cand):
                    jsonl = cand
            if not jsonl:  # fallback: find the jsonl by id under any project folder
                hits = glob.glob(os.path.join(home, 'projects', '*', cli + '.jsonl'))
                if hits:
                    jsonl = hits[0]
            if not jsonl:
                continue
            seen.add(cli)
            out.append({'title': o.get('title') or '(untitled)', 'cwd': cwd,
                        'cli_id': cli, 'jsonl': jsonl,
                        'last': o.get('lastFocusedAt') or 0,
                        'archived': bool(o.get('isArchived'))})
    out.sort(key=lambda r: r['last'], reverse=True)
    return out

# shape of a record with nothing inherited -- used when the destination has no
# local_*.json to clone (fresh install). Mirrors the fields the app always writes.
BLANK_RECORD = {
    'alwaysAllowedReasons': [],
    'bridgeSessionIds': [],
    'enabledMcpTools': {},
    'permissionMode': 'default',
    'sessionPermissionUpdates': [],
    'spawnSeed': {},
}


def write_app_index(base, template_path, cli_id, cwd, title, title_source, dry,
                    rec_dir=None, archived=False):
    """Create local_<uuid>.json (the record the app uses to list/title the session).

    Clones an existing record when there is one; on a fresh install there is
    none, so it falls back to BLANK_RECORD and writes into the signed-in
    account folder (rec_dir)."""
    if template_path:
        rec_dir = os.path.dirname(template_path)
        o = json.load(open(template_path, encoding='utf-8'))
    else:
        if not rec_dir:
            raise ValueError('write_app_index needs template_path or rec_dir')
        o = dict(BLANK_RECORD)
    new_local = 'local_' + str(uuid.uuid4())
    now = int(time.time() * 1000)
    o['sessionId'] = new_local
    o['cliSessionId'] = cli_id
    o['cwd'] = cwd
    o['originCwd'] = cwd
    o['title'] = title
    o['titleSource'] = title_source
    o['createdAt'] = now
    o['lastFocusedAt'] = now
    o['lastActivityAt'] = now
    o['isArchived'] = archived
    o['bridgeSessionIds'] = []
    # drop fields that should not be inherited from the template
    for k in ('worktreeName', 'worktreePath', 'prNumber', 'prUrl', 'prRepository',
              'prState', 'prs', 'sourceBranch', 'branch', 'scheduledTaskId',
              'planPath', 'chromeTabGroupId'):
        o.pop(k, None)
    dest = os.path.join(rec_dir, new_local + '.json')
    if not dry:
        os.makedirs(rec_dir, exist_ok=True)
    if dry:
        print(f" [app-index] would create {os.path.relpath(dest, base)}  (title={title!r}, source={title_source})")
        return dest
    with open(dest, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(o, fh, ensure_ascii=False, indent=2)
    return dest

# ---------------------------------------------------------------------- export
def first_user_message(lines, limit=60):
    """The first thing the user actually said -- the last resort for a title,
    before falling back to the folder name (which produces several sessions all
    called ClaudeNode)."""
    for ln in lines:
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("type") != "user":
            continue
        c = (o.get("message") or {}).get("content")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
        if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<"):
            t = " ".join(c.split())
            return t[:limit].rstrip() + ("..." if len(t) > limit else "")
    return None


def do_export(args):
    src = os.path.abspath(args.src)
    if not os.path.isfile(src):
        sys.exit(f"[error] not found: {src}")
    sess_id = os.path.splitext(os.path.basename(src))[0]
    sidecar = os.path.join(os.path.dirname(src), sess_id)
    out = args.out or os.path.join(os.path.dirname(src), f"claude-session-{sess_id}.zip")

    # capture metadata from the app record (display title, cwd, model) -> rides in the bundle
    lines = open(src, encoding='utf-8').readlines()
    meta = {"cliSessionId": sess_id,
            "cwd": detect_old_cwd(lines),
            "title": None, "titleSource": None,
            "model": None, "effort": None,
            # did the source show this session in the interface? With no app
            # record it exists on disk and appears nowhere. Recreating a record
            # for every one of them fills the destination with sessions the user
            # never saw -- abandoned resume branches above all.
            "hadAppRecord": False, "isArchived": False}

    # titles stored in the transcript itself, in the priority the app uses:
    # the name the user gave beats the automatically generated one
    custom_title = ai_title = None
    for ln in lines:
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("type") == "custom-title" and o.get("customTitle"):
            custom_title = o["customTitle"]
        elif o.get("type") == "ai-title" and o.get("aiTitle"):
            ai_title = o["aiTitle"]
    meta["customTitle"] = custom_title
    meta["aiTitle"] = ai_title
    meta["firstUserMessage"] = first_user_message(lines)

    rec, _ = find_record_by_cli(sess_id, args.app_store)
    if rec:
        meta.update({"title": rec.get("title"), "titleSource": rec.get("titleSource"),
                     "cwd": rec.get("cwd") or meta["cwd"],
                     "model": rec.get("model"), "effort": rec.get("effort"),
                     "hadAppRecord": True,
                     "isArchived": bool(rec.get("isArchived"))})
    else:
        meta["title"] = custom_title or ai_title

    if args.dry_run:
        print(f"[dry] zip -> {out}\n  + {os.path.basename(src)}  + meta.json (title={meta['title']!r})")
        if os.path.isdir(sidecar):
            print(f"  + {sess_id}/ (sidecar)")
        return
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(src, arcname=os.path.basename(src))
        z.writestr('meta.json', json.dumps(meta, ensure_ascii=False, indent=2))
        if os.path.isdir(sidecar):
            for root, _, files in os.walk(sidecar):
                for fn in files:
                    full = os.path.join(root, fn)
                    rel = os.path.relpath(full, os.path.dirname(src))
                    z.write(full, arcname=rel)
    print(f"[ok] bundle: {out}   (title={meta['title']!r})")
    print("     copy this zip to the other machine and run 'import' — title/cwd travel with it.")

# ---------------------------------------------------------------------- import
def load_source(src, workdir):
    """Return (jsonl_path, sidecar_dir|None, meta_dict). Accepts a .zip or a raw .jsonl."""
    if src.lower().endswith('.zip'):
        with zipfile.ZipFile(src) as z:
            z.extractall(workdir)
        jsonls = [os.path.join(workdir, f) for f in os.listdir(workdir) if f.endswith('.jsonl')]
        if not jsonls:
            sys.exit("[error] zip contains no .jsonl")
        jp = jsonls[0]
        sid = os.path.splitext(os.path.basename(jp))[0]
        sc = os.path.join(workdir, sid)
        meta = {}
        mp = os.path.join(workdir, 'meta.json')
        if os.path.isfile(mp):
            try:
                meta = json.load(open(mp, encoding='utf-8'))
            except Exception:
                meta = {}
        return jp, (sc if os.path.isdir(sc) else None), meta
    else:
        jp = os.path.abspath(src)
        sid = os.path.splitext(os.path.basename(jp))[0]
        sc = os.path.join(os.path.dirname(jp), sid)
        return jp, (sc if os.path.isdir(sc) else None), {}

def local_version(home):
    """Read the 'version' field from any existing local session (for --bump-version)."""
    proj = os.path.join(home, 'projects')
    if not os.path.isdir(proj):
        return None
    for d in os.listdir(proj):
        dd = os.path.join(proj, d)
        if not os.path.isdir(dd):
            continue
        for f in os.listdir(dd):
            if f.endswith('.jsonl'):
                try:
                    with open(os.path.join(dd, f), encoding='utf-8') as fh:
                        for ln in fh:
                            o = json.loads(ln)
                            if isinstance(o, dict) and o.get('version'):
                                return o['version']
                except Exception:
                    pass
    return None

def do_import(args):
    home = os.path.abspath(os.path.expanduser(args.claude_home or os.path.join(os.path.expanduser('~'), '.claude')))

    tmp = tempfile.mkdtemp(prefix='clsport_')
    try:
        src_jsonl, src_sidecar, meta = load_source(args.src, tmp)
        with open(src_jsonl, encoding='utf-8') as fh:
            lines = fh.readlines()

        # target cwd: --target-cwd, else the cwd that came in the bundle (meta), else the jsonl's
        target_cwd = args.target_cwd or meta.get('cwd') or detect_old_cwd(lines)
        if not target_cwd:
            sys.exit("[error] no --target-cwd and no cwd in the bundle — pass --target-cwd")
        if not args.target_cwd:
            print(f"[info] --target-cwd not given; using source cwd: {target_cwd}")

        old_cwd = detect_old_cwd(lines)
        old_sid = detect_session_id(lines, os.path.splitext(os.path.basename(src_jsonl))[0])
        new_sid = old_sid if args.keep_id else str(uuid.uuid4())

        bump = local_version(home) if args.bump_version else None

        # deep path-rewrite map (rewrite the old cwd inside string content)
        rewrites = []
        if not args.keep_paths and old_cwd and old_cwd != target_cwd:
            rewrites.append((jesc(old_cwd), jesc(target_cwd)))                       # backslash form
            rewrites.append((jesc(old_cwd.replace('\\', '/')), jesc(target_cwd)))    # forward-slash form

        out_lines = []
        prompts = []  # for history.jsonl
        for ln in lines:
            raw = ln.rstrip('\n')
            if not raw.strip():
                continue
            try:
                o = json.loads(raw)
            except Exception:
                out_lines.append(raw)  # pass through untouched if not JSON
                continue
            if isinstance(o, dict):
                if 'cwd' in o:
                    o['cwd'] = target_cwd
                if 'sessionId' in o:
                    o['sessionId'] = new_sid
                if bump and 'version' in o:
                    o['version'] = bump
                if args.git_branch is not None and 'gitBranch' in o:
                    o['gitBranch'] = args.git_branch
                if args.title_suffix and o.get('type') == 'ai-title' and o.get('aiTitle'):
                    o['aiTitle'] = o['aiTitle'] + args.title_suffix
                # collect user prompts for history.jsonl
                if o.get('type') == 'user':
                    msg = o.get('message') or {}
                    c = msg.get('content') if isinstance(msg, dict) else None
                    if isinstance(c, str) and c.strip():
                        prompts.append(c)
                    elif isinstance(c, list):
                        for part in c:
                            if isinstance(part, dict) and part.get('type') == 'text' and part.get('text'):
                                prompts.append(part['text'])
            s = json.dumps(o, ensure_ascii=False, separators=(',', ':'))
            for a, b in rewrites:
                s = s.replace(a, b)
            out_lines.append(s)

        dest_dir = os.path.join(home, 'projects', enc_project(target_cwd))
        dest_jsonl = os.path.join(dest_dir, f"{new_sid}.jsonl")

        print("======== import plan ========")
        print(f" source jsonl : {src_jsonl}")
        print(f" old cwd      : {old_cwd}")
        print(f" new cwd      : {target_cwd}")
        print(f" sessionId    : {old_sid}" + ("  (kept)" if args.keep_id else f"  ->  {new_sid}  (new)"))
        print(f" dest folder  : {dest_dir}")
        print(f" dest file    : {dest_jsonl}")
        print(f" lines        : {len(out_lines)}")
        if bump:
            print(f" version      : -> {bump}")
        if rewrites:
            print(f" rewrite      : {old_cwd}  ->  {target_cwd}  (in content)")
        if src_sidecar and not args.no_sidecar:
            print(f" sidecar      : {os.path.basename(src_sidecar)}/  ->  {new_sid}/")
        print("=============================")

        if args.dry_run:
            print("[dry-run] nothing written.")
            return

        if os.path.exists(dest_jsonl) and args.keep_id:
            print(f"[warn] {dest_jsonl} already exists — overwriting (drop --keep-id to mint a new id).")

        os.makedirs(dest_dir, exist_ok=True)
        with open(dest_jsonl, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write('\n'.join(out_lines) + '\n')

        if src_sidecar and not args.no_sidecar:
            dest_sidecar = os.path.join(dest_dir, new_sid)
            if os.path.isdir(dest_sidecar):
                shutil.rmtree(dest_sidecar)
            shutil.copytree(src_sidecar, dest_sidecar)

        if args.with_history and prompts:
            hist = os.path.join(home, 'history.jsonl')
            base_ts = 1700000000000
            with open(hist, 'a', encoding='utf-8', newline='\n') as fh:
                for i, p in enumerate(prompts):
                    rec = {"display": p, "pastedContents": {}, "timestamp": base_ts + i,
                           "project": target_cwd, "sessionId": new_sid}
                    fh.write(json.dumps(rec, ensure_ascii=False) + '\n')

        # ---- PIECE 2: the desktop app index record (gives the session a title + listing) ----
        if not args.no_app_index:
            # mirror the source's visibility: a session with no record there did
            # not appear in the interface, and creating one here would invent a
            # session the user never saw. --index-all forces the opposite.
            visible = meta.get("hadAppRecord", True) or args.index_all
            if not visible:
                print("[skip] app record: no record at the source, "
                      "stays on disk only (--index-all lists it anyway)")
            else:
                base, rec_dir, template = find_record_dir(args.app_store)
                if not template:
                    # fresh install: there is nothing to clone. Signing in has
                    # already created the account folder, which is the one thing
                    # we cannot invent -- build the record from scratch in it.
                    base, rec_dir = find_account_dir(args.app_store)
                if not rec_dir:
                    print("[warn] no signed-in account found. Bases searched:")
                    for b in candidate_app_store_bases(args.app_store):
                        print("        " + b)
                    print("       open the app and sign in (that creates the")
                    print("       account folder), then re-run import.")
                else:
                    # the name the user gave beats the generated one; only then
                    # the first message; the folder name is the last resort,
                    # because it produces several sessions called ClaudeNode.
                    title = (args.title or meta.get("customTitle")
                             or meta.get("title") or meta.get("aiTitle")
                             or meta.get("firstUserMessage")
                             or first_user_message(out_lines)
                             or os.path.basename(target_cwd.rstrip(chr(92) + "/")))
                    if args.title or meta.get("customTitle"):
                        src_choice = "user"
                    else:
                        src_choice = meta.get("titleSource") or "auto"
                    rec = write_app_index(base, template, new_sid, target_cwd,
                                          title, src_choice, args.dry_run,
                                          rec_dir=rec_dir,
                                          archived=bool(meta.get("isArchived")))
                    print(f"[ok] app record : {rec}")

        print(f"[ok] installed  : {dest_jsonl}")
        print("     >>> Relaunch Claude (Quit + reopen) — the session shows up in the project's Recents.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def do_list(args):
    rows = list_sessions(args.claude_home, args.app_store, include_archived=args.all)
    if not rows:
        print("No sessions found.")
        return
    print(f"{len(rows)} session(s):\n")
    for i, r in enumerate(rows, 1):
        print(f"{i:3}. {r['title']}")
        print(f"     {r['cwd']}")
        print(f"     {r['jsonl']}")

def main():
    ap = argparse.ArgumentParser(
        description="Port Claude Code .jsonl sessions between installs, machines, accounts and builds.")
    sub = ap.add_subparsers(dest='cmd', required=True)

    pl = sub.add_parser('list', help='list local sessions (title + transcript path)')
    pl.add_argument('--claude-home', help='.claude root (default ~/.claude)')
    pl.add_argument('--app-store', default=None, help='claude-code-sessions folder (default: auto)')
    pl.add_argument('--all', action='store_true', help='include archived sessions')
    pl.set_defaults(func=do_list)

    pe = sub.add_parser('export', help='bundle session + sidecar + meta(title/cwd) into a zip')
    pe.add_argument('--src', required=True, help='path to the source .jsonl')
    pe.add_argument('--out', help='output zip path')
    pe.add_argument('--app-store', default=None, help='source claude-code-sessions folder (default: auto)')
    pe.add_argument('--dry-run', action='store_true')
    pe.set_defaults(func=do_export)

    pi = sub.add_parser('import', help='retarget the cwd and install (jsonl + app record)')
    pi.add_argument('--src', required=True, help='.zip (from export) or a raw .jsonl')
    pi.add_argument('--target-cwd', default=None, help='project path on the target (default: cwd from the bundle)')
    pi.add_argument('--claude-home', help='target .claude root (default ~/.claude)')
    pi.add_argument('--keep-id', action='store_true', help='keep the original sessionId (default: mint a new one)')
    pi.add_argument('--keep-paths', action='store_true', help='do not rewrite the old cwd inside content')
    pi.add_argument('--git-branch', default=None, help='override gitBranch')
    pi.add_argument('--title-suffix', default=None, help="suffix the jsonl's internal ai-title")
    pi.add_argument('--title', default=None, help='sidebar title (app record, titleSource=user)')
    pi.add_argument('--app-store', default=None, help='claude-code-sessions folder (default: auto per OS)')
    pi.add_argument('--no-app-index', action='store_true', help='do not create the app record (jsonl only)')
    pi.add_argument('--index-all', action='store_true',
                     help='also create a record for sessions that did not show up in the source interface')
    pi.add_argument('--bump-version', action='store_true', help='set version to that of a local session')
    pi.add_argument('--no-sidecar', action='store_true', help='do not copy the sidecar folder')
    pi.add_argument('--with-history', action='store_true', help='register prompts in history.jsonl')
    pi.add_argument('--dry-run', action='store_true')
    pi.set_defaults(func=do_import)

    args = ap.parse_args()
    args.func(args)

if __name__ == '__main__':
    main()
