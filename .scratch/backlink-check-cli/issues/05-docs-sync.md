# 05 — Documentation sync

**What to build:** Bring the project's documentation map (per `AGENT.md`)
up to date with the new command family and tables:

- `docs/CLI_REFERENCE.md`: full entries for `bl-check`, `bl-stats`,
  `bl-show`, `bl-export` (flags, defaults, runnable examples), generated
  from real `--help` output as the doc's own convention requires.
- `AGENT.md`'s "Conventions worth knowing" section: update the
  `redact-target`/`expand-target` single-letter-alias exception note to
  record `bl-check`/`bl-stats`/`bl-show`/`bl-export` as the *second*
  documented exception to the long-flags-only rule, with a one-line
  reason (typing ergonomics for a frequently-run command, not legacy
  muscle memory) so a future agent doesn't treat it as inconsistency to
  "fix."
- `docs/DATABASE_SCHEMA.md`: entries for `backlink_checks` and
  `backlink_results`, regenerated from `database.py`'s `_SCHEMA_SQL`.
- `CHANGELOG.md`: an unreleased entry describing the new command family.
- `MEMORY.md`: a decision note explaining this feature exists *because*
  of the guardrail already written under "Why `backlink_checker.py` /
  `backlink_checker_js.py` live at the repo root..." — update that note to
  point at this feature instead of leaving it describing a gap that no
  longer exists.
- `README.md`: a short mention of the new commands alongside the existing
  feature list.

**Blocked by:** 02, 03, 04 — needs every command's real behavior/flags to
document accurately.

**Status:** done

- [x] `docs/CLI_REFERENCE.md` has accurate entries for all four new commands (`bl-check`/`bl-stats`/`bl-show`/`bl-export`), verified against real `--help` output.
- [x] `docs/DATABASE_SCHEMA.md` documents `backlink_checks`/`backlink_results`.
- [x] `AGENT.md`'s conventions section documents the `bl-*` family as the second exception to long-flags-only.
- [x] `CHANGELOG.md` has a new unreleased entry.
- [x] `MEMORY.md`'s existing note about `backlink_checker.py` living outside the package is updated to reflect that the "worth its own spec" feature now exists, with a pointer to `.scratch/backlink-check-cli/`.
- [x] `README.md` mentions the new commands.
