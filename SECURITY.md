# Security

## What this tool touches

It reads and writes files that belong to the Claude desktop app:

- `~/.claude/projects/**` — conversation transcripts
- `~/.claude/settings.json` — only the `cleanupPeriodDays` key
- the app's session index (`<app-store>/<accountUuid>/<group>/local_*.json`)

Transcripts are conversations. Treat an exported bundle like you would treat the
conversation itself: it can contain anything that was pasted into the session,
including credentials, file contents and internal URLs. **A bundle is not
sanitised** — review before sharing one, and prefer private channels.

## What it does not do

- No network access. Nothing is uploaded, phoned home or telemetered.
- No credentials are read, written or copied. Auth tokens are machine-bound and
  stay behind; you sign in on the target.
- No dependencies. Standard library only, so the supply-chain surface is Python
  itself.

## Destructive-operation policy

Import is **additive**. It mints a fresh session id per import, so it cannot
collide with a session already on the target; `history.jsonl` is appended, never
rewritten. The only overwrite path is `--keep-id` with an id that already
exists, and that warns first. `test_no_clobber.py` asserts this: it seeds a
session on the target, imports over it, and fails if the SHA-256 changes.

## Reporting a vulnerability

Open a [security advisory](../../security/advisories/new) rather than a public
issue, and please include the OS, the app build (Win32 / MSIX / .deb), and what
the tool did versus what you expected.

Reports that matter most here: anything that makes the tool **destroy or corrupt
data it should not touch**, or leak transcript content outside the machine.

## Unofficial by nature

This project reverse-engineers undocumented local storage. It is not affiliated
with Anthropic, and a change to the app's format can break it at any time.
**Back up `~/.claude` before running it** — that is not boilerplate, it is the
actual mitigation for the main risk.
