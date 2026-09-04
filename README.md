# claude-code-export-import

[![tests](https://github.com/Dangelo123/claude-code-export-import/actions/workflows/tests.yml/badge.svg)](https://github.com/Dangelo123/claude-code-export-import/actions/workflows/tests.yml)
[![release](https://img.shields.io/github/v/release/Dangelo123/claude-code-export-import?sort=semver)](https://github.com/Dangelo123/claude-code-export-import/releases/latest)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![no dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](#)

> Move a Claude Code session from one machine/account to another — and have it show up in the sidebar like it was always there.
>
> Or move **the whole install**, across operating systems, with your memories.

Claude Code has no built-in way to export or import a conversation between
installs ([feature request #18645](https://github.com/anthropics/claude-code/issues/18645),
[#51337](https://github.com/anthropics/claude-code/issues/51337) are still open).
Copying the transcript file by hand doesn't work, because the desktop app is
**index-driven** and indexes sessions by absolute path and by a separate record store.

This tool reverse-engineers the local storage and ports a session **seamlessly**:
it lands in the right folder, keeps its title, and is resumable — across a
**different machine, a different account (email), and even a different app build**
(Win32 install ↔ Microsoft Store/MSIX package).

> ⚠️ Unofficial. It manipulates undocumented local files and may break when
> Anthropic changes the format. Not affiliated with Anthropic. **Back up `~/.claude` first.**

---

## The key insight: a session is *two* pieces

| Piece | Where | What it is |
|------|-------|-----------|
| **1. Transcript** | `~/.claude/projects/<ENC(cwd)>/<sessionId>.jsonl` | the actual conversation (one JSON object per line) + a sibling `<sessionId>/` cache folder |
| **2. App record** | `<app-store>/<accountUuid>/<group>/local_*.json` | the desktop app's index entry: maps `cliSessionId → jsonl`, holds the **displayed title**, cwd, model, timestamps |

Drop only piece 1 and the session is **invisible** in the app. You need both.
The folder name is `ENC(cwd) = re.sub(r'[^A-Za-z0-9]', '-', cwd)` — every
non-alphanumeric char in the absolute working directory becomes `-`.

Full write-up: **[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md)**.

---

## Install

**No Python? Grab the standalone Windows binary** from the
[latest release](https://github.com/Dangelo123/claude-code-export-import/releases/latest)
(`claude-code-export-import-win-x64.exe`) and use it like the commands below,
replacing `python claude_session_port.py` with the `.exe`.

**From source** (any OS, Python 3.8+, standard library only — no dependencies):

```bash
git clone https://github.com/Dangelo123/claude-code-export-import
cd claude-code-export-import
```

---

## Usage

### Easiest: the GUI (no commands)

Download the GUI build from the
[latest release](https://github.com/Dangelo123/claude-code-export-import/releases/latest)
(`claude-code-export-import-gui-win-x64.exe`) and double-click it.

- **Export tab** — pick a session from the list (by title) and save a `.zip`.
- **Import tab** — choose a `.zip` you received and click **Import**.
- **Migrate everything tab** — move a *whole install* to another machine.
  Step 1 bundles every session plus your memories on the old machine; step 2
  restores them on the new one. It reads the path map from the bundle and shows
  one row per project so you can say where each lives now — **Suggest
  destinations** prefills them from your home folder, and **Preview** shows every
  `old → new` decision without writing anything.

Then quit and reopen Claude. That's the whole flow. Python users can launch the
same GUI with `python gui.py`.

**macOS:** download `claude-code-export-import-gui-macos-universal.zip` (one
build runs on both Apple Silicon and Intel), unzip, and open the app. Because it is **unsigned**,
macOS Gatekeeper will block the first launch — **right-click the app → Open →
Open**, or run `xattr -cr <app>` once. Or just run from source: `python3 gui.py`.

### CLI

#### 1) Export on the source machine

```bash
python claude_session_port.py export --src ~/.claude/projects/<folder>/<sessionId>.jsonl
```

Produces `claude-session-<id>.zip` containing the transcript, the sidecar
cache, and a `meta.json` (title + cwd) so the title travels with it.

> Tip: find a session by its sidebar title inside the `*.jsonl` / app records,
> or just sort the `~/.claude/projects/**/*.jsonl` files by modified time.

#### 2) Import on the target machine

```bash
python claude_session_port.py import --src ./claude-session-<id>.zip
# if the project lives at a different path on the target:
python claude_session_port.py import --src ./claude-session-<id>.zip --target-cwd "D:\Work\Project"
```

It mints a fresh session id (collision-safe), rewrites `cwd` everywhere,
auto-detects the target app store + the logged-in account folder, and writes
**both pieces**. Then **Quit + reopen** the app — the session appears in Recents.

Use `--dry-run` on either command to preview without writing.

---

## Migrating a whole install (`batch.py`)

The commands above move **one** session. Moving an entire install is a different
problem: many source roots map to many destination roots, and if the two
machines run different operating systems the *path separators* change too.

```bash
# on the source machine — every session, your memories, CLAUDE.md and the
# app profile (records + sidebar state). Add --with-config for MCP servers
# and per-project permissions; it warns which files carry credentials.
python batch.py export-all --out ./migration --with-config

# fill in the destinations, then on the target machine, with the app CLOSED:
python batch.py import-all --src ./migration --path-map ./my-path-map.json --faithful
```

`--faithful` is what makes the destination's sidebar match the source's,
pinned sessions and all. Without it the importer mints new session ids, and
every pin points at nothing.

`export-all` writes a `path-map.template.json` next to the bundles, pre-filled
with every source path it found. You supply the destinations:

```json
{
  "D:\\Work\\Project": "/home/you/work/project",
  "C:\\Users\\You\\Documents\\Notes": "/home/you/notes"
}
```

Matching is **longest-prefix**, so worktrees and sub-projects resolve from their
parent root — you only map the roots. Use `--dry-run` to see every
`old → new` decision before anything is written.

### Windows → Linux

Swapping the prefix is not enough. `D:\proj\src\Foo.cs` would become
`/home/you/proj\src\Foo.cs` — a hybrid that is broken on both systems. And you
cannot simply replace every backslash in the file, because that would mangle
regexes, escape sequences and code quoted inside the conversation.

The rewriter matches *prefix + path tail* and flips separators only within that
match. It runs in two flavours, because the same path is spelled differently
depending on where it lives:

| File | How the path is stored | Rewriter |
|------|------------------------|----------|
| `*.jsonl`, `*.json` | JSON-escaped — `D:\\proj` | `build_rewriter` |
| `*.md`, `*.txt` | literal — `D:\proj` | `build_plain_rewriter` |

Paths **outside** your map are left alone. That is deliberate: the tool will not
invent a destination it was not given. If your notes reference tool paths like
`C:\Users\You\.azure`, add them to the map — think "every Windows path that must
become a Linux path", not just "where the projects live".

### More than transcripts

`export-all` also carries what lives *beside* the sessions and is never
referenced by a `.jsonl`:

- per-project `memory/` and `plans/` folders
- the home-level `CLAUDE.md`

They ride in `_extras.zip` and are rewritten with the plain-text rewriter. An
existing file on the target is **never** overwritten.

### Retention: read this before importing

Claude Code prunes transcripts older than `cleanupPeriodDays` (default: **30**)
at startup. Restoring an archive without raising it first means the app deletes
most of your history the moment you open it.

`import-all` writes the setting **before** any transcript lands, and never
lowers a value that is already higher. On the corpus this was built against,
that guard protected 2442 of 3154 transcripts.

### Tests

```bash
python test_batch.py         # path rewriters, both grammars
python test_extras.py        # memories/CLAUDE.md transport
python test_no_clobber.py    # importing never touches existing sessions
python check_corpus.py   # dry-run over YOUR real transcripts (read-only)

python -m unittest test_faithful_mode test_localstorage_paths \
                   test_title_visibility test_fresh_install
```

The `unittest` modules cover what a real cross-OS migration broke: a fresh
install with no record to clone, titles falling back to the folder name,
sessions the source never listed showing up at the destination, the paths
buried in Local Storage, and the profile transport that carries pinning.

`check_corpus.py` is the one worth running before you trust a migration: it
replays the rewriter over every local transcript and asserts that every line
that parsed as JSON before still parses after, and that no mapped Windows path
survived.

---

## What survives the trip

- ✅ Same account, same machine
- ✅ Different account (different email)
- ✅ Different machine
- ✅ Different app build (Win32 ↔ MSIX/Store) — the store path is auto-detected
- ✅ Large sessions (tested with a 67 MB / 1154-turn transcript)
- ✅ The session **title** (carried in the bundle, set as `titleSource=user` so the app won't regenerate it)
- ✅ The model/effort are **inherited from the target account's own template**, so it never asks for a model the target can't use
- ✅ **A different operating system** — Windows → Linux, with paths and separators rewritten (`batch.py`)
- ✅ **Your memories and `CLAUDE.md`** (`batch.py export-all`)
- ✅ **Pinned sessions**, in the same order (`import-all --faithful`) — see below
- ✅ **MCP servers and per-project permissions** (`export-all --with-config`)

### The sidebar lives in three places

Making the destination *look* like the source took longer than moving the
conversations, because that state is split:

| Where | What it holds |
|---|---|
| `claude-code-sessions/…/local_*.json` | one record per session: title, cwd, archived, `isStarred` |
| `Local Storage/leveldb` | order, width, grouping, `pinnedOrder`, and a `cc-session-cwd-*` **path** per session |
| `IndexedDB/https_claude.ai_*.leveldb` | the session index the UI actually reads |

Restoring only the first two is not enough, and it fails quietly: on a real
migration 32 sessions had `isStarred: true` on disk and `pinnedOrder` listed
every id, while the sidebar showed no pinned section at all. `--faithful`
copies all three and keeps the session ids, which is what makes the references
line up.

Local Storage holds filesystem paths, so those get rewritten (this needs
`plyvel`; without it the copy still happens and the tool prints the one command
left to run). IndexedDB is copied byte for byte — its values use Blink's
structured-clone format, where strings carry their length, so swapping a path
for a longer one would corrupt the record. The paths a session actually opens
with come from `local_*.json`, which *is* rewritten.

## What does *not* travel

- ❌ The cloud-side mirror (`bridgeSessionIds` are zeroed → the imported session is local-only)
- ❌ **Auth** — the token is machine-bound; sign in on the target
- ❌ A session token or device registration (`buddy-tokens.json`, `ant-device-registry.json`) — those belong to the machine
- ❌ MCP OAuth tokens, and paths inside `.claude.json` that point at Windows binaries
- ❌ Paths you did not put in the `--path-map` — by design, the tool invents nothing
- ❌ The project files themselves — a transcript is not an environment

---

## Platform support (app store auto-detection)

| OS / build | `<app-store>` |
|---|---|
| Windows (Win32 install) | `%APPDATA%\Claude\claude-code-sessions` |
| Windows (MSIX / Store) | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code-sessions` |
| macOS | `~/Library/Application Support/Claude/claude-code-sessions` |
| Linux | `~/.config/Claude/claude-code-sessions` |

> Piece 2 needs the target to be **signed in** — that alone creates the account
> folder, and the app only reads records under the signed-in account's UUID, so
> it is the one thing the tool will not invent. Running a session there first is
> not required: with no record to clone, the importer builds one from scratch.

---

## Options (import)

| Flag | Effect |
|---|---|
| `--target-cwd PATH` | project path on the target (default: the cwd from the bundle) |
| `--keep-id` | keep the original sessionId (default: mint a new one) |
| `--title "..."` | force the sidebar title |
| `--no-app-index` | write only the transcript (piece 1), skip the app record |
| `--faithful` | *(`import-all`)* reproduce the source sidebar: keep ids, restore the original records and both browser stores, pinned sessions included. Close the app on the target first |
| `--index-all` | *(`import-all`)* also list sessions the source itself never showed — by default the destination mirrors the source's visibility |
| `--no-sidecar` | don't copy the sidecar cache |
| `--keep-paths` | don't rewrite the old cwd inside the content |
| `--claude-home DIR` / `--app-store DIR` | override the auto-detected locations |
| `--with-history` | also add the prompts to `history.jsonl` |
| `--dry-run` | preview, write nothing |

---

## Disclaimer

This is a community reverse-engineering effort for personal portability/backup.
It is **not** affiliated with, authorized, or supported by Anthropic. Formats are
undocumented and can change without notice. Use at your own risk and keep backups.

## License

[MIT](LICENSE)
