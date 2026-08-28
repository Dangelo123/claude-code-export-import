# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] — 2026-08-28

Moves the tool from transporting **one session** to migrating a **whole install**,
including across operating systems.

### Added

- **`batch.py`** — `export-all` and `import-all`. `export-all` walks
  `~/.claude/projects`, bundles every session and emits a `path-map.template.json`
  pre-filled with the source paths; `import-all` applies it by longest-prefix
  match, so worktrees resolve from their parent root.
- **Windows → Linux support.** Swapping a path prefix is not enough when the
  target uses different separators: `D:\proj\src\Foo.cs` would become
  `/home/you/proj\src\Foo.cs`. The rewriter matches prefix *plus path tail* and
  converts separators only inside that match, leaving backslashes that belong to
  regexes, escapes and quoted code untouched.
- **Two rewriter grammars.** A transcript stores paths JSON-escaped (`D:\\proj`);
  markdown stores them literally (`D:\proj`). Using one for the other silently
  matches nothing.
- **Extras transport.** Per-project `memory/` and `plans/` folders and the
  home-level `CLAUDE.md` now travel too — they sit beside the sessions, are never
  referenced by a `.jsonl`, and are what users mean when they ask whether the
  migrated assistant "still has its context".
- **Retention guard.** `import-all` writes `cleanupPeriodDays` *before* any
  transcript lands. Claude Code prunes sessions older than 30 days at startup, so
  restoring an archive without raising it first destroys most of the history.
- **GUI "Migrate everything" tab** with an inline path-map editor, destination
  suggestions that never overwrite what you typed, and a preview that shows every
  `old → new` decision before writing.
- **Test suite** (`test_batch.py`, `test_extras.py`, `test_no_clobber.py`,
  `test_real_corpus.py`, `test_gui_migrate.py`) and CI across Linux, Windows and
  macOS.

### Fixed

- The deep rewrite walked every `.jsonl` in the destination project folder,
  touching files the tool does not own. It now diffs the directory before and
  after each import and rewrites only what that import created.

### Verified

Migrated a real 174-session install from Windows to Linux: 395,679 lines
processed, zero JSON corrupted, zero Windows paths left behind, titles preserved
in the sidebar, and pre-existing sessions on the target untouched (identical
SHA-256).

## [1.0.0] — 2026-06-02

Initial release: port a single Claude Code session across machines, accounts and
app builds, writing both the transcript and the desktop app's index record.
