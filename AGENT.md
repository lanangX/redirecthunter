# AGENT.md

Orientation for any coding agent working in this repo. Read this first;
everything else is reached from here by pointer, not repeated here.

## What this project is

RedirectHunter: an async HTTP redirect-discovery and validation framework
(Typer CLI, Pydantic models, SQLite persistence). Not an exploitation tool —
it classifies and reports redirect behavior on URLs the operator already
has. See `README.md` for the full pitch and CLI walkthrough.

## Documentation map

Each doc below owns one concern. If you're about to explain something one
of these already covers, edit that doc instead of restating it here or in
chat.

| Doc | Owns | Regenerate from |
|---|---|---|
| `docs/ARCHITECTURE.md` | Module map, data flow, plugin pipeline, why boundaries are drawn where they are | The code itself — update this doc when you change a module boundary, not just when you remember to |
| `docs/DATABASE_SCHEMA.md` | SQLite table definitions | `database.py`'s `_SCHEMA_SQL` |
| `docs/CLI_REFERENCE.md` | Every CLI flag, per command | `--help` output of the real CLI — never hand-write a flag here without running it |
| `MEMORY.md` | Current project state: what's in flight, recent decisions and why, open threads across sessions | This conversation / prior sessions — update it whenever you finish or hand off work |
| `CHANGELOG.md` | Released and unreleased version history | Merged changes, Keep-a-Changelog format |

Start a non-trivial task by reading `MEMORY.md` — it says what's currently
in flight so you don't re-derive context that already exists.

## Orientation, not lookup

Things you can always get faster from the environment than from this file
— don't ask, run them:

- Dependencies, Python version, entry point: `pyproject.toml`
- Run tests: `pytest` (config already set in `pyproject.toml`: coverage on,
  asyncio auto mode)
- Lint / type-check: `ruff check .`, `mypy redirecthunter`
- Any command's flags: `redirecthunter <command> --help` (and keep
  `docs/CLI_REFERENCE.md` in sync if you change one)

## Conventions worth knowing (not written anywhere else)

- `utils.py` is deliberately dependency-free and sits at the bottom of the
  import graph — nothing in it may import another `redirecthunter` module.
  New cross-cutting helpers (URL transforms, token substitution, etc.)
  belong there, paired with their inverse if one exists (see
  `expand_target()` / `redact_domain()`).
- CLI commands in `cli.py` are flat verbs (`scan`, `stats`, `export`, ...),
  not nested subcommands, and use long `--option` flags only — with two
  documented exceptions: `redact-target` / `expand-target` carry
  single-letter aliases for compatibility with the shell-script workflow
  they replaced, and the `bl-check` / `bl-stats` / `bl-show` / `bl-export`
  family carries several (`-d`, `-c`, `-t`, `-u`, `-l`, `-f`, `-o`) purely
  for typing ergonomics on a command family expected to run often — not
  legacy muscle memory. Neither is an inconsistency to "fix"; see
  `MEMORY.md` for the full reasoning behind each.
- `examples/` files are verified against the real loader/config code (see
  `examples/README.md`) — never illustrative-only. Don't let an example
  drift from what the code actually accepts.
- New format enums (`InputFormat`, `ExportFormat`, `RedactFormat`, ...)
  live in `models.py` next to each other, even when only one module reads
  a given enum — that's where every other format contract already lives.

## Working conventions for this repo specifically

- This repo uses `/grill-me` (interview to sharpen a plan), `/to-spec`
  (synthesize the conversation into a spec), and `/to-tickets` (break a
  spec into blocked, vertical-slice tickets) as its planning workflow. No
  issue tracker is configured yet, so specs/tickets are written to
  `.scratch/<feature-slug>/` — check there for in-flight design work
  before starting something that might already have a spec.
- No CI/lint pre-commit hook is configured; run `pytest` and `ruff check .`
  yourself before calling work done.
