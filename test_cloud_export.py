#!/usr/bin/env python3
"""
export-cloud, and cloud sessions in export-all.

A cloud session has no local transcript, so the tool pulls it down with
`claude --teleport` and exports the copy that writes. What was learned against
the real CLI, and is pinned down here:

1. In print mode (`claude -p /exit --teleport <id>`) the teleport needs no
   terminal, checks no repository and switches no branch: the transcript is
   written and /exit, which print mode does not run, costs no model turn. So it
   runs in an empty scratch folder, the session is given the project folder the
   user chose, and nothing of the user's is touched.

2. Run from inside a Claude Code session, the teleport inherits that session's
   CLAUDE_CODE_CHILD_SESSION marker and saves no transcript at all.
   teleport_env() keeps those markers away from it.

3. A session that ran on a computer serving Remote Control (not in Anthropic's
   cloud) teleports with no conversation. That has to fail loudly, not export
   an empty session.

The end-to-end cases drive a fake `claude` that behaves like the real one.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

import batch
import claude_session_port as csp


def lines(*objs):
    return [json.dumps(o, ensure_ascii=False) + '\n' for o in objs]


MARKER = {'type': 'user', 'isMeta': True, 'cwd': '@CWD@',
          'message': {'role': 'user', 'content': csp.TELEPORT_MARKER + '. The updated working directory is @CWD@'}}
HISTORY = [
    {'type': 'permission-mode', 'permissionMode': 'auto'},
    {'type': 'user', 'uuid': 'u1', 'cwd': '@CWD@',
     'message': {'role': 'user', 'content': 'Read README.md and summarize it.'}},
    {'type': 'assistant', 'uuid': 'a1', 'parentUuid': 'u1', 'cwd': '@CWD@',
     'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'It ports Claude Code sessions.'}]}},
    {'type': 'system', 'subtype': 'stop_hook_summary', 'cwd': '@CWD@'},
]
EXIT_TAIL = [
    {'type': 'system', 'subtype': 'informational', 'content': 'Session resumed'},
    {'type': 'user', 'message': {'role': 'user', 'content': '<local-command-caveat>Caveat: ...</local-command-caveat>'}},
    {'type': 'user', 'message': {'role': 'user', 'content': '<command-name>/exit</command-name>'}},
    {'type': 'user', 'message': {'role': 'user', 'content': '<local-command-stdout>Goodbye!</local-command-stdout>'}},
    {'type': 'last-prompt', 'lastPrompt': 'Read README.md', 'leafUuid': 'x'},
    {'type': 'permission-mode', 'permissionMode': 'auto'},
]
TELEPORTED = HISTORY + [MARKER] + EXIT_TAIL


class CloudId(unittest.TestCase):
    def test_bare_id(self):
        self.assertEqual(csp.parse_cloud_id('session_017Adu28UhKapnrkbVdLrFyG'), 'session_017Adu28UhKapnrkbVdLrFyG')

    def test_url_with_query(self):
        url = 'https://claude.ai/code/session_017Adu28UhKapnrkbVdLrFyG?from=cli&m=0'
        self.assertEqual(csp.parse_cloud_id(url), 'session_017Adu28UhKapnrkbVdLrFyG')

    def test_cse_prefix(self):
        self.assertEqual(csp.parse_cloud_id('claude.ai/code/cse_0Ab9'), 'cse_0Ab9')

    def test_garbage(self):
        self.assertIsNone(csp.parse_cloud_id('not a session'))
        self.assertIsNone(csp.parse_cloud_id(None))


class CloudSpecs(unittest.TestCase):
    def test_lines_folders_comments_duplicates(self):
        text = ("# from the old account\n"
                "https://claude.ai/code/session_01A?from=cli\n"
                "\n"
                "session_01B | /work/other\n"
                "session_01A\n")
        self.assertEqual(batch.parse_cloud_specs([text], '/work/default'),
                         [('session_01A', os.path.abspath('/work/default')),
                          ('session_01B', os.path.abspath('/work/other'))])

    def test_needs_a_folder(self):
        with self.assertRaises(SystemExit) as cm:
            batch.parse_cloud_specs(['session_01A'], None)
        self.assertIn('--cloud-folder', str(cm.exception))

    def test_rejects_what_is_not_a_link(self):
        with self.assertRaises(SystemExit) as cm:
            batch.parse_cloud_specs(['https://example.com/x'], '/w')
        self.assertIn('not a cloud session link', str(cm.exception))


class Environment(unittest.TestCase):
    def test_untouched_outside_claude_code(self):
        env = {'PATH': '/bin', 'CLAUDE_CODE_OAUTH_TOKEN': 'mine', 'ANTHROPIC_BASE_URL': 'https://gw'}
        out, cleaned = csp.teleport_env(env)
        self.assertFalse(cleaned)
        self.assertEqual(out, env)

    def test_session_markers_removed_inside_claude_code(self):
        env = {'PATH': '/bin', 'HOME': '/h', 'CLAUDE_CONFIG_DIR': '/h/.claude-x',
               'CLAUDECODE': '1', 'CLAUDE_CODE_CHILD_SESSION': '1', 'CLAUDE_CODE_SESSION_ID': 's',
               'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop', 'CLAUDE_PID': '42',
               'ANTHROPIC_BASE_URL': 'http://127.0.0.1:1234'}
        out, cleaned = csp.teleport_env(env)
        self.assertTrue(cleaned)
        self.assertEqual(out, {'PATH': '/bin', 'HOME': '/h', 'CLAUDE_CONFIG_DIR': '/h/.claude-x'})
        self.assertIn('CLAUDE_CODE_CHILD_SESSION', env, 'the caller environment must not be modified')


class Split(unittest.TestCase):
    def test_typical_teleport(self):
        self.assertEqual(csp.teleport_split(lines(*TELEPORTED)), (len(HISTORY), 2, True))

    def test_local_work_after_teleport_is_kept(self):
        more = {'type': 'user', 'message': {'role': 'user', 'content': 'now fix the tests'}}
        idx, history, tail_is_noise = csp.teleport_split(lines(*HISTORY, MARKER, EXIT_TAIL[0], more))
        self.assertEqual((idx, history), (len(HISTORY), 2))
        self.assertFalse(tail_is_noise)

    def test_nothing_came_down(self):
        # what a session that ran on a computer serving Remote Control teleports as
        self.assertEqual(csp.teleport_split(lines({'type': 'permission-mode'}, MARKER, *EXIT_TAIL))[1], 0)

    def test_not_a_teleport(self):
        self.assertEqual(csp.teleport_split(lines(*HISTORY)), (None, 0, False))

    def test_path_swap_is_json_aware(self):
        old, new = r'C:\Temp\cse-teleport-x', r'D:\Work\Project'
        ln = json.dumps({'cwd': old, 'message': {'content': old + r'\README.md'}}) + '\n'
        o = json.loads(csp._swap_path(ln, [old], new))
        self.assertEqual((o['cwd'], o['message']['content']), (new, new + r'\README.md'))


class FindTranscript(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write(self, name, objs):
        p = os.path.join(self.d, name)
        with open(p, 'w', encoding='utf-8') as fh:
            fh.writelines(lines(*objs))
        return p

    def test_new_file_with_marker(self):
        old = self.write('old.jsonl', HISTORY + [MARKER])
        self.write('unrelated.jsonl', HISTORY)
        new = self.write('new.jsonl', TELEPORTED)
        self.assertEqual(csp.find_teleported_transcript([self.d], {old}), new)

    def test_none_when_nothing_new(self):
        old = self.write('old.jsonl', HISTORY + [MARKER])
        self.assertIsNone(csp.find_teleported_transcript([self.d], {old}))


class FindCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.which = csp.shutil.which
        csp.shutil.which = lambda name: None          # as on a desktop-only machine

    def tearDown(self):
        csp.shutil.which = self.which
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_newest_cli_shipped_with_the_app(self):
        app = os.path.join(self.tmp, 'Claude')
        store = os.path.join(app, 'claude-code-sessions')
        os.makedirs(store)
        exe = 'claude.exe' if os.name == 'nt' else 'claude'
        for v in ('2.1.9', '2.1.10'):
            os.makedirs(os.path.join(app, 'claude-code', v))
            open(os.path.join(app, 'claude-code', v, exe), 'w').close()
        self.assertEqual(csp.find_claude_cli(store), os.path.join(app, 'claude-code', '2.1.10', exe))

    def test_nothing_anywhere(self):
        self.assertIsNone(csp.find_claude_cli(os.path.join(self.tmp, 'Claude', 'claude-code-sessions')))


FAKE_CLAUDE = r'''#!{python}
# stands in for the Claude Code CLI: `auth status`, and `--teleport <id>` either
# interactive (switches the checkout to the session's branch) or in print mode
# (`-p /exit --teleport <id>`, which touches no repository), as the real one does
import json, os, re, subprocess, sys, uuid
projects = os.environ['FAKE_PROJECTS']
if sys.argv[1:3] == ['auth', 'status']:
    print(json.dumps({{'loggedIn': True, 'authMethod': 'claude.ai', 'projectsDirectory': projects}}))
    sys.exit(0)
assert '--teleport' in sys.argv, sys.argv
cwd = os.getcwd()
with open(os.environ['FAKE_CALLS'], 'a') as fh:
    fh.write(' '.join(sys.argv[1:]) + ' @ ' + cwd + '\n')
assert 'CLAUDE_CODE_CHILD_SESSION' not in os.environ, 'session marker leaked into the teleport'
if os.environ.get('FAKE_FAIL'):
    print(os.environ['FAKE_FAIL'], file=sys.stderr)
    sys.exit(1)
if '-p' not in sys.argv:
    subprocess.check_call(['git', 'checkout', '-q', '-b', 'claude/cloud-work'])
folder = os.path.join(projects, re.sub(r'[^A-Za-z0-9]', '-', cwd))
os.makedirs(os.path.join(folder, 'memory'), exist_ok=True)
with open(os.path.join(folder, str(uuid.uuid4()) + '.jsonl'), 'w', encoding='utf-8') as fh:
    fh.write(os.environ['FAKE_TRANSCRIPT'].replace('@CWD@', json.dumps(cwd)[1:-1]))
if '-p' in sys.argv:
    print("/exit isn't available in this environment.")
'''


@unittest.skipUnless(os.name == 'posix' and shutil.which('git'), 'needs a POSIX shebang and git')
class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.realpath(os.path.join(self.tmp, 'repo'))
        self.projects = os.path.join(self.tmp, 'projects')
        os.makedirs(self.repo)
        os.makedirs(self.projects)
        git = ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com']
        subprocess.check_call(git + ['init', '-q'], cwd=self.repo)
        subprocess.check_call(git + ['checkout', '-q', '-b', 'main'], cwd=self.repo)
        with open(os.path.join(self.repo, 'README.md'), 'w') as fh:
            fh.write('hello\n')
        subprocess.check_call(git + ['add', '.'], cwd=self.repo)
        subprocess.check_call(git + ['commit', '-q', '-m', 'init'], cwd=self.repo)
        self.claude = os.path.join(self.tmp, 'claude')
        with open(self.claude, 'w') as fh:
            fh.write(FAKE_CLAUDE.format(python=sys.executable))
        os.chmod(self.claude, 0o755)
        self.saved_env = dict(os.environ)
        os.environ['FAKE_PROJECTS'] = self.projects
        os.environ['FAKE_TRANSCRIPT'] = ''.join(lines(*TELEPORTED))
        os.environ['FAKE_CALLS'] = os.path.join(self.tmp, 'calls.txt')
        os.environ['CLAUDE_CODE_CHILD_SESSION'] = '1'   # as if run by an agent

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_export(self, **kw):
        if kw.get('transcript') is not None:
            os.environ['FAKE_TRANSCRIPT'] = ''.join(lines(*kw['transcript']))
        if kw.get('fail'):
            os.environ['FAKE_FAIL'] = kw['fail']
        out = os.path.join(self.tmp, 'bundle.zip')
        args = argparse.Namespace(session='https://claude.ai/code/session_01ABC?from=cli', cwd=self.repo,
                                  out=out, title=kw.get('title'), claude_bin=self.claude, claude_home=None,
                                  app_store=os.path.join(self.tmp, 'no-store'),
                                  keep_teleport_tail=kw.get('keep', False),
                                  interactive=kw.get('interactive', False), dry_run=False)
        csp.do_export_cloud(args)
        return out

    def calls(self):
        with open(os.environ['FAKE_CALLS']) as fh:
            return fh.read().splitlines()

    def bundle(self, out):
        with zipfile.ZipFile(out) as z:
            meta = json.loads(z.read('meta.json'))
            jsonl = [n for n in z.namelist() if n.endswith('.jsonl')][0]
            return meta, z.read(jsonl).decode('utf-8')

    def branch(self):
        return subprocess.check_output(['git', 'branch', '--show-current'], cwd=self.repo, text=True).strip()

    def test_bundle_holds_the_cloud_conversation(self):
        meta, body = self.bundle(self.run_export(title='README summary'))
        self.assertEqual(len(body.splitlines()), len(HISTORY))
        self.assertNotIn(csp.TELEPORT_MARKER, body)
        self.assertNotIn('/exit', body)
        cwds = {json.loads(ln).get('cwd') for ln in body.splitlines()} - {None}
        self.assertEqual(cwds, {self.repo}, 'the session belongs to the folder chosen, not the scratch one')
        self.assertNotIn('cse-teleport-', body)
        self.assertEqual((meta['cloudSessionId'], meta['cwd']), ('session_01ABC', self.repo))
        self.assertEqual((meta['title'], meta['titleSource']), ('README summary', 'user'))
        self.assertTrue(meta['hadAppRecord'], 'a cloud session was visible to the user, so it must show up after import')

    def test_runs_unattended_in_a_scratch_folder(self):
        self.run_export()
        (call,) = self.calls()
        self.assertTrue(call.startswith('-p /exit --teleport session_01ABC @ '), call)
        self.assertNotIn(self.repo, call, "the user's folder is not where the teleport runs")
        self.assertEqual(os.listdir(self.projects), [], "what the CLI kept about the scratch folder is gone")
        self.assertEqual(self.branch(), 'main')

    def test_unattended_does_not_care_about_local_changes(self):
        with open(os.path.join(self.repo, 'README.md'), 'a') as fh:
            fh.write('local edit\n')
        self.run_export()
        with open(os.path.join(self.repo, 'README.md')) as fh:
            self.assertIn('local edit', fh.read())

    def test_interactive_runs_in_the_checkout_and_restores_it(self):
        self.run_export(interactive=True)
        self.assertEqual(self.calls(), ['--teleport session_01ABC @ ' + self.repo])
        self.assertEqual(self.branch(), 'main', 'the checkout goes back to the branch it was on')

    def test_interactive_refuses_a_dirty_checkout(self):
        with open(os.path.join(self.repo, 'README.md'), 'a') as fh:
            fh.write('local edit\n')
        with self.assertRaises(SystemExit) as cm:
            self.run_export(interactive=True)
        self.assertIn('uncommitted changes', str(cm.exception))

    def test_keep_teleport_tail(self):
        _, body = self.bundle(self.run_export(keep=True))
        self.assertIn(csp.TELEPORT_MARKER, body)

    def test_empty_teleport_fails(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_export(transcript=[{'type': 'permission-mode'}, MARKER] + EXIT_TAIL)
        self.assertIn('no conversation', str(cm.exception))
        self.assertEqual(os.listdir(self.projects), [])

    def test_teleport_failure_shows_why(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_export(fail='Session not found: session_01ABC')
        self.assertIn('Session not found', str(cm.exception))

    def test_export_all_brings_listed_cloud_sessions(self):
        home = os.path.join(self.tmp, 'home')
        os.makedirs(os.path.join(home, 'projects'))
        out = os.path.join(self.tmp, 'migration')
        args = argparse.Namespace(out=out, claude_home=home, app_store=os.path.join(self.tmp, 'no-store'),
                                  with_config=False, dry_run=False,
                                  cloud=['https://claude.ai/code/session_01ABC?from=cli'], cloud_file=None,
                                  cloud_folder=self.repo, claude_bin=self.claude)
        saved = batch.export_extras, batch.export_app_profile
        batch.export_extras = batch.export_app_profile = lambda *a, **kw: None   # not this machine's real profile
        try:
            batch.do_export_all(args)
        finally:
            batch.export_extras, batch.export_app_profile = saved
        with open(os.path.join(out, 'manifest.json')) as fh:
            manifest = json.load(fh)
        with open(os.path.join(out, 'path-map.template.json')) as fh:
            template = json.load(fh)
        self.assertEqual(len(manifest), 1)
        self.assertEqual((manifest[0]['cloudSessionId'], manifest[0]['cwd']), ('session_01ABC', self.repo))
        self.assertTrue(os.path.isfile(os.path.join(out, manifest[0]['bundle'])))
        self.assertIn(self.repo, template)


class ImportAllFaithful(unittest.TestCase):
    """In faithful mode records come from the source's app profile, where a cloud
    session has none: it must get one made, or it never shows up."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = {n: getattr(batch, n) for n in ('import_app_profile', 'import_extras', 'import_config',
                                                     'ensure_retention', '_deep_rewrite_files')}
        for n in self.saved:
            setattr(batch, n, lambda *a, **kw: None)
        self.do_import = csp.do_import
        self.seen = {}
        csp.do_import = lambda ns: self.seen.__setitem__(os.path.basename(ns.src), ns.no_app_index)

    def tearDown(self):
        for n, f in self.saved.items():
            setattr(batch, n, f)
        csp.do_import = self.do_import
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cloud_entry_gets_a_record(self):
        src = os.path.join(self.tmp, 'bundle')
        os.makedirs(src)
        manifest = [{'sessionId': 'loc', 'cwd': '/old/a', 'bundle': 'loc.zip'},
                    {'sessionId': 'cld', 'cwd': '/old/a', 'bundle': 'cld.zip', 'cloudSessionId': 'session_01X'}]
        for name in ('loc.zip', 'cld.zip', batch.PROFILE_ZIP):
            open(os.path.join(src, name), 'w').close()
        with open(os.path.join(src, 'manifest.json'), 'w') as fh:
            json.dump(manifest, fh)
        pmap = os.path.join(self.tmp, 'map.json')
        with open(pmap, 'w') as fh:
            json.dump({'/old/a': '/new/a'}, fh)
        args = argparse.Namespace(src=src, path_map=pmap, claude_home=os.path.join(self.tmp, 'home'),
                                  app_store=None, keep_id=False, no_app_index=False, faithful=True,
                                  index_all=False, with_history=False, retention_days=999999, dry_run=False)
        batch.do_import_all(args)
        self.assertEqual(self.seen, {'loc.zip': True, 'cld.zip': False})


class GuiTabs(unittest.TestCase):
    """The tabs hand the core what was typed (needs a display)."""

    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception as e:                                   # noqa: BLE001
            raise unittest.SkipTest('no display (%s)' % e)
        import gui
        cls.gui = gui

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        gui = self.gui
        self.saved = (gui.core.do_export_cloud, gui.batch.do_export_all,
                      gui.filedialog.asksaveasfilename, gui.messagebox.showinfo)
        self.seen = {}
        gui.core.do_export_cloud = lambda a: self.seen.update(vars(a))
        gui.batch.do_export_all = lambda a: self.seen.update(vars(a))
        gui.filedialog.asksaveasfilename = lambda **kw: os.path.join(tempfile.gettempdir(), 'x.zip')
        gui.messagebox.showinfo = lambda *a, **kw: None
        self.app = gui.App(self.root)
        self.app._busy_run = lambda work, *a: work()          # run it right here, not in a thread

    def tearDown(self):
        gui = self.gui
        (gui.core.do_export_cloud, gui.batch.do_export_all,
         gui.filedialog.asksaveasfilename, gui.messagebox.showinfo) = self.saved

    def test_cloud_tab(self):
        self.app.cloud_id_var.set('https://claude.ai/code/session_01XYZ?from=cli')
        self.app.cloud_dir_var.set(tempfile.gettempdir())
        self.app.cloud_title_var.set('My task')
        self.app._do_export_cloud()
        self.assertEqual((self.seen['session'], self.seen['cwd'], self.seen['title']),
                         ('session_01XYZ', tempfile.gettempdir(), 'My task'))
        self.assertEqual(self.seen['out'], os.path.join(tempfile.gettempdir(), 'x.zip'))
        self.assertFalse(self.seen['interactive'], 'a window has no terminal to run the teleport in')

    def test_migrate_tab_cloud_list(self):
        self.app.mig_out_var.set(tempfile.gettempdir())
        self.app.mig_cloud_text.insert('1.0', 'session_01A\nsession_01B | /work/b')
        self.app.mig_cloud_dir_var.set('/work/default')
        self.app._do_export_all()
        self.assertEqual(self.seen['cloud'], ['session_01A\nsession_01B | /work/b'])
        self.assertEqual(self.seen['cloud_folder'], '/work/default')


if __name__ == '__main__':
    unittest.main()
