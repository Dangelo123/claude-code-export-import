"""
Um destino recem-instalado tem a pasta da conta (criada pelo login) e ZERO
local_*.json. Antes, o importador desistia da peca 2 e a sessao existia em
disco sem aparecer na interface -- foi exatamente o que aconteceu no teste
em CachyOS. Estes casos travam o comportamento novo.
"""
import json
import os
import shutil
import tempfile
import unittest
import uuid

import claude_session_port as csp


class FreshInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = os.path.join(self.tmp, 'claude-code-sessions')
        self.conta = os.path.join(self.base, str(uuid.uuid4()))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_acha_conta_sem_nenhum_registro(self):
        os.makedirs(self.conta)
        base, rec_dir = csp.find_account_dir(self.base)
        self.assertEqual(base, self.base)
        self.assertTrue(rec_dir.startswith(self.conta))

    def test_reaproveita_grupo_existente(self):
        grupo = os.path.join(self.conta, str(uuid.uuid4()))
        os.makedirs(grupo)
        _, rec_dir = csp.find_account_dir(self.base)
        self.assertEqual(rec_dir, grupo)

    def test_sem_conta_nao_inventa(self):
        os.makedirs(self.base)
        base, rec_dir = csp.find_account_dir(self.base)
        self.assertIsNone(rec_dir)

    def test_escreve_registro_utilizavel(self):
        os.makedirs(self.conta)
        _, rec_dir = csp.find_account_dir(self.base)
        dest = csp.write_app_index(self.base, None, 'cli-123', '/home/f/proj',
                                   'Um titulo', 'user', False, rec_dir=rec_dir)
        o = json.load(open(dest, encoding='utf-8'))
        self.assertEqual(o['cliSessionId'], 'cli-123')
        self.assertEqual(o['cwd'], '/home/f/proj')
        self.assertEqual(o['originCwd'], '/home/f/proj')
        self.assertEqual(o['title'], 'Um titulo')
        self.assertFalse(o['isArchived'])
        self.assertEqual(o['sessionId'], os.path.basename(dest)[:-5])
        for k in ('createdAt', 'lastFocusedAt', 'lastActivityAt'):
            self.assertGreater(o[k], 0)

    def test_registro_sintetico_e_achavel_pelo_cli_id(self):
        os.makedirs(self.conta)
        _, rec_dir = csp.find_account_dir(self.base)
        csp.write_app_index(self.base, None, 'cli-abc', '/home/f/p', 't', 'user',
                            False, rec_dir=rec_dir)
        o, f = csp.find_record_by_cli('cli-abc', self.base)
        self.assertIsNotNone(o)

    def test_clone_ainda_tem_prioridade(self):
        grupo = os.path.join(self.conta, str(uuid.uuid4()))
        os.makedirs(grupo)
        tpl = os.path.join(grupo, 'local_%s.json' % uuid.uuid4())
        json.dump({'cliSessionId': 'x', 'cwd': 'c', 'permissionMode': 'plan',
                   'enabledMcpTools': {'meu:tool': True}, 'worktreeName': 'wt'},
                  open(tpl, 'w', encoding='utf-8'))
        base, rd, template = csp.find_record_dir(self.base)
        self.assertEqual(template, tpl)
        dest = csp.write_app_index(base, template, 'novo', '/d', 't', 'user', False)
        o = json.load(open(dest, encoding='utf-8'))
        self.assertEqual(o['permissionMode'], 'plan')      # herdado
        self.assertIn('meu:tool', o['enabledMcpTools'])    # herdado
        self.assertNotIn('worktreeName', o)                # descartado


if __name__ == '__main__':
    unittest.main(verbosity=2)
