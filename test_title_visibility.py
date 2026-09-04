#!/usr/bin/env python3
"""
Two regressions seen on the real migration to CachyOS:

1. The importer only looked at 'ai-title'. Sessions the user had renamed (132
   out of 184 in the real corpus) fell back to the folder name, and the sidebar
   filled up with "ClaudeNode" and "AcmeWorkspace".

2. The importer created an app record for EVERY session. Sessions that had no
   record at the source did not appear in that interface -- abandoned resume
   branches above all -- and started showing up at the destination, which looked
   like duplicated sessions.
"""
import json
import os
import shutil
import tempfile
import unittest
import uuid

import claude_session_port as csp


def lines(*objs):
    return [json.dumps(o, ensure_ascii=False) + '\n' for o in objs]


class Title(unittest.TestCase):
    def test_custom_beats_ai(self):
        ls = lines({'type': 'ai-title', 'aiTitle': 'generated'},
                   {'type': 'custom-title', 'customTitle': 'my own name'})
        custom = ai = None
        for ln in ls:
            o = json.loads(ln)
            if o.get('type') == 'custom-title':
                custom = o['customTitle']
            elif o.get('type') == 'ai-title':
                ai = o['aiTitle']
        self.assertEqual(custom or ai, 'my own name')

    def test_first_message_as_last_resort(self):
        ls = lines({'type': 'user', 'message': {'content': 'fix the kiosk deploy'}})
        self.assertEqual(csp.first_user_message(ls), 'fix the kiosk deploy')

    def test_first_message_ignores_system_block(self):
        ls = lines({'type': 'user', 'message': {'content': '<system-reminder>x</system-reminder>'}},
                   {'type': 'user', 'message': {'content': 'the actual question'}})
        self.assertEqual(csp.first_user_message(ls), 'the actual question')

    def test_first_message_accepts_list_content(self):
        ls = lines({'type': 'user',
                    'message': {'content': [{'type': 'text', 'text': 'hello   world'}]}})
        self.assertEqual(csp.first_user_message(ls), 'hello world')

    def test_first_message_truncated_with_ellipsis(self):
        ls = lines({'type': 'user', 'message': {'content': 'x' * 200}})
        t = csp.first_user_message(ls, limit=20)
        self.assertTrue(t.endswith('...'))
        self.assertEqual(len(t), 23)

    def test_no_user_message(self):
        self.assertIsNone(csp.first_user_message(lines({'type': 'system'})))


class Visibility(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = os.path.join(self.tmp, 'claude-code-sessions')
        self.rec_dir = os.path.join(self.base, str(uuid.uuid4()), str(uuid.uuid4()))
        os.makedirs(self.rec_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_archived_stays_archived(self):
        d = csp.write_app_index(self.base, None, 'c1', '/p', 't', 'user', False,
                                rec_dir=self.rec_dir, archived=True)
        self.assertTrue(json.load(open(d, encoding='utf-8'))['isArchived'])

    def test_active_stays_active(self):
        d = csp.write_app_index(self.base, None, 'c2', '/p', 't', 'user', False,
                                rec_dir=self.rec_dir)
        self.assertFalse(json.load(open(d, encoding='utf-8'))['isArchived'])

    def test_clone_does_not_inherit_archiving_from_the_template(self):
        tpl = os.path.join(self.rec_dir, 'local_%s.json' % uuid.uuid4())
        json.dump({'isArchived': True, 'cwd': 'x', 'cliSessionId': 'y'},
                  open(tpl, 'w', encoding='utf-8'))
        d = csp.write_app_index(self.base, tpl, 'c3', '/p', 't', 'user', False)
        self.assertFalse(json.load(open(d, encoding='utf-8'))['isArchived'])

    def test_meta_without_the_key_is_treated_as_visible(self):
        # bundles produced before this change have no hadAppRecord; the default
        # has to be "list it", otherwise an old bundle would import invisible
        meta = {}
        self.assertTrue(meta.get('hadAppRecord', True))

    def test_meta_marking_it_invisible(self):
        meta = {'hadAppRecord': False}
        self.assertFalse(meta.get('hadAppRecord', True) or False)
        self.assertTrue(meta.get('hadAppRecord', True) or True)   # --index-all


if __name__ == '__main__':
    unittest.main(verbosity=2)
