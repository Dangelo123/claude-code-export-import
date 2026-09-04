#!/usr/bin/env python3
"""
The app's Local Storage holds file paths -- one `cc-session-cwd-local_<id>` key
per session, plus JSON blobs with escaped paths. Copied raw from a Windows
machine to a Linux one, the app asks you to trust `D:\\Project`, which does not
exist there. These cases pin the rewrite down.
"""
import unittest

import localstorage_paths as lp

BS = chr(92)
PATH_MAP = {
    'D:' + BS + 'ClaudeCowork_MeepGreenfield': '/home/f/ClaudeCowork_MeepGreenfield',
    'C:' + BS + 'Users' + BS + 'LocalAdmin' + BS + 'Documents' + BS + 'GTD_Project':
        '/home/f/GTD_Project',
    'C:' + BS + 'Users' + BS + 'LocalAdmin': '/home/f',
}


class Rewriter(unittest.TestCase):
    def setUp(self):
        self.r = lp.build_rewriter(PATH_MAP, True)

    def test_raw_path(self):
        self.assertEqual(self.r('D:' + BS + 'ClaudeCowork_MeepGreenfield'),
                         '/home/f/ClaudeCowork_MeepGreenfield')

    def test_tail_switches_separator(self):
        self.assertEqual(
            self.r('D:' + BS + 'ClaudeCowork_MeepGreenfield' + BS + 'src' + BS + 'A.cs'),
            '/home/f/ClaudeCowork_MeepGreenfield/src/A.cs')

    def test_path_escaped_in_json(self):
        given = '{"folder":"D:' + BS * 2 + 'ClaudeCowork_MeepGreenfield' + BS * 2 + 'sub"}'
        self.assertEqual(self.r(given),
                         '{"folder":"/home/f/ClaudeCowork_MeepGreenfield/sub"}')

    def test_forward_slash_matches_too(self):
        self.assertEqual(self.r('D:/ClaudeCowork_MeepGreenfield'),
                         '/home/f/ClaudeCowork_MeepGreenfield')

    def test_longest_prefix_wins(self):
        # C:\Users\LocalAdmin matches too, but GTD_Project is more specific
        target = 'C:' + BS + 'Users' + BS + 'LocalAdmin' + BS + 'Documents' + BS + 'GTD_Project'
        self.assertEqual(self.r(target), '/home/f/GTD_Project')

    def test_path_outside_the_map_is_left_alone(self):
        self.assertEqual(self.r('E:' + BS + 'other' + BS + 'thing'),
                         'E:' + BS + 'other' + BS + 'thing')

    def test_text_without_a_path(self):
        self.assertEqual(self.r('{"pinnedOrder":["local_a","local_b"]}'),
                         '{"pinnedOrder":["local_a","local_b"]}')

    def test_does_not_break_a_backslash_that_is_not_a_path(self):
        # the backslash here belongs to an escape sequence, not to a path
        given = '{"re":"' + BS * 2 + 'd+","p":"D:' + BS * 2 + 'ClaudeCowork_MeepGreenfield"}'
        out = self.r(given)
        self.assertIn(BS * 2 + 'd+', out)
        self.assertIn('/home/f/ClaudeCowork_MeepGreenfield', out)


class Encoding(unittest.TestCase):
    def test_latin1_round_trip(self):
        b = lp._encode('C:' + BS + 'x', lp.LATIN1)
        self.assertEqual(b[0], lp.LATIN1)
        self.assertEqual(lp._decode(b), ('C:' + BS + 'x', lp.LATIN1))

    def test_utf16_round_trip(self):
        b = lp._encode('session ' + chr(231), lp.UTF16)
        self.assertEqual(b[0], lp.UTF16)
        self.assertEqual(lp._decode(b), ('session ' + chr(231), lp.UTF16))

    def test_empty_value(self):
        self.assertEqual(lp._decode(b''), (None, None))

    def test_binary_value_is_ignored(self):
        text, _ = lp._decode(bytes([9, 200, 201, 202]))
        self.assertIsNone(text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
