#!/usr/bin/env python3
"""
Organiza a barra lateral do Claude Desktop depois de uma migracao.

Tres coisas que o transporte fiel, por ser fiel, nao faz:

  grupos     o app tem grupos personalizados e quase ninguem sabe. Enquanto
             nao existe nenhum, a barra agrupa por diretorio -- o que espalha
             worktrees e mistura trabalho com coisa pessoal. Criar grupos troca
             o agrupamento (`groupByByMode.code = "custom"`), entao TODA sessao
             precisa de grupo: o que sobrar cai em "Ungrouped".
  fixadas    fixar 40% das sessoes e quase nao fixar.
  mortas     registro cujo transcript ja foi podado abre vazio. Arquivar tira
             da lista sem apagar nada.

Depende de plyvel (mesmo motivo de localstorage_paths.py: os valores vivem em
blocos comprimidos). O app tem de estar FECHADO.
"""
import argparse
import glob
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claude_session_port as csp          # noqa: E402
import localstorage_paths as lsp           # noqa: E402

CHAVE_LOJA = b'dframe-store'


def _achar_loja(db):
    """(chave, estado, envelope, codificacao) da loja da barra lateral."""
    for k, v in db.iterator():
        if CHAVE_LOJA not in k or not v:
            continue
        texto, cod = lsp._decodifica(v)
        if texto is None:
            continue
        try:
            env = json.loads(texto)
        except Exception:
            continue
        if 'pinnedOrder' in json.dumps(env)[:4000] or 'state' in env:
            return k, env.get('state', env), env, cod
    return None, None, None, None


def aplicar(plano, app_store=None, dry=False):
    import plyvel

    base = next((b for b in csp.candidate_app_store_bases(app_store)
                 if os.path.isdir(b)), None)
    if not base:
        sys.exit('[error] destino sem claude-code-sessions')
    ls_dir = os.path.join(csp.app_profile_dir(app_store),
                          'Local Storage', 'leveldb')
    if not os.path.isdir(ls_dir):
        sys.exit('[error] destino sem Local Storage')

    registros = {}
    for f in glob.glob(os.path.join(base, '**', 'local_*.json'), recursive=True):
        registros[os.path.splitext(os.path.basename(f))[0]] = f

    home = csp.default_claude_home()
    vivos = {os.path.splitext(os.path.basename(f))[0]
             for f in glob.glob(os.path.join(home, 'projects', '*', '*.jsonl'))}

    # ---------------------------------------------------- registros: pin/arquivo
    desafixar = set(plano.get('desafixar') or [])
    n_unpin = n_arq = 0
    for rid, caminho in sorted(registros.items()):
        try:
            o = json.load(open(caminho, encoding='utf-8'))
        except Exception:
            continue                      # ilegivel na origem; nao mexe
        mudou = False
        if rid in desafixar and o.get('isStarred'):
            o['isStarred'] = False
            mudou = True
            n_unpin += 1
        if plano.get('arquivar_mortas') and not o.get('isArchived') \
                and o.get('cliSessionId') not in vivos:
            o['isArchived'] = True
            mudou = True
            n_arq += 1
        if mudou and not dry:
            with open(caminho, 'w', encoding='utf-8', newline='\n') as fh:
                json.dump(o, fh, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------ grupos
    grupos = plano.get('grupos') or {}
    db = plyvel.DB(ls_dir, create_if_missing=False)
    try:
        chave, estado, envelope, cod = _achar_loja(db)
        if estado is None:
            sys.exit('[error] nao achei a loja da barra lateral no Local Storage')

        escopo = estado.get('lastSidebarScopeKey')
        if not escopo:
            porescopo = estado.get('customGroupsByScope') or {}
            escopo = next(iter(porescopo), None)
        if not escopo:
            sys.exit('[error] sem escopo de barra lateral; abra o app uma vez')

        defs, atrib, ordem = [], {}, {}
        for nome, ids in grupos.items():
            gid = 'cg-' + str(uuid.uuid4())
            defs.append({'id': gid, 'name': nome})
            chaves = ['code:' + i for i in ids if i in registros]
            ordem[gid] = chaves
            for c in chaves:
                atrib[c] = gid

        estado['customGroupsByScope'] = {
            escopo: {'groups': defs, 'assignments': atrib, 'order': ordem}}
        # sem isto a barra continua agrupando por diretorio e os grupos ficam
        # criados porem invisiveis
        modo = dict(estado.get('groupByByMode') or {})
        modo['code'] = 'custom'
        estado['groupByByMode'] = modo

        if not dry:
            if 'state' in envelope:
                envelope['state'] = estado
            else:
                envelope = estado
            db.put(chave, lsp._codifica(json.dumps(envelope, ensure_ascii=False), cod))
    finally:
        db.close()

    print("  grupos criados     : %d" % len(defs))
    for g in defs:
        print("     %-18s %3d sessoes" % (g['name'], len(ordem[g['id']])))
    print("  desafixadas        : %d" % n_unpin)
    print("  mortas arquivadas  : %d" % n_arq)
    if dry:
        print("  (dry-run: nada foi escrito)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plano', required=True, help='JSON com grupos/desafixar')
    ap.add_argument('--app-store', default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not lsp.disponivel():
        sys.exit('[error] plyvel nao instalado (pacman -S python-plyvel)')
    with open(args.plano, encoding='utf-8') as fh:
        plano = json.load(fh)
    aplicar(plano, args.app_store, args.dry_run)


if __name__ == '__main__':
    main()
