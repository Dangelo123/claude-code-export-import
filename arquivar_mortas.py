#!/usr/bin/env python3
"""
Arquiva as entradas da barra lateral cujo transcript ja nao existe.

O registro de uma sessao sobrevive a poda de 30 dias que apaga o transcript,
entao a lista acumula entradas que abrem vazias: 49 de 129 na instalacao que
originou este script. Arquivar tira da lista sem apagar nada -- um clique em
"Unarchive" desfaz.

## O que este script deliberadamente NAO faz

Fixar e agrupar tambem parecem estado de registro, mas nao sao. O app le os
dois do IndexedDB; o `local_*.json` e um espelho que ele escreve, nao le.
Medido: depois de gravar `isStarred: false` em 13 registros e montar 4 grupos
com 76 atribuicoes no Local Storage, o contador do proprio app continuou em
`code.pinned: 31` e mostrando apenas o grupo criado pela interface. Ja
`isArchived` no registro E respeitado -- dai este script existir.

O IndexedDB tambem nao se escreve de fora: usa o comparador `idb_cmp1` do
Chromium, que biblioteca comum de leveldb recusa abrir.

Para fixar e agrupar, o caminho e organizar uma vez pela interface: o modo
fiel copia o IndexedDB byte a byte, entao isso atravessa a migracao intacto
(verificado com 32 sessoes fixadas chegando na ordem certa).

Feche o app antes de rodar.
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claude_session_port as csp          # noqa: E402


def arquivar(claude_home=None, app_store=None, dry=False):
    base = next((b for b in csp.candidate_app_store_bases(app_store)
                 if os.path.isdir(b)), None)
    if not base:
        sys.exit('[error] nao achei claude-code-sessions')

    home = csp.default_claude_home(claude_home)
    vivos = {os.path.splitext(os.path.basename(f))[0]
             for f in glob.glob(os.path.join(home, 'projects', '*', '*.jsonl'))}

    total = n = ja_arq = com_tx = 0
    mortas = []
    for f in sorted(glob.glob(os.path.join(base, '**', 'local_*.json'),
                              recursive=True)):
        try:
            o = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue                      # ilegivel na origem: nao mexe
        total += 1
        if o.get('isArchived'):
            ja_arq += 1
            continue
        if o.get('cliSessionId') in vivos:
            com_tx += 1
            continue
        mortas.append(o.get('title') or os.path.basename(f))
        n += 1
        if not dry:
            o['isArchived'] = True
            with open(f, 'w', encoding='utf-8', newline='\n') as fh:
                json.dump(o, fh, ensure_ascii=False, indent=2)

    print("  registros            : %d" % total)
    print("  com transcript       : %d" % com_tx)
    print("  ja arquivados        : %d" % ja_arq)
    print("  arquivadas agora%s: %d" % ('  (dry-run)' if dry else '     ', n))
    for t in mortas[:15]:
        print("     %s" % t[:66])
    if len(mortas) > 15:
        print("     ... e mais %d" % (len(mortas) - 15))
    return n


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--claude-home', default=None)
    ap.add_argument('--app-store', default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    arquivar(args.claude_home, args.app_store, args.dry_run)


if __name__ == '__main__':
    main()
