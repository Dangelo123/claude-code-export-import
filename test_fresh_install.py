"""
A freshly installed destination has the account folder (created by signing in)
and ZERO local_*.json. Before, the importer gave up on piece 2 and the session
existed on disk without appearing in the interface -- exactly what happened on
the CachyOS run. These cases pin the new behaviour down.
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
        self.account = os.path.join(self.base, str(uuid.uuid4()))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finds_the_account_with_no_records_at_all(self):
        os.makedirs(self.account)
        base, rec_dir = csp.find_account_dir(self.base)
        self.assertEqual(base, self.base)
        self.assertTrue(rec_dir.startswith(self.account))

    def test_reuses_an_existing_group(self):
        group = os.path.join(self.account, str(uuid.uuid4()))
        os.makedirs(group)
        _, rec_dir = csp.find_account_dir(self.base)
        self.assertEqual(rec_dir, group)

    def test_without_an_account_it_invents_nothing(self):
        os.makedirs(self.base)
        base, rec_dir = csp.find_account_dir(self.base)
        self.assertIsNone(rec_dir)

    def test_writes_a_usable_record(self):
        os.makedirs(self.account)
        _, rec_dir = csp.find_account_dir(self.base)
        dest = csp.write_app_index(self.base, None, 'cli-123', '/home/f/proj',
                                   'A title', 'user', False, rec_dir=rec_dir)
        o = json.load(open(dest, encoding='utf-8'))
        self.assertEqual(o['cliSessionId'], 'cli-123')
        self.assertEqual(o['cwd'], '/home/f/proj')
        self.assertEqual(o['originCwd'], '/home/f/proj')
        self.assertEqual(o['title'], 'A title')
        self.assertFalse(o['isArchived'])
        self.assertEqual(o['sessionId'], os.path.basename(dest)[:-5])
        for k in ('createdAt', 'lastFocusedAt', 'lastActivityAt'):
            self.assertGreater(o[k], 0)

    def test_synthetic_record_is_findable_by_cli_id(self):
        os.makedirs(self.account)
        _, rec_dir = csp.find_account_dir(self.base)
        csp.write_app_index(self.base, None, 'cli-abc', '/home/f/p', 't', 'user',
                            False, rec_dir=rec_dir)
        o, f = csp.find_record_by_cli('cli-abc', self.base)
        self.assertIsNotNone(o)

    def test_cloning_still_takes_priority(self):
        group = os.path.join(self.account, str(uuid.uuid4()))
        os.makedirs(group)
        tpl = os.path.join(group, 'local_%s.json' % uuid.uuid4())
        json.dump({'cliSessionId': 'x', 'cwd': 'c', 'permissionMode': 'plan',
                   'enabledMcpTools': {'my:tool': True}, 'worktreeName': 'wt'},
                  open(tpl, 'w', encoding='utf-8'))
        base, rd, template = csp.find_record_dir(self.base)
        self.assertEqual(template, tpl)
        dest = csp.write_app_index(base, template, 'new', '/d', 't', 'user', False)
        o = json.load(open(dest, encoding='utf-8'))
        self.assertEqual(o['permissionMode'], 'plan')      # inherited
        self.assertIn('my:tool', o['enabledMcpTools'])     # inherited
        self.assertNotIn('worktreeName', o)                # dropped


if __name__ == '__main__':
    unittest.main(verbosity=2)
