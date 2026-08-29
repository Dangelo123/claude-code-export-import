# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] — 2026-08-28

Fixes what a real Windows → CachyOS migration exposed: every session landed on
disk, yet the destination's sidebar looked wrong. Three separate causes, none of
them lost data.

### Added

- **App records on a fresh install.** The importer could only build the record
  the sidebar reads by *cloning an existing one* — and a newly installed app has
  none, which is precisely the migration case. It warned and moved on, leaving
  sessions on disk but invisible. It now synthesises the record inside the
  signed-in account folder. That folder is the one thing it will not invent:
  the app only reads records under the UUID of the account that is signed in,
  so signing in remains a prerequisite (and is enough — no session needed).
- **`--index-all`** to list sessions the source itself never showed.

### Fixed

- **Titles fell back to the folder name.** Only `ai-title` was read, so the 132
  sessions the user had renamed (of 184) showed up as `ClaudeNode` or
  `ClaudeCowork_MeepGreenfield`, repeated down the sidebar. The chain is now
  `custom-title` → `ai-title` → source record → first user message → folder name.
- **Sessions that were invisible at the source became visible at the
  destination.** Creating a record for *every* transcript surfaced 108 sessions
  the app had never listed — mostly abandoned resume branches — which read as
  duplicates. `meta.json` now carries `hadAppRecord` and `isArchived`, and the
  importer mirrors them.
- **Records counted and read twice on Windows.** With the Store build,
  `%APPDATA%\Claude` is a junction into the package's `LocalCache`, so both
  candidate paths are the same folder. Bases are now deduplicated by real path.

### Note on deduplication

Sessions sharing a title are *not* duplicates. Measured on a 184-session corpus:
71 look redundant, but none is a prefix or subset of its sibling — together they
hold 1,773 messages no other transcript has. They are branches from the same
compaction point where work continued in more than one. Nothing is deleted;
visibility alone is mirrored.

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
