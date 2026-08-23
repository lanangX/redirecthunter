# 02 — Ship `redirecthunter redact-target` end-to-end

**What to build:** A new `redirecthunter redact-target INPUT_FILE` CLI command an operator can run today in place of the old `url-target-replace.sh` script: it reads a plain-text file of one URL per line, replaces occurrences of a given domain with a token (via `redact_domain()` from ticket 01), and writes the result to stdout or a file in txt, csv, json, or sqlite format — with a verbose mode reporting unmatched lines.

**Blocked by:** 01 — needs `redact_domain()` to exist.

**Status:** done (shipped in 1.1.0)

- [ ] `RedactFormat` enum added to `redirecthunter/models.py` (`TXT`, `CSV`, `JSON`, `SQLITE`), alongside `InputFormat`/`ExportFormat`.
- [ ] New module `redirecthunter/target_replace.py` with: a plain-text line reader, and writers for each `RedactFormat` value (txt: one URL per line; csv: `target_url,original_url` header + rows; json: array of `{"target_url": ..., "original_url": ...}`; sqlite: `urls` table with `target_url`/`original_url` columns) — implemented without depending on `ScanConfig`/`CandidateURL`/`load_candidates`.
- [ ] New `redact-target` command registered in `redirecthunter/cli.py`:
  - [ ] Positional `input_file` argument (plain text, one URL per line).
  - [ ] `-o/--output` (path; default stdout — sqlite format requires an explicit path since it's binary).
  - [ ] `-f/--format` (RedactFormat; inferred from `-o`'s extension when omitted, else defaults to txt).
  - [ ] `-d/--domain` (required).
  - [ ] `-t/--token` (default `{TARGET}`).
  - [ ] `-v/--verbose` (reports each unmatched line and the total unmatched count to stderr).
  - [ ] Both short (`-d`/`-f`/`-t`/`-v`/`-o`) and long (`--domain`/`--format`/`--token`/`--verbose`/`--output`) flag forms work.
- [ ] Lines with no domain match are written through unchanged by default (no data loss).
- [ ] `redirecthunter redact-target --help` shows usage and at least one example per output format, matching the style of `redirecthunter scan --help`.
- [ ] `tests/test_target_replace.py` extended with `CliRunner`-based tests (style of `tests/test_cli.py`) covering: each output format's structural correctness, verbose reporting output, unmatched-line passthrough, and stdout-default behavior.
