# Spec: Migrate `url-target-replace.sh` into the RedirectHunter CLI, add reverse (`expand-target`) direction

## Problem Statement

RedirectHunter operators prepare candidate-URL templates (URLs with a domain
occurrence swapped for the literal `{TARGET}` token, e.g.
`https://redir.example/go?u={TARGET}`) before feeding them to
`redirecthunter scan --target ...`. Today that preparation step lives entirely
in `examples/url-target-replace.sh`: a 319-line Bash script that shells out to
an embedded Perl one-liner (for the domain-matching regex) and a Python
heredoc (for SQLite output). It is real, load-bearing tooling — not a
throwaway example, evidenced by `confirmed_backlinks.txt` at the repo root,
which is its output.

Two problems follow from that:

1. **Maintainability.** The logic lives outside the tested, typed Python
   package, duplicated in a second language (Perl) with its own boundary
   regex, and a third (Python-in-a-heredoc) for the SQLite path. It has zero
   automated test coverage. Every future bug fix or behavior change has to be
   made and reasoned about inside a large shell script instead of the
   package's existing module/test structure.
2. **Missing direction.** The script only goes one way: domain → `{TARGET}`.
   There is no way to go the other way — take a file of `{TARGET}` templates
   and expand them into real URLs against a chosen target — without invoking
   a full `redirecthunter scan`. The package already has the primitive for
   this (`redirecthunter.utils.expand_target`), but it isn't exposed as a
   standalone, scriptable step.

## Solution

Retire `examples/url-target-replace.sh` and replace it with two first-class,
tested, documented RedirectHunter CLI commands:

- `redirecthunter redact-target` — domain → `{TARGET}` (replaces the old
  script's only mode).
- `redirecthunter expand-target` — `{TARGET}` → a real target URL (new
  capability, built on the existing `expand_target()` primitive).

Both commands live in the Python package, share the project's existing
test/CLI conventions, and are documented in `docs/CLI_REFERENCE.md` as
official commands rather than an example script.

## User Stories

1. As a RedirectHunter operator, I want to run `redirecthunter redact-target` on a raw file of scraped backlink URLs, so that I get back a file where every occurrence of my target domain is replaced with `{TARGET}`, ready for `scan --target`.
2. As an operator, I want `redact-target` to recognize my domain regardless of scheme (`http`/`https`), `www.` prefix, or percent-encoding variants, so that messy scraped data still gets normalized correctly.
3. As an operator, I want `redact-target` to leave a domain match untouched if it's actually a substring of a different domain (e.g. not matching `tmedilana.id` when searching for `medilana.id`), so that I don't corrupt unrelated URLs.
4. As an operator, I want to choose the output format for `redact-target` (txt, csv, json, or sqlite), so that I can plug the result into different downstream tools.
5. As an operator, I want a `--verbose` flag on `redact-target` that reports, to stderr, every input line where no domain match was found and the total count, so that I can manually audit rows the automated pass couldn't handle.
6. As an operator, I want lines with no domain match to still be written through unchanged (not dropped) by default, so that I never lose data silently.
7. As an operator, I want to run `redirecthunter expand-target` on a file of `{TARGET}` templates and a target value, so that I get back a file of real, ready-to-request URLs without running a full scan.
8. As an operator, I want an `--encode` flag on `expand-target` that percent-encodes the target value before substitution, so that I can safely embed it inside a query-string parameter when the destination expects an encoded value.
9. As an operator, I want `expand-target` to leave lines with no `{TARGET}` token unchanged, so that mixed files (some templated, some not) still round-trip safely.
10. As an operator upgrading from the old script, I want the new commands' flags to feel familiar, so that I don't have to relearn the tool: single-letter aliases (`-d`, `-f`, `-t`, `-v`, `-o`) are available alongside the long `--option` forms already used by `scan`/`export`.
11. As an operator, I want `redact-target`'s input format to stay plain-text (one URL per line), so that the command matches the actual shape of scraped backlink data without unnecessary format-detection complexity.
12. As an operator, I want `expand-target` to always read and write plain text, so that it stays a lightweight preview step rather than a second archival pipeline.
13. As a maintainer, I want the domain-matching regex to live in `redirecthunter/utils.py` as a `redact_domain()` function paired with the existing `expand_target()`, so that both halves of the `{TARGET}` substitution contract are discoverable in one place.
14. As a maintainer, I want `redact-target`'s multi-format output and file I/O implemented in a new, narrowly-scoped module (not by force-fitting `ScanConfig`/`CandidateURL`/`load_candidates`), so that this feature doesn't inherit state and fields meant for network scanning.
15. As a maintainer, I want the three known limitations of the old regex (partially-broken percent-encoding, two URLs concatenated with no separator, an encoded Google `site:` operator glued to the domain) preserved and re-documented on `redact_domain()`, so that behavior doesn't silently regress or silently improve in ways nobody verified.
16. As a maintainer, I want those three known limitations proven by explicit, named tests (not `xfail`), so that they read as documented, intentional behavior rather than accidental bugs.
17. As a maintainer, I want a single new test file (`tests/test_target_replace.py`) covering both the `utils.py` additions and the two new CLI commands, so that all target-replace-related test coverage is discoverable in one place.
18. As a maintainer, I want `examples/url-target-replace.sh` deleted (not just deprecated) once the CLI commands exist, with a short migration pointer added to `examples/README.md`, so that the repo doesn't carry two implementations of the same behavior.
19. As a maintainer, I want the package version bumped to `1.1.0` and the new commands mentioned in the top-level `README.md`, so that the new, non-breaking feature is discoverable and versioned like any other release.
20. As an operator, I want `redact-target`'s CLI help text and `docs/CLI_REFERENCE.md` entry to include the same kind of runnable examples the other commands have, so that I can copy-paste my way to a working invocation.

## Implementation Decisions

- **New module `redirecthunter/target_replace.py`**: owns the file I/O for both commands — a plain-text line reader/writer for `expand-target`, and a multi-format writer (txt/csv/json/sqlite) for `redact-target`. It does **not** depend on `ScanConfig`, `CandidateURL`, or `load_candidates`; those model network-scan state that doesn't apply here.
- **New function `redirecthunter.utils.redact_domain(text, domain, *, token=TARGET_PLACEHOLDER)`**: houses the boundary-aware regex (scheme-optional, `www.`-optional, percent-encoding variants, left/right alphanumeric boundary guards) ported from the Perl implementation. Lives beside `expand_target()` as its logical counterpart. Public, and covered directly by `tests/test_target_replace.py` the same way other `utils.py` functions are covered by `tests/test_utils.py`.
- **New enum `RedactFormat`** (in `models.py`, alongside `InputFormat`/`ExportFormat`): `TXT`, `CSV`, `JSON`, `SQLITE`. Not a reuse of `ExportFormat`, since that enum's semantics are "scan result export," not "redacted URL list."
- **New CLI command `redact-target`** in `cli.py`:
  - Positional argument: input file path (plain text, one URL per line — mirrors `scan`'s `input_file` positional).
  - `-o/--output` (path; default stdout).
  - `-f/--format` (RedactFormat; default inferred from `-o` extension, else `txt` — mirrors the old script's auto-detect-from-extension behavior).
  - `-d/--domain` (required).
  - `-t/--token` (default `{TARGET}`, i.e. `utils.TARGET_PLACEHOLDER`).
  - `-v/--verbose` (report skipped/no-match lines + total count to stderr).
  - Unmatched lines are written through unchanged by default (fail-open), matching the old script.
- **New CLI command `expand-target`** in `cli.py`:
  - Positional argument: input file path (plain text, one templated URL per line).
  - `-o/--output` (path; default stdout).
  - `--target` (required; the replacement value — reuses the same option name/semantics as `scan --target`).
  - `--encode` (flag; passes `url_encode=True` through to `expand_target()`).
  - `-v/--verbose` (report lines with no `{TARGET}` token + total count to stderr, mirroring `redact-target`'s verbose behavior).
  - Always plain-text in, plain-text out — no format flag.
  - Internally calls `redirecthunter.utils.expand_target()` per line; lines without the token pass through unchanged (this is already `expand_target()`'s behavior).
- **CLI flag style**: both commands follow existing Typer conventions (positional input file, long `--option` names) *and* additionally register the single-letter aliases above, since the old script's operators are used to them. This is an intentional exception to the rest of the CLI (`scan`/`export`/etc. use long-only flags).
- **Deletion**: `examples/url-target-replace.sh` is removed. `examples/README.md` gets a short section pointing at `redirecthunter redact-target` / `redirecthunter expand-target` as the replacement, with one example invocation each.
- **Docs**: `docs/CLI_REFERENCE.md` gets full entries for both new commands (flags, defaults, examples), in the same format as the existing `scan`/`export`/etc. entries.
- **Versioning**: `pyproject.toml` version bumped `1.0.0` → `1.1.0`. Top-level `README.md` gets a mention of the two new commands as a new feature.

## Testing Decisions

- All new tests live in a single new file: `tests/test_target_replace.py`.
- Follow existing project conventions:
  - Pure-function tests for `redact_domain()` / the `expand_target()` call path, in the same style as `tests/test_utils.py` (table-driven cases for scheme variants, `www.`, percent-encoding variants, left/right boundary rejection).
  - CLI tests for `redact-target` / `expand-target` using Typer's `CliRunner`, in the same style as `tests/test_cli.py` (write a temp input file, invoke the command, assert on output file/stdout content and exit code).
- Good tests here assert on **observable behavior** — the output file/stdout content and stderr verbose report — not on internal regex construction.
- The three known limitations (broken partial percent-encoding, two concatenated URLs, encoded `site:` operator glued to a domain) each get an explicit, named test asserting the line is *not* transformed — documenting them as known, intentional behavior rather than leaving them to be rediscovered as "bugs" later.
- `RedactFormat` output writers (csv/json/sqlite) are tested for structural correctness (headers, row shape, SQLite table/columns) the same way `Exporter`'s formats are tested in `tests/test_exporter.py`.

## Out of Scope

- Fixing/improving the three documented regex limitations.
- Multi-format *input* for `redact-target` (stays plain-text only).
- Any format flag or multi-format support for `expand-target` (stays plain-text in/out only).
- Matching against multiple domains in a single `redact-target` invocation.
- A CHANGELOG file (none exists today; not being introduced by this work).
- Any change to `redirecthunter scan --target` behavior itself — `expand_target()`'s existing contract is reused as-is.

## Further Notes

- `confirmed_backlinks.txt` at the repo root is real output of the old script and evidence this is production tooling, not example-only — worth keeping in mind if anyone asks why this got "CLI command" treatment instead of staying an example.
- The full grilling/decision history behind this spec is in the parent conversation; this file is the synthesized result, not a transcript.
