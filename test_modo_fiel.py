#!/usr/bin/env python3
"""
Modo fiel: a barra lateral do destino tem de ficar igual a da origem,
inclusive as sessoes fixadas.

O que torna isso possivel: pinnedOrder (no Local Storage) guarda o id
'local_<uuid>' de cada REGISTRO, nao o id da sessao. Sintetizar registros
novos gera ids novos e o pinned aponta para o nada. Copiando os registros
originais e importando com --keep-id, os ids continuam validos.
"""
import json
import os
import shutil
import tempfile
import unittest
import zipfile

import batch
import claude_session_port as csp


class Perfil(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.origem = os.path.join(self.tmp, 'origem', 'claude-code-sessions')
        self.rec_dir = os.path.join(self.origem, 'conta-1', 'grupo-1')
        os.makedirs(self.rec_dir)
        self.ls_origem = os.path.join(self.tmp, 'origem', 'Local Storage', 'leveldb')
        os.makedirs(self.ls_origem)
        with open(os.path.join(self.ls_origem, '000123.ldb'), 'wb') as fh:
            fh.write(b'\x00\x01{"pinnedOrder":["local_aaa","local_bbb"]}\x00')
        with open(os.path.join(self.ls_origem, 'LOCK'), 'wb') as fh:
            fh.write(b'')

        for nome, cwd in (('local_aaa', r'D:\proj'), ('local_bbb', r'D:\proj\sub')):
            json.dump({'sessionId': nome, 'cliSessionId': nome[6:],
                       'cwd': cwd, 'originCwd': cwd, 'title': 't-' + nome,
                       'isArchived': False},
                      open(os.path.join(self.rec_dir, nome + '.json'), 'w',
                           encoding='utf-8'))

        # o IndexedDB e o armazem que a interface le para saber o que esta
        # fixado; sem ele o pinned nao aparece por mais que os registros
        # tragam isStarred
        self.idb_origem = os.path.join(self.tmp, 'origem', 'IndexedDB',
                                       'https_claude.ai_0.indexeddb.leveldb')
        os.makedirs(self.idb_origem)
        with open(os.path.join(self.idb_origem, '000001.ldb'), 'wb') as fh:
            fh.write(b'\x00starred\x00local_aaa\x00')

        self.out = os.path.join(self.tmp, 'bundle')
        os.makedirs(self.out)
        self.destino = os.path.join(self.tmp, 'destino', 'claude-code-sessions')
        os.makedirs(os.path.join(self.destino, 'conta-1'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _exportar(self):
        return batch.export_app_profile(self.out, app_store=self.origem)

    def _remap(self, p):
        if p.startswith(r'D:\proj'):
            return '/home/f/proj' + p[len(r'D:\proj'):].replace('\\', '/')
        return None

    def test_exporta_registros_e_local_storage(self):
        n = self._exportar()
        self.assertEqual(n, 2)
        z = zipfile.ZipFile(os.path.join(self.out, batch.PROFILE_ZIP))
        nomes = z.namelist()
        self.assertIn('records/conta-1/grupo-1/local_aaa.json', nomes)
        self.assertIn('local-storage/000123.ldb', nomes)
        info = json.loads(z.read('profile.json'))
        self.assertEqual(info['records'], 2)
        self.assertEqual(info['accounts'], ['conta-1'])

    def test_local_storage_vai_byte_a_byte(self):
        self._exportar()
        z = zipfile.ZipFile(os.path.join(self.out, batch.PROFILE_ZIP))
        self.assertEqual(z.read('local-storage/000123.ldb'),
                         open(os.path.join(self.ls_origem, '000123.ldb'), 'rb').read())

    def test_import_preserva_o_id_do_registro(self):
        self._exportar()
        batch.import_app_profile(self.out, self._remap, app_store=self.destino)
        dest = os.path.join(self.destino, 'conta-1', 'grupo-1', 'local_aaa.json')
        self.assertTrue(os.path.isfile(dest))
        o = json.load(open(dest, encoding='utf-8'))
        self.assertEqual(o['sessionId'], 'local_aaa')      # pinnedOrder continua valendo
        self.assertEqual(o['cliSessionId'], 'aaa')

    def test_import_reescreve_o_cwd(self):
        self._exportar()
        batch.import_app_profile(self.out, self._remap, app_store=self.destino)
        o = json.load(open(os.path.join(self.destino, 'conta-1', 'grupo-1',
                                        'local_bbb.json'), encoding='utf-8'))
        self.assertEqual(o['cwd'], '/home/f/proj/sub')
        self.assertEqual(o['originCwd'], '/home/f/proj/sub')

    def test_import_copia_o_local_storage_para_o_destino(self):
        self._exportar()
        batch.import_app_profile(self.out, self._remap, app_store=self.destino)
        dest = os.path.join(os.path.dirname(self.destino), 'Local Storage',
                            'leveldb', '000123.ldb')
        self.assertTrue(os.path.isfile(dest))
        self.assertIn(b'pinnedOrder', open(dest, 'rb').read())

    def test_cwd_fora_do_mapa_fica_intacto(self):
        json.dump({'sessionId': 'local_ccc', 'cliSessionId': 'ccc',
                   'cwd': r'E:\outro', 'originCwd': r'E:\outro', 'title': 'x'},
                  open(os.path.join(self.rec_dir, 'local_ccc.json'), 'w',
                       encoding='utf-8'))
        self._exportar()
        batch.import_app_profile(self.out, self._remap, app_store=self.destino)
        o = json.load(open(os.path.join(self.destino, 'conta-1', 'grupo-1',
                                        'local_ccc.json'), encoding='utf-8'))
        self.assertEqual(o['cwd'], r'E:\outro')

    def test_dry_run_nao_escreve(self):
        self._exportar()
        batch.import_app_profile(self.out, self._remap, app_store=self.destino, dry=True)
        self.assertFalse(os.path.exists(os.path.join(self.destino, 'conta-1',
                                                     'grupo-1', 'local_aaa.json')))

    def test_sem_perfil_no_bundle_nao_quebra(self):
        n = batch.import_app_profile(self.out, self._remap, app_store=self.destino)
        self.assertEqual(n, (0, 0))

    def test_exporta_o_indexeddb(self):
        self._exportar()
        z = zipfile.ZipFile(os.path.join(self.out, batch.PROFILE_ZIP))
        self.assertIn('indexeddb/https_claude.ai_0.indexeddb.leveldb/000001.ldb',
                      z.namelist())
        self.assertEqual(json.loads(z.read('profile.json'))['indexedDB'], 1)

    def test_import_restaura_o_indexeddb(self):
        self._exportar()
        batch.import_app_profile(self.out, self._remap, app_store=self.destino)
        dest = os.path.join(os.path.dirname(self.destino), 'IndexedDB',
                            'https_claude.ai_0.indexeddb.leveldb', '000001.ldb')
        self.assertTrue(os.path.isfile(dest))
        self.assertIn(b'starred', open(dest, 'rb').read())

    def test_indexeddb_vai_byte_a_byte(self):
        # a serializacao do Blink prefixa cada string pelo tamanho; reescrever
        # um caminho por outro de tamanho diferente corromperia o registro
        self._exportar()
        batch.import_app_profile(self.out, self._remap, app_store=self.destino)
        dest = os.path.join(os.path.dirname(self.destino), 'IndexedDB',
                            'https_claude.ai_0.indexeddb.leveldb', '000001.ldb')
        self.assertEqual(open(dest, 'rb').read(),
                         open(os.path.join(self.idb_origem, '000001.ldb'), 'rb').read())

    def test_destino_com_indexeddb_proprio_e_salvo_antes(self):
        alvo = os.path.join(os.path.dirname(self.destino), 'IndexedDB',
                            'https_claude.ai_0.indexeddb.leveldb')
        os.makedirs(alvo)
        with open(os.path.join(alvo, 'antigo.ldb'), 'wb') as fh:
            fh.write(b'do destino')
        self._exportar()
        batch.import_app_profile(self.out, self._remap, app_store=self.destino)
        self.assertTrue(os.path.isfile(os.path.join(alvo + '.antes-do-import',
                                                    'antigo.ldb')))
        # e a pasta ficou so com os arquivos da origem
        self.assertEqual(sorted(os.listdir(alvo)), ['000001.ldb'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
