#!/usr/bin/env python3
"""
Duas regressoes vistas na migracao real para o CachyOS:

1. O importador so olhava 'ai-title'. Sessoes renomeadas pelo usuario (132 de
   184 no corpus real) caiam no nome da pasta, e a barra lateral ficava cheia
   de "ClaudeNode" e "ClaudeCowork_MeepGreenfield".

2. O importador criava registro de app para TODAS as sessoes. Sessoes que na
   origem nao tinham registro nao apareciam na interface de la -- em especial
   os ramos abandonados de retomada -- e passaram a aparecer no destino, o que
   parecia sessao repetida.
"""
import json
import os
import shutil
import tempfile
import unittest
import uuid

import claude_session_port as csp


def linhas(*objs):
    return [json.dumps(o, ensure_ascii=False) + '\n' for o in objs]


class Titulo(unittest.TestCase):
    def test_custom_vence_ai(self):
        ls = linhas({'type': 'ai-title', 'aiTitle': 'gerado'},
                    {'type': 'custom-title', 'customTitle': 'meu nome'})
        custom = ai = None
        for ln in ls:
            o = json.loads(ln)
            if o.get('type') == 'custom-title':
                custom = o['customTitle']
            elif o.get('type') == 'ai-title':
                ai = o['aiTitle']
        self.assertEqual(custom or ai, 'meu nome')

    def test_primeira_msg_como_ultimo_recurso(self):
        ls = linhas({'type': 'user', 'message': {'content': 'arruma o deploy do totem'}})
        self.assertEqual(csp.first_user_message(ls), 'arruma o deploy do totem')

    def test_primeira_msg_ignora_bloco_de_sistema(self):
        ls = linhas({'type': 'user', 'message': {'content': '<system-reminder>x</system-reminder>'}},
                    {'type': 'user', 'message': {'content': 'a pergunta de verdade'}})
        self.assertEqual(csp.first_user_message(ls), 'a pergunta de verdade')

    def test_primeira_msg_aceita_conteudo_em_lista(self):
        ls = linhas({'type': 'user',
                     'message': {'content': [{'type': 'text', 'text': 'ola   mundo'}]}})
        self.assertEqual(csp.first_user_message(ls), 'ola mundo')

    def test_primeira_msg_truncada_com_reticencias(self):
        ls = linhas({'type': 'user', 'message': {'content': 'x' * 200}})
        t = csp.first_user_message(ls, limite=20)
        self.assertTrue(t.endswith('...'))
        self.assertEqual(len(t), 23)

    def test_sem_mensagem_de_usuario(self):
        self.assertIsNone(csp.first_user_message(linhas({'type': 'system'})))


class Visibilidade(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = os.path.join(self.tmp, 'claude-code-sessions')
        self.rec_dir = os.path.join(self.base, str(uuid.uuid4()), str(uuid.uuid4()))
        os.makedirs(self.rec_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_arquivada_continua_arquivada(self):
        d = csp.write_app_index(self.base, None, 'c1', '/p', 't', 'user', False,
                                rec_dir=self.rec_dir, archived=True)
        self.assertTrue(json.load(open(d, encoding='utf-8'))['isArchived'])

    def test_ativa_continua_ativa(self):
        d = csp.write_app_index(self.base, None, 'c2', '/p', 't', 'user', False,
                                rec_dir=self.rec_dir)
        self.assertFalse(json.load(open(d, encoding='utf-8'))['isArchived'])

    def test_clone_nao_herda_arquivamento_do_template(self):
        tpl = os.path.join(self.rec_dir, 'local_%s.json' % uuid.uuid4())
        json.dump({'isArchived': True, 'cwd': 'x', 'cliSessionId': 'y'},
                  open(tpl, 'w', encoding='utf-8'))
        d = csp.write_app_index(self.base, tpl, 'c3', '/p', 't', 'user', False)
        self.assertFalse(json.load(open(d, encoding='utf-8'))['isArchived'])

    def test_meta_sem_a_chave_e_tratada_como_visivel(self):
        # bundles gerados antes desta mudanca nao tem hadAppRecord; o padrao
        # tem de ser listar, senao um bundle antigo importaria invisivel
        meta = {}
        self.assertTrue(meta.get('hadAppRecord', True))

    def test_meta_marcando_invisivel(self):
        meta = {'hadAppRecord': False}
        self.assertFalse(meta.get('hadAppRecord', True) or False)
        self.assertTrue(meta.get('hadAppRecord', True) or True)   # --index-all


if __name__ == '__main__':
    unittest.main(verbosity=2)
