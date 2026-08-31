#!/usr/bin/env python3
"""
A reescrita de caminhos tem de alcancar o sidecar da sessao.

O que passou batido: `_deep_rewrite_files` recebia o diff antes/depois do
diretorio do projeto -- que traz ARQUIVOS e DIRETORIOS -- e descartava tudo que
nao terminasse em .jsonl. O diretorio do sidecar caia nessa peneira, e com ele
os transcripts de subagent e workflow. Numa migracao real: 2774 de 2959
aninhados ficaram com o caminho da maquina de origem.

Nao quebrava o retomar (o cwd que a sessao usa esta no arquivo de topo), o que
explica ter passado por toda a bateria anterior sem sinal.
"""
import json
import os
import shutil
import tempfile
import unittest

import batch

BS = chr(92)
MAPA = {'D:' + BS + 'proj': '/home/eu/proj'}


def linha(caminho):
    return json.dumps({'type': 'user', 'cwd': caminho,
                       'message': {'content': 'olha ' + caminho}}) + '\n'


class RewriteAninhado(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dest = os.path.join(self.tmp, '-home-eu-proj')
        # sidecar com subagent e workflow, como o app cria
        os.makedirs(os.path.join(self.dest, 'sess1', 'subagents', 'workflows'))
        self.topo = os.path.join(self.dest, 'sess1.jsonl')
        self.sub = os.path.join(self.dest, 'sess1', 'subagents',
                                'workflows', 'agent-a1.jsonl')
        self.fundo = os.path.join(self.dest, 'sess1', 'subagents',
                                  'workflows', 'journal.jsonl')
        for p in (self.topo, self.sub, self.fundo):
            with open(p, 'w', encoding='utf-8') as fh:
                # caminho CRU: quem escapa e o json.dumps, igual a um
                # transcript de verdade. Passar ja escapado gera D:\\\\proj no
                # arquivo, que nenhuma regra casa -- foi o que quebrou o teste.
                fh.write(linha('D:' + BS + 'proj' + BS + 'src'))
        # binario no sidecar: nao pode derrubar a reescrita
        self.bin = os.path.join(self.dest, 'sess1', 'anexo.pdf')
        with open(self.bin, 'wb') as fh:
            fh.write(bytes([0, 1, 2, 255, 254]))
        self.rw = batch.build_rewriter(MAPA, True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rodar(self, nomes=None):
        batch._deep_rewrite_files(self.dest,
                                  nomes if nomes is not None
                                  else sorted(os.listdir(self.dest)),
                                  self.rw)

    def _tem_antigo(self, p):
        return 'D:' in open(p, encoding='utf-8', errors='ignore').read()

    def test_topo_reescrito(self):
        self._rodar()
        self.assertFalse(self._tem_antigo(self.topo))

    def test_subagent_reescrito(self):
        self._rodar()
        self.assertFalse(self._tem_antigo(self.sub), 'sidecar ficou com caminho antigo')

    def test_arquivo_no_fundo_reescrito(self):
        self._rodar()
        self.assertFalse(self._tem_antigo(self.fundo))

    def test_novo_caminho_presente(self):
        self._rodar()
        self.assertIn('/home/eu/proj', open(self.sub, encoding='utf-8').read())

    def test_binario_intacto(self):
        self._rodar()
        self.assertEqual(open(self.bin, 'rb').read(), bytes([0, 1, 2, 255, 254]))

    def test_so_mexe_no_que_veio_no_diff(self):
        # sessao que ja existia no destino nao pode ser tocada
        outro = os.path.join(self.dest, 'preexistente.jsonl')
        with open(outro, 'w', encoding='utf-8') as fh:
            fh.write(linha('D:' + BS + 'proj' + BS + 'antigo'))
        antes = open(outro, encoding='utf-8').read()
        self._rodar(['sess1.jsonl', 'sess1'])       # diff sem o preexistente
        self.assertEqual(open(outro, encoding='utf-8').read(), antes)

    def test_idempotente(self):
        self._rodar()
        depois1 = open(self.sub, encoding='utf-8').read()
        self._rodar()
        self.assertEqual(open(self.sub, encoding='utf-8').read(), depois1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
