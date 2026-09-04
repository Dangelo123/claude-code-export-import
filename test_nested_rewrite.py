#!/usr/bin/env python3
"""
The path rewrite has to reach the session's sidecar.

What slipped through: `_deep_rewrite_files` was handed the before/after diff of
the project directory -- which lists FILES and DIRECTORIES -- and discarded
anything not ending in .jsonl. The sidecar directory fell through that sieve,
and with it every subagent and workflow transcript. On a real migration: 2774
out of 2959 nested transcripts kept the source machine's paths.

It never broke resuming (the cwd a session resumes with lives in the top-level
file), which is why the whole existing suite stayed green.
"""
import json
import os
import shutil
import tempfile
import unittest

import batch

BS = chr(92)
PATH_MAP = {'D:' + BS + 'proj': '/home/me/proj'}


def line(path):
    return json.dumps({'type': 'user', 'cwd': path,
                       'message': {'content': 'look at ' + path}}) + '\n'


class NestedRewrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dest = os.path.join(self.tmp, '-home-me-proj')
        # sidecar with a subagent and a workflow, the way the app creates it
        os.makedirs(os.path.join(self.dest, 'sess1', 'subagents', 'workflows'))
        self.top = os.path.join(self.dest, 'sess1.jsonl')
        self.sub = os.path.join(self.dest, 'sess1', 'subagents',
                                'workflows', 'agent-a1.jsonl')
        self.deepest = os.path.join(self.dest, 'sess1', 'subagents',
                                    'workflows', 'journal.jsonl')
        for p in (self.top, self.sub, self.deepest):
            with open(p, 'w', encoding='utf-8') as fh:
                # RAW path: json.dumps is what escapes it, exactly like a real
                # transcript. Passing it pre-escaped writes D:\\\\proj into the
                # file, which no rule matches -- that is what broke this test.
                fh.write(line('D:' + BS + 'proj' + BS + 'src'))
        # a binary in the sidecar: it must not take the rewrite down
        self.bin = os.path.join(self.dest, 'sess1', 'attachment.pdf')
        with open(self.bin, 'wb') as fh:
            fh.write(bytes([0, 1, 2, 255, 254]))
        self.rw = batch.build_rewriter(PATH_MAP, True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, names=None):
        batch._deep_rewrite_files(self.dest,
                                  names if names is not None
                                  else sorted(os.listdir(self.dest)),
                                  self.rw)

    def _has_old(self, p):
        return 'D:' in open(p, encoding='utf-8', errors='ignore').read()

    def test_top_level_rewritten(self):
        self._run()
        self.assertFalse(self._has_old(self.top))

    def test_subagent_rewritten(self):
        self._run()
        self.assertFalse(self._has_old(self.sub), 'sidecar kept the old path')

    def test_deepest_file_rewritten(self):
        self._run()
        self.assertFalse(self._has_old(self.deepest))

    def test_new_path_present(self):
        self._run()
        self.assertIn('/home/me/proj', open(self.sub, encoding='utf-8').read())

    def test_binary_untouched(self):
        self._run()
        self.assertEqual(open(self.bin, 'rb').read(), bytes([0, 1, 2, 255, 254]))

    def test_only_touches_what_came_in_the_diff(self):
        # a session that was already at the destination must not be touched
        other = os.path.join(self.dest, 'preexisting.jsonl')
        with open(other, 'w', encoding='utf-8') as fh:
            fh.write(line('D:' + BS + 'proj' + BS + 'old'))
        before = open(other, encoding='utf-8').read()
        self._run(['sess1.jsonl', 'sess1'])       # diff without the preexisting one
        self.assertEqual(open(other, encoding='utf-8').read(), before)

    def test_idempotent(self):
        self._run()
        after_first = open(self.sub, encoding='utf-8').read()
        self._run()
        self.assertEqual(open(self.sub, encoding='utf-8').read(), after_first)


if __name__ == '__main__':
    unittest.main(verbosity=2)
