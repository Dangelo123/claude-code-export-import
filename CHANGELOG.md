# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.4.1] - 2026-08-30

### Fixed

- **Nested transcripts kept the source machine's paths.** The rewrite ran over
  the before/after diff of the destination project directory, which lists both
  files and directories, and discarded anything not ending in `.jsonl`. The
  session's sidecar is a directory, so every subagent and workflow transcript
  inside it was skipped. Measured on the migration this was found in: 2774 of
  2959 nested transcripts still referenced the Windows paths.

  Resuming a session was never affected -- the cwd it resumes with lives in the
  top-level transcript, which was rewritten -- which is why the whole existing
  suite stayed green. What was wrong was the history inside the log.

  Binary files that share the sidecar (PDFs, images from tool results) are now
  skipped explicitly instead of raising.

### Tests

`test_rewrite_aninhado.py` builds a real sidecar tree (subagents/workflows plus
a binary) and asserts the rewrite reaches the bottom of it, still never touches
a pre-existing session, and is idempotent. It fails against the old code.

## [1.4.0] - 2026-08-29

Pinned sessions actually arrive.

### Added

- **IndexedDB transport.** 1.3.0 carried the app's session records and Local
  Storage and claimed pinning survived. It did not: on the migrated machine 32
  sessions had `isStarred: true` on disk, `pinnedOrder` listed all 31 ids, the
  scope key and account matched -- and the sidebar's "Pinned" section was
  empty, with the app's own row counter reporting `code.pinned: 0`. The state
  the UI reads lives in a third store, `IndexedDB/https_claude.ai_0.
  indexeddb.leveldb`, which was never copied. Pinning a session by hand in the
  destination produced exactly one pinned row, which is what isolated it. With
  IndexedDB copied, the destination reproduces the source's pinned list in the
  same order, icons included.
- **`--with-config`** carries `~/.claude.json` (MCP servers, per-project
  permissions), `settings.json` and the desktop app's own JSON config. It is a
  flag rather than the default because these files hold real credentials --
  database passwords and API tokens in MCP env blocks -- and the bundle usually
  ends up on an external drive; the export names every file whose contents look
  like a secret. `buddy-tokens.json` and `ant-device-registry.json` are never
  copied: a session token and a device registration belong to the machine.

### Note on IndexedDB

Its values use Blink's structured-clone serialization, where strings are
length-prefixed, so a path cannot be swapped for one of a different length
without corrupting the record. It is copied byte for byte. The paths a session
actually opens with come from `local_*.json`, which is rewritten.

### What the app itself does with the copy

On first launch the app prunes a record it cannot parse (the zero-filled one
described in 1.3.0), so the destination settles at 129 records where the source
had 130. That is the app's own housekeeping, not a transport loss.

## [1.3.0] - 2026-08-29

Makes the destination's sidebar a reproduction of the source's, pinned
sessions included.

### Added

- **`--faithful`.** Until now the importer rebuilt the app's session records
  from scratch, which necessarily produced new ids. Pinning does not survive
  that: `pinnedOrder`, in the app's Local Storage, holds each record's own
  `local_<uuid>` id, so every regenerated id points at nothing. Faithful mode
  copies the original records instead and keeps session ids (`--keep-id`), so
  those references stay valid. Measured on a real install: 31 pinned sessions,
  30 of which land intact (the 31st is unreadable at the source -- see below).
- **App profile transport (`_app-profile.zip`).** `export-all` also bundles the
  `local_*.json` records verbatim and the Local Storage LevelDB.
- **`localstorage_paths.py`.** Local Storage is not just ids and preferences:
  it holds a `cc-session-cwd-local_<id>` key per session plus JSON blobs with
  escaped paths (the sidebar's folder grouping). Copied as-is from Windows to
  Linux, the app asks you to trust a `D:` path that does not exist there. This
  rewrites them -- 53 of 391 keys on the real install. It needs `plyvel`,
  because the values sit in snappy-compressed blocks and scanning bytes cannot
  reach them; without it the Local Storage is still copied (pinning works) and
  the tool prints how to finish the job.
- The destination's Local Storage is copied to `leveldb.antes-do-import` before
  being replaced, and the directory is emptied first so the result is exactly
  the source's file set -- a mix leaves orphaned `.ldb` files and a `CURRENT`
  pointing at the other set's `MANIFEST`.
- Records that are unreadable at the source are copied byte for byte and
  reported, instead of being skipped in silence. The real install had one: 533
  bytes of zeros, the signature of an unclean shutdown, belonging to a pinned
  session that does not open on Windows either.

### Requires

The app must be **closed on the destination**: Local Storage is a LevelDB read
only at startup. Closing it on the source too is advisable, so the copied
LevelDB is a consistent snapshot.

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
