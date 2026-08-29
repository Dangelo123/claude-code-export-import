#!/usr/bin/env python3
"""
Reescreve os caminhos guardados no Local Storage do Claude Desktop.

Por que isto existe
-------------------
O estado da barra lateral -- quais sessoes estao fixadas, como estao agrupadas,
a largura -- fica no Local Storage do app, que e um LevelDB. Copia-lo garante
que o pinned atravesse a migracao, porque `pinnedOrder` guarda o id
`local_<uuid>` de cada registro.

So que o Local Storage tambem guarda caminho de arquivo: ha uma chave
`cc-session-cwd-local_<id>` por sessao e blobs JSON com caminhos escapados
(agrupamento por pasta). Copiado cru de um Windows para um Linux, o app pede
para confiar num caminho de disco C: ou D: que nao existe ali.

Reescrever exige ler o banco de verdade: os valores vivem em blocos
comprimidos com snappy dentro dos .ldb, entao varrer bytes nao resolve. Isso
depende de `plyvel`, que e opcional: sem ele o Local Storage ainda e copiado
(o pinned funciona), mas os caminhos ficam como estavam.

    Arch/CachyOS:  pacman -S python-plyvel
    Debian/Ubuntu: apt install python3-plyvel
    pip:           pip install plyvel   (precisa de libleveldb-dev)
"""
import json
import re
import sys

# valores do Local Storage do Chromium tem um byte de prefixo dizendo a
# codificacao do resto: 0 = UTF-16LE, 1 = Latin-1
UTF16, LATIN1 = 0, 1


def disponivel():
    try:
        import plyvel  # noqa: F401
        return True
    except Exception:
        return False


def _decodifica(v):
    """(texto, codificacao) ou (None, None) se nao for texto."""
    if not v:
        return None, None
    marca, corpo = v[0], v[1:]
    try:
        if marca == UTF16:
            return corpo.decode('utf-16-le'), UTF16
        if marca == LATIN1:
            return corpo.decode('latin-1'), LATIN1
    except Exception:
        pass
    return None, None


def _codifica(texto, codificacao):
    if codificacao == UTF16:
        return bytes([UTF16]) + texto.encode('utf-16-le')
    return bytes([LATIN1]) + texto.encode('latin-1')


def construir_reescritor(mapa, para_posix):
    """
    f(texto) -> texto, trocando prefixos mapeados.

    Cobre as duas formas em que um caminho aparece aqui: cru, numa chave
    `cc-session-cwd-*`, e escapado dentro de um blob JSON (o agrupamento da
    barra lateral). Sem a segunda, o agrupamento continua apontando para
    pastas do Windows.
    """
    BS = chr(92)
    variantes = []
    for antigo in sorted(mapa, key=len, reverse=True):
        novo = mapa[antigo]
        for forma in (antigo, antigo.replace(BS, BS * 2), antigo.replace(BS, '/')):
            variantes.append((re.compile(re.escape(forma)), novo))

    # Depois da troca de prefixo sobra a cauda com separador de Windows. So a
    # cauda que segue um destino recem-escrito e convertida, para nao tocar em
    # barra invertida que pertence a codigo ou a um escape.
    # Numa regex, uma barra invertida literal se escreve com duas.
    B2 = BS + BS
    cauda = '((?:' + B2 + '{1,2}[^' + B2 + '"' + "'" + '<>|*?' + BS + 'r' + BS + 'n]+)+)'
    caudas = [(re.compile(re.escape(d) + cauda), d) for d in set(mapa.values())]

    def reescreve(texto):
        for rx, dest in variantes:
            texto = rx.sub(lambda m, d=dest: d, texto)
        if para_posix:
            for rx, dest in caudas:
                texto = rx.sub(
                    lambda m, d=dest: d + m.group(1).replace(BS * 2, '/').replace(BS, '/'),
                    texto)
        return texto

    return reescreve


def reescrever(leveldb_dir, mapa, para_posix=True, dry=False):
    """
    Percorre o banco e reescreve todo valor de texto que contenha um prefixo
    mapeado. Devolve (chaves_examinadas, chaves_alteradas).
    """
    import plyvel

    reescreve = construir_reescritor(mapa, para_posix)
    db = plyvel.DB(leveldb_dir, create_if_missing=False)
    examinadas = alteradas = 0
    mudancas = []
    try:
        for chave, valor in db.iterator():
            examinadas += 1
            texto, codificacao = _decodifica(valor)
            if texto is None:
                continue
            novo = reescreve(texto)
            if novo != texto:
                alteradas += 1
                mudancas.append((chave, _codifica(novo, codificacao),
                                 chave.decode('latin-1', 'replace')))
        if not dry:
            with db.write_batch() as wb:
                for chave, valor, _ in mudancas:
                    wb.put(chave, valor)
    finally:
        db.close()
    return examinadas, alteradas, [m[2] for m in mudancas]


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--leveldb', required=True, help='pasta Local Storage/leveldb')
    ap.add_argument('--path-map', required=True)
    ap.add_argument('--windows-target', action='store_true',
                    help='o destino usa caminhos Windows (nao converte separador)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not disponivel():
        sys.exit("[error] plyvel nao instalado; veja o cabecalho deste arquivo")

    with open(args.path_map, encoding='utf-8') as fh:
        mapa = json.load(fh)

    n, k, chaves = reescrever(args.leveldb, mapa, not args.windows_target,
                              args.dry_run)
    print("  chaves examinadas: %d" % n)
    print("  chaves alteradas : %d%s" % (k, '  (dry-run)' if args.dry_run else ''))
    for c in chaves[:25]:
        print("    %s" % c[:100])
    if len(chaves) > 25:
        print("    ... e mais %d" % (len(chaves) - 25))


if __name__ == '__main__':
    main()
