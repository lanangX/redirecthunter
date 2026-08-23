# 04 — Retire `url-target-replace.sh`, document the new commands, bump version

**What to build:** With both new commands shipped, remove the old shell script, point existing documentation at the replacement, document the commands as official CLI features, and release the change as a versioned, non-breaking addition.

**Blocked by:** 02, 03 — both commands must exist and be tested before the old script is removed and docs claim them as official.

**Status:** done (shipped in 1.1.0)

- [x] `examples/url-target-replace.sh` deleted.
- [x] `examples/README.md` updated: the section referencing the old script is replaced with a short migration note pointing at `redirecthunter redact-target` and `redirecthunter expand-target`, each with one example invocation.
- [x] `docs/CLI_REFERENCE.md` gets full entries for `redact-target` and `expand-target` (flags, defaults, examples), matching the format of the existing command entries.
- [x] Top-level `README.md` mentions the two new commands as a feature.
- [x] `pyproject.toml` version bumped from `1.0.0` to `1.1.0`.
- [x] No remaining references to `url-target-replace.sh` in shipped docs, comments, or code. (References inside `.scratch/target-replace-cli/` itself are intentionally kept — this directory is the historical spec/ticket record for the migration, not shipped documentation.)
