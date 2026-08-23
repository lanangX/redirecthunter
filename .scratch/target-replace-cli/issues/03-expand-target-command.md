# 03 — Ship `redirecthunter expand-target` end-to-end (new reverse-direction feature)

**What to build:** A new `redirecthunter expand-target INPUT_FILE` CLI command that does the reverse of `redact-target`: it reads a plain-text file of `{TARGET}`-templated URLs and writes out real URLs with `{TARGET}` replaced by an operator-supplied value, via the existing `expand_target()` in `redirecthunter/utils.py`, without requiring a full `scan`.

**Blocked by:** 02 — reuses the plain-text reader/writer scaffolding introduced in `redirecthunter/target_replace.py`.

**Status:** done (shipped in 1.1.0)

- [ ] New `expand-target` command registered in `redirecthunter/cli.py`:
  - [ ] Positional `input_file` argument (plain text, one templated URL per line).
  - [ ] `-o/--output` (path; default stdout).
  - [ ] `--target` (required; the value substituted for `{TARGET}`, same name/semantics as `scan --target`).
  - [ ] `--encode` (flag; when set, percent-encodes `--target`'s value before substitution by passing `url_encode=True` to `expand_target()`).
  - [ ] `-v/--verbose` (reports each line with no `{TARGET}` token, and the total such count, to stderr).
  - [ ] Always plain-text in and plain-text out — no `-f/--format` flag on this command.
- [ ] Lines with no `{TARGET}` token are written through unchanged.
- [ ] `redirecthunter expand-target --help` shows usage and at least one example, including one with `--encode`.
- [ ] `tests/test_target_replace.py` extended with `CliRunner`-based tests covering: basic expansion, `--encode` producing a percent-encoded substitution, verbose reporting of untemplated lines, and passthrough of untemplated lines by default.
