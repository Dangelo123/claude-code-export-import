#!/usr/bin/env python3
"""
Tests for extras migration (per-project memory/ + home-level CLAUDE.md).

A transcript is not the whole install. Memories and CLAUDE.md live beside the
sessions, are never referenced by a .jsonl, and quote Windows paths in plain
markdown -- so they need their own transport and their own rewriter. The first
implementation shipped without them and left 187 memory files behind; the
second shipped with the JSON rewriter and left 105 of them with Windows paths.
Both regressions are covered here.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import batch  # noqa: E402
import claude_session_port as csp  # noqa: E402

WIN_ROOT = r"D:\Proj"
WIN_GTD = r"C:\Users\Someone\Documents\GTD_Project"
POSIX_ROOT = "/home/tester/proj"
POSIX_GTD = "/home/tester/gtd"
MAP = {WIN_ROOT: POSIX_ROOT, WIN_GTD: POSIX_GTD}

fails = []


def check(name, cond, detail=""):
    if cond:
        print("  %-46s ok" % name)
    else:
        fails.append("%s%s" % (name, (": " + detail) if detail else ""))
        print("  %-46s FAIL" % name)


tmp = tempfile.mkdtemp(prefix='extras_')
try:
    src_home = os.path.join(tmp, 'src', '.claude')
    dst_home = os.path.join(tmp, 'dst', '.claude')
    bundles = os.path.join(tmp, 'bundles')
    os.makedirs(bundles)

    # ---------------- origem: memorias + CLAUDE.md + um binario -------------
    p_root = os.path.join(src_home, 'projects', csp.enc_project(WIN_ROOT), 'memory')
    p_wt = os.path.join(src_home, 'projects',
                        csp.enc_project(WIN_ROOT + r"\.claude\worktrees\wt-1"), 'memory')
    p_gtd = os.path.join(src_home, 'projects', csp.enc_project(WIN_GTD), 'memory')
    for d in (p_root, p_wt, p_gtd):
        os.makedirs(d)

    with open(os.path.join(p_root, 'MEMORY.md'), 'w', encoding='utf-8') as fh:
        fh.write("# Index\n"
                 "- [Setup](setup.md) — o repo fica em %s\\src\n"
                 "- caminho nao mapeado: E:\\Outro\\Lugar\n" % WIN_ROOT)
    with open(os.path.join(p_root, 'setup.md'), 'w', encoding='utf-8') as fh:
        fh.write("rodar em `%s` e ver %s\\docs\\a.md\n" % (WIN_ROOT, WIN_ROOT))
    with open(os.path.join(p_wt, 'wt.md'), 'w', encoding='utf-8') as fh:
        fh.write("worktree de %s\n" % WIN_ROOT)
    with open(os.path.join(p_gtd, 'gtd.md'), 'w', encoding='utf-8') as fh:
        fh.write("projeto em %s\n" % WIN_GTD)
    with open(os.path.join(src_home, 'CLAUDE.md'), 'w', encoding='utf-8') as fh:
        fh.write("instrucoes globais\nprojeto principal: %s\n" % WIN_ROOT)
    # binario: nao pode ser corrompido pelo rewrite
    with open(os.path.join(p_root, 'blob.bin'), 'wb') as fh:
        fh.write(bytes(range(256)))

    n = batch.export_extras(src_home, bundles)
    check("export empacota todos os extras", n == 6, "empacotou %d, esperado 6" % n)
    check("_extras.zip criado", os.path.isfile(os.path.join(bundles, batch.EXTRAS_ZIP)))

    # ---------------- destino: um arquivo ja existente ----------------------
    dst_proj = os.path.join(dst_home, 'projects', csp.enc_project(POSIX_ROOT), 'memory')
    os.makedirs(dst_proj)
    keep = os.path.join(dst_proj, 'MEMORY.md')
    with open(keep, 'w', encoding='utf-8') as fh:
        fh.write("EU JA ESTAVA AQUI\n")

    rw = batch.build_rewriter(MAP, to_posix=True)
    prw = batch.build_plain_rewriter(MAP, to_posix=True)
    slug_map = dict((csp.enc_project(o), csp.enc_project(nn)) for o, nn in MAP.items())

    def remap_slug(s):
        if s in slug_map:
            return slug_map[s]
        for o, nn in sorted(slug_map.items(), key=lambda kv: -len(kv[0])):
            if s.startswith(o):
                return nn + s[len(o):]
        return None

    batch.import_extras(dst_home, bundles, rw, remap_slug, plain_rewrite=prw)

    # ---------------- verificacoes -----------------------------------------
    def read(*parts):
        p = os.path.join(dst_home, *parts)
        return open(p, encoding='utf-8').read() if os.path.isfile(p) else None

    proj_slug = csp.enc_project(POSIX_ROOT)
    wt_slug = csp.enc_project(POSIX_ROOT + "/.claude/worktrees/wt-1")
    gtd_slug = csp.enc_project(POSIX_GTD)

    setup = read('projects', proj_slug, 'memory', 'setup.md')
    check("memoria restaurada no slug remapeado", setup is not None)
    check("path convertido em markdown (texto plano)",
          setup is not None and POSIX_ROOT in setup and "D:\\" not in setup,
          repr(setup))

    wt = read('projects', wt_slug, 'memory', 'wt.md')
    check("worktree remapeado por prefixo", wt is not None and POSIX_ROOT in wt)

    gtd = read('projects', gtd_slug, 'memory', 'gtd.md')
    check("segunda raiz remapeada", gtd is not None and POSIX_GTD in gtd)

    cl = read('CLAUDE.md')
    check("CLAUDE.md global restaurado", cl is not None)
    check("CLAUDE.md com path convertido",
          cl is not None and POSIX_ROOT in cl and "D:\\" not in cl)

    # arquivo pre-existente nao pode ser sobrescrito
    check("nao sobrescreve arquivo ja existente",
          read('projects', proj_slug, 'memory', 'MEMORY.md') == "EU JA ESTAVA AQUI\n")

    # caminho fora do mapa fica intacto (nao inventar destino)
    idx_src = open(os.path.join(p_root, 'MEMORY.md'), encoding='utf-8').read()
    check("origem mantinha o path nao mapeado", "E:\\Outro\\Lugar" in idx_src)

    # binario intacto
    blob = os.path.join(dst_home, 'projects', proj_slug, 'memory', 'blob.bin')
    check("binario nao corrompido pelo rewrite",
          os.path.isfile(blob) and open(blob, 'rb').read() == bytes(range(256)))

    print()
    if fails:
        print("FAILURES (%d):" % len(fails))
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("all tests passed")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
