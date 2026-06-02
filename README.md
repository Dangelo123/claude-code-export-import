# claude-code-export-import

> Move a Claude Code session from one machine/account to another — and have it show up in the sidebar like it was always there.

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
[latest release](https://github.com/fernandomachado-arch/claude-code-export-import/releases/latest)
(`claude-code-export-import-win-x64.exe`) and use it like the commands below,
replacing `python claude_session_port.py` with the `.exe`.

**From source** (any OS, Python 3.8+, standard library only — no dependencies):

```bash
git clone https://github.com/fernandomachado-arch/claude-code-export-import
cd claude-code-export-import
```

---

## Usage

### 1) Export on the source machine

```bash
python claude_session_port.py export --src ~/.claude/projects/<folder>/<sessionId>.jsonl
```

Produces `claude-session-<id>.zip` containing the transcript, the sidecar
cache, and a `meta.json` (title + cwd) so the title travels with it.

> Tip: find a session by its sidebar title inside the `*.jsonl` / app records,
> or just sort the `~/.claude/projects/**/*.jsonl` files by modified time.

### 2) Import on the target machine

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

## What survives the trip

- ✅ Same account, same machine
- ✅ Different account (different email)
- ✅ Different machine
- ✅ Different app build (Win32 ↔ MSIX/Store) — the store path is auto-detected
- ✅ Large sessions (tested with a 67 MB / 1154-turn transcript)
- ✅ The session **title** (carried in the bundle, set as `titleSource=user` so the app won't regenerate it)
- ✅ The model/effort are **inherited from the target account's own template**, so it never asks for a model the target can't use

## What does *not* travel

- ❌ The **pin** state (lives in the app's Local Storage / leveldb) — re-pin with one click
- ❌ The cloud-side mirror (`bridgeSessionIds` are zeroed → the imported session is local-only)
- ❌ The project files, MCP servers, auth, settings — a transcript is not an environment

---

## Platform support (app store auto-detection)

| OS / build | `<app-store>` |
|---|---|
| Windows (Win32 install) | `%APPDATA%\Claude\claude-code-sessions` |
| Windows (MSIX / Store) | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code-sessions` |
| macOS | `~/Library/Application Support/Claude/claude-code-sessions` |
| Linux | `~/.config/Claude/claude-code-sessions` |

> Piece 2 requires the target app to have created at least one Code session
> already (so the account folder exists to clone a template from). Sign in and
> open one session once, then import.

---

## Options (import)

| Flag | Effect |
|---|---|
| `--target-cwd PATH` | project path on the target (default: the cwd from the bundle) |
| `--keep-id` | keep the original sessionId (default: mint a new one) |
| `--title "..."` | force the sidebar title |
| `--no-app-index` | write only the transcript (piece 1), skip the app record |
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
