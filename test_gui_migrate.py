#!/usr/bin/env python3
"""
Smoke test for the Migrate tab.

Tkinter needs a display, so this is skipped when there is none (headless CI on
Linux). Where a display exists it builds the real window, drives the widgets and
checks the wiring: the path-map editor loads rows from a bundle, suggestions
fill only empty fields, and the collected map is what the core would receive.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import tkinter as tk
except Exception as e:                                   # noqa: BLE001
    print("SKIP: tkinter unavailable (%s)" % e)
    sys.exit(0)

try:
    _root = tk.Tk()
    _root.withdraw()
except Exception as e:                                   # noqa: BLE001
    print("SKIP: no display (%s)" % e)
    sys.exit(0)

import gui  # noqa: E402

fails = []


def check(name, cond, detail=""):
    if cond:
        print("  %-46s ok" % name)
    else:
        fails.append("%s%s" % (name, (": " + detail) if detail else ""))
        print("  %-46s FAIL" % name)


tmp = tempfile.mkdtemp(prefix='guimig_')
try:
    app = gui.App(_root)
    check("app constroi com a aba nova", app.tab_migrate is not None)
    check("editor de mapa comeca vazio", app.map_entries == [])

    # bundle falso, como o export-all deixaria
    bundle = os.path.join(tmp, 'bundle')
    os.makedirs(bundle)
    tmpl = {r"D:\Work\Alpha": "<FILL IN destination path>",
            r"C:\Users\X\Documents\Beta": "<FILL IN destination path>"}
    with open(os.path.join(bundle, 'path-map.template.json'), 'w', encoding='utf-8') as fh:
        json.dump(tmpl, fh)

    app._load_path_map(bundle)
    check("carrega uma linha por caminho de origem", len(app.map_entries) == 2,
          "carregou %d" % len(app.map_entries))

    # nada preenchido -> mapa vazio (o botao deve recusar)
    check("mapa vazio quando nada foi digitado", app._collect_map() == {})

    # sugestao preenche os vazios
    app._suggest_dests()
    m = app._collect_map()
    check("sugestao preenche os dois campos", len(m) == 2, str(m))
    check("sugestao usa o nome final da pasta",
          any(v.replace("\\", "/").endswith("/Alpha") for v in m.values()), str(m))

    # sugestao NAO sobrescreve o que o usuario digitou
    src0, e0 = app.map_entries[0]
    e0.delete(0, "end")
    e0.insert(0, "/home/me/escolhido-a-mao")
    app._suggest_dests()
    check("sugestao respeita valor ja digitado",
          app._collect_map()[src0] == "/home/me/escolhido-a-mao")

    # campo em branco = projeto pulado
    src1, e1 = app.map_entries[1]
    e1.delete(0, "end")
    m = app._collect_map()
    check("campo vazio deixa o projeto de fora", src1 not in m and src0 in m, str(m))

    # pasta sem template deve avisar, nao explodir
    empty = os.path.join(tmp, 'vazia')
    os.makedirs(empty)
    app._load_path_map(empty)
    check("pasta sem template nao quebra", app.map_entries == [])

    # --- as opcoes novas ---
    # "reproduzir a barra lateral" vem LIGADO de proposito: sem ela os ids
    # mudam e todo pinned aponta para o nada, que foi o defeito da migracao real
    check("reproduzir a barra lateral vem ligado", app.mig_faithful.get() is True)
    check("listar sessoes ocultas vem desligado", app.mig_index_all.get() is False)
    check("levar config vem desligado", app.mig_with_config.get() is False)

    check("import-all aceita faithful", 'faithful' in gui._IMPORT_ALL_DEFAULTS)
    check("import-all aceita index_all", 'index_all' in gui._IMPORT_ALL_DEFAULTS)
    check("export-all aceita with_config", 'with_config' in gui._EXPORT_ALL_DEFAULTS)

    # o que a GUI monta tem de casar com o que o nucleo le
    import inspect

    import batch
    for fn, padroes in ((batch.do_export_all, gui._EXPORT_ALL_DEFAULTS),
                        (batch.do_import_all, gui._IMPORT_ALL_DEFAULTS)):
        fonte = inspect.getsource(fn)
        faltando = [k for k in padroes if ('args.' + k) not in fonte]
        check("%s usa tudo que a GUI passa" % fn.__name__, not faltando, str(faltando))

    print()
    if fails:
        print("FAILURES (%d):" % len(fails))
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("all tests passed")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        _root.destroy()
    except Exception:
        pass
