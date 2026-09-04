# Contributing

## Running the tests

No install step — the project is standard library only.

```bash
python test_batch.py         # path rewriters, both grammars
python test_extras.py        # memory/ and CLAUDE.md transport
python test_no_clobber.py    # importing never touches existing sessions
python test_gui_migrate.py   # GUI tab (skips itself when headless)
```

And the one worth running before trusting a real migration:

```bash
python check_corpus.py   # replays the rewriter over YOUR transcripts, read-only
```

It asserts that every line that parsed as JSON before still parses after, and
that no mapped Windows path survived. Unit tests cover the cases you thought of;
a real transcript corpus contains regexes, shell output and JSON inside JSON —
which is where a regex-based rewriter actually breaks.

CI runs the suite on Linux, Windows and macOS. Path handling is the whole point
of this tool and it behaves differently per platform, so a change that passes on
one is not evidence it passes on the others.

## What a good change looks like

**Explain the failure, not the edit.** The commit message should say what breaks
without the change. "fix path handling" tells a future reader nothing; "swapping
the prefix alone yields `/home/you/proj\src\Foo.cs`, a hybrid broken on both
systems" tells them why the code looks the way it does.

**Add the test that would have caught it.** Two of the bugs in this project's
history — the deep rewrite touching files it did not own, and the JSON rewriter
silently matching nothing in markdown — were found by questions, not by tests.
Both now have one.

**Be conservative with other people's data.** This tool writes into a directory
full of someone's conversation history. Prefer additive operations; if you must
overwrite, warn first and make it opt-in.

## Reverse-engineering notes

The format is undocumented and can change without notice.
[`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md) records what was verified and
how. If you discover something new, add it there with the date and the app
version you observed it on — a claim without those is hard to trust six months
later.

## Scope

This is a migration tool, not a session manager. Things that belong here: moving
sessions and what sits beside them, across machines, accounts, builds and
operating systems. Things that do not: editing conversations, syncing
continuously, or anything requiring a server.
