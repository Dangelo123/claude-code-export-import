# How it works — reverse-engineering Claude Code session storage

Everything here was found empirically by inspecting local files (Windows
desktop app, v2.1.x). Nothing is from Anthropic docs. Treat it as a snapshot
that can drift between versions.

## TL;DR

A Claude Code desktop session is **two artifacts**, and the app only shows
sessions that have both:

```
PIECE 1  transcript
  ~/.claude/projects/<ENC(cwd)>/<sessionId>.jsonl   (+ sibling <sessionId>/ cache)

PIECE 2  app index record
  <app-store>/<accountUuid>/<group>/local_<uuid>.json
```

Copying only piece 1 to a new machine fails (the session is invisible). That is
why naive "just copy the jsonl" workarounds stopped working — the app is
index-driven.

## Piece 1 — the transcript

- Path: `~/.claude/projects/<ENC(cwd)>/<sessionId>.jsonl`, one JSON object per line.
- **Folder encoding:** `ENC(cwd) = re.sub(r'[^A-Za-z0-9]', '-', cwd)`. Every
  non-alphanumeric character of the absolute working directory becomes `-`,
  with no collapsing of repeats. Examples:
  - `C:\Users\Me\Documents\GTD_Project` → `C--Users-Me-Documents-GTD-Project`
  - `/home/me/proj` → `-home-me-proj`
- **Per-line structural fields:** `cwd` (absolute path) and `sessionId`
  (which **must equal the filename**). Other fields (`version`, `gitBranch`,
  `uuid`, `parentUuid`, `requestId`) do not need editing to port.
- Line `types` seen: `user`, `assistant`, `system`, `attachment`,
  `queue-operation`, `ai-title`, `last-prompt`.
- There is an `{"type":"ai-title","aiTitle":...}` line, but **it is not the
  title the sidebar shows** — see piece 2.
- **Sidecar folder** `<sessionId>/` holds `tool-results/` (e.g. cached webfetch
  PDFs), `subagents/`, `workflows/`. It is cache, not referenced by path from
  the jsonl, and is safe to drop — but we copy it along for completeness.

To port piece 1: rewrite `cwd` and `sessionId` on every line, recompute the
folder via `ENC(new_cwd)`, write the file as `<new_sessionId>.jsonl`.

## Piece 2 — the desktop app index record

The Electron app keeps a per-session record, and **this** is what drives the
sidebar list and the displayed title.

- Path: `<app-store>/<accountUuid>/<group>/local_<uuid>.json`
  - `<accountUuid>` = `oauthAccount.accountUuid` from `~/.claude.json`
    (i.e. the folder is **bound to the logged-in email**).
  - `<group>` is a stable sub-id; all of an account's records live under it.
- Shape (key fields):

```json
{
  "sessionId": "local_<uuid>",        // the app's own id (UI-facing)
  "cliSessionId": "<jsonl sessionId>", // THE MAPPING to piece 1
  "cwd": "C:\\path", "originCwd": "C:\\path",
  "title": "My session",               // the displayed title
  "titleSource": "user" | "auto",      // "user" = a manual rename, locked
  "model": "claude-...", "effort": "high",
  "createdAt": 0, "lastFocusedAt": 0, "lastActivityAt": 0,  // ms; drive Recents order
  "isArchived": false,
  "bridgeSessionIds": [ ... ]          // server-side cloud mirror ids
}
```

- The **displayed title comes from `title`**, not from the jsonl's `ai-title`.
  `titleSource: "user"` means you renamed it (and the app won't regenerate it);
  `"auto"` means the app generated it.
- **No signature / hash / ownership field exists** → the app does not validate
  who created a record. That is why injecting a record into *another account's*
  folder works.
- The **pin** state is *not* here — it lives in the renderer's Local Storage
  (leveldb), so pinning does not travel via these files.

To create piece 2 on the target: clone an existing `local_*.json` (to inherit a
valid `model`/`effort` the target account actually has), then override
`sessionId`, `cliSessionId`, `cwd`, `title` (`titleSource=user`), timestamps,
and zero `bridgeSessionIds`. Write it into the same `<accountUuid>/<group>/`
folder found on the target.

## App-store location differs by build

This bit cost us a debugging round. The **Win32 install** stores under Roaming:

```
%APPDATA%\Claude\claude-code-sessions\...
```

The **Microsoft Store / MSIX package** virtualizes AppData *inside the package*:

```
%LOCALAPPDATA%\Packages\Claude_<suffix>\LocalCache\Roaming\Claude\claude-code-sessions\...
```

Note that `~/.claude/projects` (piece 1) is **not** virtualized — both builds
read it from the real user profile. Only the Electron app data (piece 2) moves
into the package container. The tool probes both locations.

| OS / build | app store base |
|---|---|
| Windows Win32 | `%APPDATA%\Claude\claude-code-sessions` |
| Windows MSIX/Store | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code-sessions` |
| macOS | `~/Library/Application Support/Claude/claude-code-sessions` |
| Linux | `~/.config/Claude/claude-code-sessions` |

## Cross-account / cross-machine — what actually carries

- **Account folder:** discovered by globbing the target's existing records, so
  it always lands under the account currently signed in on the target.
- **Model/effort:** inherited from the target's own template → never references
  a model the target account lacks.
- **bridgeSessionIds:** zeroed → imported session is local-only (no cloud
  mirror from the source account, which is the correct behaviour across accounts).
- **Pin:** not portable (renderer leveldb) — one click to re-pin.

## Things that are NOT part of a session

A transcript is not an environment. MCP servers, auth, settings, and the actual
project files do not live in these artifacts. The conversation history ports
perfectly; to keep *working*, the project must also exist at the target `cwd`.

## Validation performed

Same account ✅ · different account (email) ✅ · different machine ✅ ·
different build (Win32 → MSIX) ✅ · large session (67 MB / 1154 turns) ✅.
Record schema compared across builds: the MSIX record is a subset of the Win32
record (no MSIX-only fields), so the clone approach is build-agnostic.
