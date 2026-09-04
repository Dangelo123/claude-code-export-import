#!/usr/bin/env python3
"""
Faithful mode: the destination's sidebar has to end up identical to the
source's, pinned sessions included.

What makes that possible: pinnedOrder (in Local Storage) holds the
'local_<uuid>' id of each RECORD, not the session's id. Synthesising new records
mints new ids and pinning points at nothing. By copying the original records and
importing with --keep-id, the ids stay valid.
"""
import json
import os
import shutil
import tempfile
import unittest
import zipfile

import batch
import claude_session_port as csp


class Profile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.source = os.path.join(self.tmp, 'source', 'claude-code-sessions')
        self.rec_dir = os.path.join(self.source, 'account-1', 'group-1')
        os.makedirs(self.rec_dir)
        self.ls_source = os.path.join(self.tmp, 'source', 'Local Storage', 'leveldb')
        os.makedirs(self.ls_source)
        with open(os.path.join(self.ls_source, '000123.ldb'), 'wb') as fh:
            fh.write(b'\x00\x01{"pinnedOrder":["local_aaa","local_bbb"]}\x00')
        with open(os.path.join(self.ls_source, 'LOCK'), 'wb') as fh:
            fh.write(b'')

        for name, cwd in (('local_aaa', r'D:\proj'), ('local_bbb', r'D:\proj\sub')):
            json.dump({'sessionId': name, 'cliSessionId': name[6:],
                       'cwd': cwd, 'originCwd': cwd, 'title': 't-' + name,
                       'isArchived': False},
                      open(os.path.join(self.rec_dir, name + '.json'), 'w',
                           encoding='utf-8'))

        # IndexedDB is the store the interface reads to know what is pinned;
        # without it pinning does not show up no matter how many records carry
        # isStarred
        self.idb_source = os.path.join(self.tmp, 'source', 'IndexedDB',
                                       'https_claude.ai_0.indexeddb.leveldb')
        os.makedirs(self.idb_source)
        with open(os.path.join(self.idb_source, '000001.ldb'), 'wb') as fh:
            fh.write(b'\x00starred\x00local_aaa\x00')

        self.out = os.path.join(self.tmp, 'bundle')
        os.makedirs(self.out)
        self.dest = os.path.join(self.tmp, 'dest', 'claude-code-sessions')
        os.makedirs(os.path.join(self.dest, 'account-1'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _export(self):
        return batch.export_app_profile(self.out, app_store=self.source)

    def _remap(self, p):
        if p.startswith(r'D:\proj'):
            return '/home/f/proj' + p[len(r'D:\proj'):].replace('\\', '/')
        return None

    def test_exports_records_and_local_storage(self):
        n = self._export()
        self.assertEqual(n, 2)
        z = zipfile.ZipFile(os.path.join(self.out, batch.PROFILE_ZIP))
        names = z.namelist()
        self.assertIn('records/account-1/group-1/local_aaa.json', names)
        self.assertIn('local-storage/000123.ldb', names)
        info = json.loads(z.read('profile.json'))
        self.assertEqual(info['records'], 2)
        self.assertEqual(info['accounts'], ['account-1'])

    def test_lock_does_not_enter_the_package(self):
        # on Windows the app keeps LOCK open with exclusive access; zipping it
        # failed on the read AFTER the entry header had already been written,
        # leaving an empty entry in the package that nobody could read
        self._export()
        z = zipfile.ZipFile(os.path.join(self.out, batch.PROFILE_ZIP))
        self.assertFalse([n for n in z.namelist() if n.endswith('/LOCK')])
        empty = [n for n in z.namelist()
                 if not n.endswith('/') and z.getinfo(n).file_size == 0]
        self.assertEqual(empty, [])

    def test_count_matches_the_package(self):
        self._export()
        z = zipfile.ZipFile(os.path.join(self.out, batch.PROFILE_ZIP))
        info = json.loads(z.read('profile.json'))
        for prefix, field in (('local-storage/', 'localStorage'),
                              ('indexeddb/', 'indexedDB'),
                              ('records/', 'records')):
            actual = len([n for n in z.namelist() if n.startswith(prefix)])
            self.assertEqual(info[field], actual, prefix)

    def test_local_storage_goes_byte_for_byte(self):
        self._export()
        z = zipfile.ZipFile(os.path.join(self.out, batch.PROFILE_ZIP))
        self.assertEqual(z.read('local-storage/000123.ldb'),
                         open(os.path.join(self.ls_source, '000123.ldb'), 'rb').read())

    def test_import_preserves_the_record_id(self):
        self._export()
        batch.import_app_profile(self.out, self._remap, app_store=self.dest)
        dest = os.path.join(self.dest, 'account-1', 'group-1', 'local_aaa.json')
        self.assertTrue(os.path.isfile(dest))
        o = json.load(open(dest, encoding='utf-8'))
        self.assertEqual(o['sessionId'], 'local_aaa')      # pinnedOrder still valid
        self.assertEqual(o['cliSessionId'], 'aaa')

    def test_import_rewrites_the_cwd(self):
        self._export()
        batch.import_app_profile(self.out, self._remap, app_store=self.dest)
        o = json.load(open(os.path.join(self.dest, 'account-1', 'group-1',
                                        'local_bbb.json'), encoding='utf-8'))
        self.assertEqual(o['cwd'], '/home/f/proj/sub')
        self.assertEqual(o['originCwd'], '/home/f/proj/sub')

    def test_import_copies_local_storage_to_the_destination(self):
        self._export()
        batch.import_app_profile(self.out, self._remap, app_store=self.dest)
        dest = os.path.join(os.path.dirname(self.dest), 'Local Storage',
                            'leveldb', '000123.ldb')
        self.assertTrue(os.path.isfile(dest))
        self.assertIn(b'pinnedOrder', open(dest, 'rb').read())

    def test_cwd_outside_the_map_is_left_alone(self):
        json.dump({'sessionId': 'local_ccc', 'cliSessionId': 'ccc',
                   'cwd': r'E:\other', 'originCwd': r'E:\other', 'title': 'x'},
                  open(os.path.join(self.rec_dir, 'local_ccc.json'), 'w',
                       encoding='utf-8'))
        self._export()
        batch.import_app_profile(self.out, self._remap, app_store=self.dest)
        o = json.load(open(os.path.join(self.dest, 'account-1', 'group-1',
                                        'local_ccc.json'), encoding='utf-8'))
        self.assertEqual(o['cwd'], r'E:\other')

    def test_dry_run_writes_nothing(self):
        self._export()
        batch.import_app_profile(self.out, self._remap, app_store=self.dest, dry=True)
        self.assertFalse(os.path.exists(os.path.join(self.dest, 'account-1',
                                                     'group-1', 'local_aaa.json')))

    def test_no_profile_in_the_bundle_does_not_break(self):
        n = batch.import_app_profile(self.out, self._remap, app_store=self.dest)
        self.assertEqual(n, (0, 0))

    def test_exports_the_indexeddb(self):
        self._export()
        z = zipfile.ZipFile(os.path.join(self.out, batch.PROFILE_ZIP))
        self.assertIn('indexeddb/https_claude.ai_0.indexeddb.leveldb/000001.ldb',
                      z.namelist())
        self.assertEqual(json.loads(z.read('profile.json'))['indexedDB'], 1)

    def test_import_restores_the_indexeddb(self):
        self._export()
        batch.import_app_profile(self.out, self._remap, app_store=self.dest)
        dest = os.path.join(os.path.dirname(self.dest), 'IndexedDB',
                            'https_claude.ai_0.indexeddb.leveldb', '000001.ldb')
        self.assertTrue(os.path.isfile(dest))
        self.assertIn(b'starred', open(dest, 'rb').read())

    def test_indexeddb_goes_byte_for_byte(self):
        # Blink's serialisation prefixes each string with its length; rewriting
        # one path into another of a different length would corrupt the record
        self._export()
        batch.import_app_profile(self.out, self._remap, app_store=self.dest)
        dest = os.path.join(os.path.dirname(self.dest), 'IndexedDB',
                            'https_claude.ai_0.indexeddb.leveldb', '000001.ldb')
        self.assertEqual(open(dest, 'rb').read(),
                         open(os.path.join(self.idb_source, '000001.ldb'), 'rb').read())

    def test_destination_with_its_own_indexeddb_is_saved_first(self):
        target = os.path.join(os.path.dirname(self.dest), 'IndexedDB',
                              'https_claude.ai_0.indexeddb.leveldb')
        os.makedirs(target)
        with open(os.path.join(target, 'old.ldb'), 'wb') as fh:
            fh.write(b'from the destination')
        self._export()
        batch.import_app_profile(self.out, self._remap, app_store=self.dest)
        self.assertTrue(os.path.isfile(os.path.join(target + '.before-import',
                                                    'old.ldb')))
        # and the folder was left holding only the source's files
        self.assertEqual(sorted(os.listdir(target)), ['000001.ldb'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
