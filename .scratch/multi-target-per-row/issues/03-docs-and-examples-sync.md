# 03 — Docs and examples sync

**What to build:**

- `docs/CLI_REFERENCE.md`: extend the `bl-check` section's `|target`
  row-syntax note with the `;`-separated multi-target form and one
  example line.
- `examples/backlink.txt` (or a new dedicated example file per
  `AGENT.md`'s "examples are verified against the real loader, never
  illustrative-only" rule): add one real, loader-valid row using the
  `|target1;target2` syntax.
- `CHANGELOG.md`: unreleased entry describing the multi-target
  per-row override as additive to the existing `|target` syntax.
- `MEMORY.md`: note this ticket sequence's completion and the decision
  trail (semicolon inside the existing `|` field, not a new top-level
  delimiter).

**Blocked by:** Tickets 01, 02 (docs should describe shipped behavior,
not planned behavior).

**Status:** done

- [x] `docs/CLI_REFERENCE.md` updated with the `;`-list row syntax and
      example.
- [x] `examples/backlink.txt` (or new file) has a real multi-target row,
      verified against the actual loader.
- [x] `CHANGELOG.md` unreleased entry added.
- [x] `MEMORY.md` updated.
