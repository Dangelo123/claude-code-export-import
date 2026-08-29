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

    # Modo fiel: em vez de sintetizar registros novos, restaura os originais e
    # mantem os ids das sessoes. E o que faz o pinned sobreviver, porque
    # pinnedOrder guarda o id local_<uuid> de cada registro, nao o da sessao.
    fiel = args.faithful and os.path.isfile(os.path.join(src_dir, PROFILE_ZIP))
    if args.faithful and not fiel:
        print("[warn] --faithful pedido, mas o bundle nao tem " + PROFILE_ZIP
              + " (exportado por uma versao antiga); seguindo no modo comum)")
    if fiel:
        print("[info] modo fiel: ids preservados; registros e barra lateral"
              " vem da origem")
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
            keep_id=args.keep_id or fiel,
            keep_paths=True,
            git_branch=None, title_suffix=None, title=None,
            app_store=args.app_store, no_app_index=args.no_app_index or fiel, index_all=args.index_all,
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

        if fiel:
            import_app_profile(src_dir, remap, args.app_store, args.dry_run,
                               mapa_bruto=mapping, para_posix=to_posix)

        restaurados = import_config(home, src_dir, rewrite, args.app_store,
                                    args.dry_run)
        if restaurados and not args.dry_run:
            # settings.json da origem sobrescreveu o que ensure_retention criou;
            # reaplica para nao voltar a podar transcript com mais de 30 dias
            ensure_retention(home, args.retention_days)
            print("     >>> feche e reabra o app: o Local Storage so e relido"
                  " na inicializacao")

    print("\n[done] imported %d, failed/skipped %d" % (ok, fail))


# ------------------------------------------------------------------- extras
# Um transcript nao e tudo. Ao lado das sessoes vivem artefatos que o usuario
# espera reencontrar do outro lado: as memorias por projeto e o CLAUDE.md
# global. Eles nao sao referenciados pelo .jsonl, entao viajam a parte -- e
# tambem precisam do rewrite de caminho, porque citam paths do Windows.
EXTRAS_ZIP = '_extras.zip'
PROJECT_EXTRA_DIRS = ('memory', 'plans')
HOME_EXTRA_FILES = ('CLAUDE.md',)

CONFIG_ZIP = '_config.zip'
# ~/.claude.json guarda os servidores MCP e as permissoes por projeto; sem ele
# a maquina nova comeca com as integracoes todas por reconfigurar
CONFIG_HOME_FILES = ('.claude.json',)
CONFIG_CLAUDE_FILES = ('settings.json', 'settings.local.json')
# arquivos do perfil do app que sao do APARELHO, nao do usuario: levar um
# token de sessao ou o registro de dispositivo para outra maquina nao ajuda e
# espalha credencial
CONFIG_PROFILE_SKIP = ('buddy-tokens.json', 'ant-device-registry.json')


def _tem_credencial(texto):
    """Heuristica: o arquivo carrega segredo que vai viajar no bundle?"""
    return bool(re.search(r'"[^"]*(secret|token|password|api_?key)[^"]*"\s*:\s*"[^"]{8,}"',
                          texto, re.I))


def export_config(home, out_dir, app_store=None):
    """
    Leva a configuracao: servidores MCP, permissoes, ajustes do app.

    Fica fora do fluxo normal (--with-config) porque estes arquivos costumam
    guardar credencial de verdade -- variaveis de ambiente de servidor MCP,
    por exemplo -- e o bundle acaba num disco externo. Quem pede, e avisado do
    que vai junto.
    """
    import zipfile
    path = os.path.join(out_dir, CONFIG_ZIP)
    lar = os.path.expanduser('~')
    prof = csp.app_profile_dir(app_store)
    com_segredo, n = [], 0

    def guarda(z, origem, arcname):
        nonlocal n
        if not os.path.isfile(origem):
            return
        z.write(origem, arcname=arcname)
        n += 1
        try:
            if _tem_credencial(open(origem, encoding='utf-8', errors='ignore').read()):
                com_segredo.append(arcname)
        except Exception:
            pass

    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for fn in CONFIG_HOME_FILES:
            guarda(z, os.path.join(lar, fn), 'home/' + fn)
        for fn in CONFIG_CLAUDE_FILES:
            guarda(z, os.path.join(home, fn), 'claude/' + fn)
        if prof and os.path.isdir(prof):
            for fn in sorted(os.listdir(prof)):
                if not fn.endswith('.json') or fn in CONFIG_PROFILE_SKIP:
                    continue
                guarda(z, os.path.join(prof, fn), 'profile/' + fn)

    print("[ok] config: %d arquivos -> %s" % (n, path))
    if com_segredo:
        print("[!!] estes carregam credencial e viajam no bundle:")
        for a in com_segredo:
            print("        " + a)
    return n


def import_config(home, src_dir, rewrite, app_store=None, dry=False):
    """Restaura a configuracao, reescrevendo os caminhos que ela guarda."""
    import zipfile
    path = os.path.join(src_dir, CONFIG_ZIP)
    if not os.path.isfile(path):
        return 0
    lar = os.path.expanduser('~')
    prof = csp.app_profile_dir(app_store)
    n = 0
    with zipfile.ZipFile(path) as z:
        for nome in z.namelist():
            if nome.startswith('home/'):
                dest = os.path.join(lar, nome[len('home/'):])
            elif nome.startswith('claude/'):
                dest = os.path.join(home, nome[len('claude/'):])
            elif nome.startswith('profile/') and prof:
                dest = os.path.join(prof, nome[len('profile/'):])
            else:
                continue
            dados = z.read(nome)
            try:
                # sao .json: o caminho aparece escapado, inclusive nas CHAVES
                # de "projects" -- reescrever o texto inteiro cobre os dois
                dados = rewrite(dados.decode('utf-8')).encode('utf-8')
            except UnicodeDecodeError:
                pass
            if not dry:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, 'wb') as fh:
                    fh.write(dados)
            n += 1
    print("[ok] config: %d arquivos restaurados" % n)
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
    print("[ok] extras: %d arquivos -> %s" % (n, path))
    return n


PROFILE_ZIP = '_app-profile.zip'


def export_app_profile(out_dir, app_store=None):
    """
    Leva o perfil do app: os registros local_*.json em si e o Local Storage.

    Recriar os registros do zero perde o que so existe neles -- e o Local
    Storage guarda pinnedOrder, que aponta para o id 'local_<uuid>' de cada
    registro. Se o destino gera ids novos, todo o pinned se perde. Copiando os
    registros originais e importando com --keep-id, os ids continuam validos e
    a barra lateral chega igual: fixadas, agrupamento e ordem.
    """
    import zipfile
    base, _, _ = csp.find_record_dir(app_store)
    if not base:
        bases = csp.candidate_app_store_bases(app_store)
        base = bases[0] if bases else None
    if not base or not os.path.isdir(base):
        print("[warn] app profile: nenhuma pasta claude-code-sessions encontrada")
        return 0

    ls = csp.local_storage_dir(app_store)
    path = os.path.join(out_dir, PROFILE_ZIP)
    n_rec = n_ls = 0
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
                conta = rel.split('/')[0]
                if conta not in info["accounts"]:
                    info["accounts"].append(conta)
        if ls:
            for fn in sorted(os.listdir(ls)):
                full = os.path.join(ls, fn)
                if not os.path.isfile(full):
                    continue
                try:
                    z.write(full, arcname='local-storage/' + fn)
                    n_ls += 1
                except (PermissionError, OSError):
                    # o LOCK do leveldb nao e legivel com o app aberto e nao
                    # faz falta: e um arquivo vazio recriado no destino
                    pass
        info["records"] = n_rec
        info["localStorage"] = n_ls
        z.writestr('profile.json', json.dumps(info, ensure_ascii=False, indent=2))
    print("[ok] perfil do app: %d registros + %d arquivos de Local Storage -> %s"
          % (n_rec, n_ls, path))
    return n_rec


def _reescrever_local_storage(ls_dir, mapa, para_posix):
    """
    Corrige os caminhos dentro do LevelDB copiado.

    Precisa ler o banco de verdade -- os valores ficam em blocos comprimidos
    com snappy, entao mexer nos bytes nao serve --, e para isso depende de
    plyvel. Sem ele o Local Storage ainda vale a pena: o pinned atravessa. So
    os caminhos e que ficam como estavam, e o aviso diz como resolver.
    """
    try:
        import localstorage_paths as lsp
    except Exception:
        return
    if not lsp.disponivel():
        print("[warn] plyvel ausente: o Local Storage foi copiado (o pinned")
        print("       atravessa), mas os caminhos guardados nele continuam")
        print("       apontando para a maquina de origem. Para corrigir:")
        print("         pacman -S python-plyvel   (ou apt install python3-plyvel)")
        print("         python3 localstorage_paths.py --leveldb '%s' --path-map ..."
              % ls_dir)
        return
    try:
        n, k, _ = lsp.reescrever(ls_dir, mapa, para_posix)
        print("[ok] Local Storage: %d chaves, %d com caminho reescrito" % (n, k))
    except Exception as e:
        print("[warn] nao consegui reescrever os caminhos do Local Storage: %s" % e)


def import_app_profile(src_dir, remap, app_store=None, dry=False,
                       mapa_bruto=None, para_posix=True):
    """
    Restaura os registros com o cwd reescrito e o Local Storage como esta.

    Os ids nao mudam -- e isso que faz o pinned continuar valendo. O Local
    Storage vai byte a byte porque e um LevelDB: reescrever seria arriscado, e
    nada la dentro guarda caminho de arquivo (so ids e preferencias de UI).
    """
    import zipfile
    src = os.path.join(src_dir, PROFILE_ZIP)
    if not os.path.isfile(src):
        return 0, 0

    bases = csp.candidate_app_store_bases(app_store)
    base = next((b for b in bases if os.path.isdir(b)), None)
    if not base:
        print("[warn] perfil do app: destino sem claude-code-sessions; "
              "abra o app e faca login antes")
        return 0, 0
    ls_dest = os.path.join(csp.app_profile_dir(app_store), 'Local Storage', 'leveldb')

    # O Local Storage do destino e substituido inteiro. Se o app guardar ali
    # algo cifrado pelo cofre do sistema (DPAPI no Windows, keyring no Linux),
    # o valor vindo de outra maquina nao decifra e o login cai. Guardar uma
    # copia deixa isso reversivel.
    if not dry and os.path.isdir(ls_dest):
        bak = ls_dest + ".antes-do-import"
        if not os.path.isdir(bak):
            import shutil
            shutil.copytree(ls_dest, bak, ignore_dangling_symlinks=True)
            print("[ok] copia do Local Storage do destino em %s" % bak)

        # Esvazia antes de escrever: misturar arquivos dos dois lados deixa .ldb
        # orfaos e um CURRENT apontando para o MANIFEST do outro conjunto. O
        # destino tem de ficar com exatamente os arquivos da origem.
        if not dry and os.path.isdir(ls_dest):
            for fn in os.listdir(ls_dest):
                alvo = os.path.join(ls_dest, fn)
                if os.path.isfile(alvo):
                    try:
                        os.remove(alvo)
                    except OSError:
                        pass

    n_rec = n_ls = 0
    ilegiveis = []
    with zipfile.ZipFile(src) as z:
        for nome in z.namelist():
            if nome.startswith('records/') and nome.endswith('.json'):
                bruto = z.read(nome)
                try:
                    o = json.loads(bruto.decode('utf-8'))
                except Exception:
                    # Registro ilegivel ja na origem: um desligamento sujo
                    # deixa o arquivo com o tamanho certo e so zeros dentro.
                    # Copiar como esta mantem o espelho fiel (nao ha caminho a
                    # reescrever) -- mas vale avisar, porque pode ser uma
                    # sessao fixada que tambem nao abre na maquina de origem.
                    ilegiveis.append(os.path.basename(nome))
                    if not dry:
                        dest = os.path.join(base, *nome[len('records/'):].split('/'))
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with open(dest, 'wb') as fh:
                            fh.write(bruto)
                    n_rec += 1
                    continue
                for campo in ('cwd', 'originCwd', 'worktreePath', 'planPath'):
                    v = o.get(campo)
                    if isinstance(v, str) and v:
                        novo_v = remap(v)
                        if novo_v:
                            o[campo] = novo_v
                dest = os.path.join(base, *nome[len('records/'):].split('/'))
                if not dry:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, 'w', encoding='utf-8', newline=chr(10)) as fh:
                        json.dump(o, fh, ensure_ascii=False, indent=2)
                n_rec += 1
            elif nome.startswith('local-storage/') and not nome.endswith('/'):
                if not dry:
                    os.makedirs(ls_dest, exist_ok=True)
                    with open(os.path.join(ls_dest, os.path.basename(nome)), 'wb') as fh:
                        fh.write(z.read(nome))
                n_ls += 1
    # O Local Storage tambem guarda caminho de arquivo: uma chave
    # cc-session-cwd-* por sessao e blobs JSON com o agrupamento por pasta.
    # Copiado cru, o app pede para confiar num caminho que nao existe aqui.
    if n_ls and not dry:
        _reescrever_local_storage(ls_dest, mapa_bruto, para_posix)

    print("[ok] perfil do app: %d registros restaurados, %d arquivos de Local Storage"
          % (n_rec, n_ls))
    if ilegiveis:
        print("[warn] %d registro(s) ilegiveis na origem, copiados como estao:"
              % len(ilegiveis))
        for fn in ilegiveis[:5]:
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
    pe.add_argument('--with-config', action='store_true',
                    help='leva tambem ~/.claude.json, settings.json e a config'
                         ' do app (servidores MCP, permissoes). Pode conter'
                         ' credencial: o comando avisa quais arquivos.')
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
                    help='reproduz a barra lateral da origem: mantem os ids,'
                         ' restaura os registros originais e o estado de'
                         ' sessoes fixadas (exige o app fechado no destino)')
    pi.add_argument('--index-all', action='store_true',
                    help='cria registro tambem para sessoes que nao'
                         ' apareciam na interface da origem')
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
