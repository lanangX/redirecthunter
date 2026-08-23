# 05 — Docs sync and example fixtures

**What to build:**

- `docs/CLI_REFERENCE.md`: add the `bl-chain`/`bl-chain-stats`/
  `bl-chain-show`/`bl-chain-export` family; add a short note on the
  `|target` row syntax to `bl-check`'s own existing reference entry.
- `docs/DATABASE_SCHEMA.md`: document `backlink_chains` /
  `backlink_chain_tiers`, the new `matched_target` column on
  `backlink_results` (with a migration note matching the
  `robots_meta`/`robots_header` precedent), and the cascade-delete
  boundary (chain deletion never touches underlying `backlink_checks`/
  `backlink_results`).
- `CHANGELOG.md`: unreleased entry for the new command family and the
  `|target` row-override syntax.
- `MEMORY.md`: current-state note on this feature, including the explicit
  `--require-confirmed-parent` default decision and why tier order is
  positional-args-only, not filename-inferred (so a future session doesn't
  re-litigate either).
- `AGENT.md`: extend (not duplicate) the existing short-flag-exception
  bullet to mention `bl-chain*` is the same documented exception as the
  rest of the `bl-` family.
- New `examples/` fixtures: `backlink-tier1.txt`, `backlink-tier2.txt`,
  `backlink-tier3.txt` — small, real, loader-valid files (per `AGENT.md`'s
  "examples/ are verified, never illustrative-only" convention)
  demonstrating both a plain row and a `|target`-override row, replacing
  the ambiguous `backlink1.txt`/`backlink2.txt` naming from the
  originating conversation.
- `examples/README.md`: describe the new fixtures and a sample `bl-chain`
  invocation using them.

**Blocked by:** 03, 04 (docs describe finished behavior, not a moving target).

**Status:** done

- [x] `docs/CLI_REFERENCE.md` updated and verified against real `--help` output.
- [x] `docs/DATABASE_SCHEMA.md` updated, including the migration note for `matched_target`.
- [x] `CHANGELOG.md` unreleased entry added.
- [x] `MEMORY.md` updated with the two explicit decisions above.
- [x] `AGENT.md`'s short-flag-exception bullet extended.
- [x] `examples/backlink-tier1.txt` / `-tier2.txt` / `-tier3.txt` added and verified against the real loader.
- [x] `examples/README.md` updated.
