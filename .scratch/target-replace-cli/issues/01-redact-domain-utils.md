# 01 — Add `redact_domain()` to `redirecthunter/utils.py`

**What to build:** A public, tested `redact_domain(text, domain, *, token=TARGET_PLACEHOLDER)` function in `redirecthunter/utils.py` that finds occurrences of `domain` inside `text` — tolerating optional scheme (`http`/`https`, plain or percent-encoded), optional `www.`, and optional trailing slash(es) (plain or percent-encoded) — and replaces each occurrence with `token`, while rejecting matches that are really a substring of a different domain (left/right alphanumeric boundary guards). This ports the matching behavior of the Perl engine in the old `examples/url-target-replace.sh` into Python, as the counterpart to the existing `expand_target()`.

**Blocked by:** None — can start immediately.

**Status:** done (shipped in 1.1.0)

- [ ] `redact_domain()` added to `redirecthunter/utils.py`, exported alongside `expand_target()`/`TARGET_PLACEHOLDER`, with a docstring matching the style of other functions in that file.
- [ ] Matches and replaces: bare domain, `www.`-prefixed, `http://`/`https://`-prefixed, percent-encoded scheme variants (`%3A%2F%2F`, `%3A%2F`, etc.), and combinations of the above, including trailing slash(es) in plain or encoded form.
- [ ] Does NOT match a domain that is a substring of a different, longer domain on either side (e.g. searching for `medilana.id` does not match inside `tmedilana.id` or `medilana.id.cheapdealuk.co.uk`).
- [ ] Default `token` is `TARGET_PLACEHOLDER` (`"{TARGET}"`); a custom token string can be passed.
- [ ] Three known limitations are preserved and documented in the function's docstring, each with an explicit, named test in `tests/test_target_replace.py` asserting the line is left unchanged (not `xfail`):
  - [ ] Partially-broken percent-encoding (e.g. a truncated `%2F`) is not recognized as a scheme, so the domain is left unreplaced.
  - [ ] Two URLs concatenated with no separator (e.g. `...domain.idhttp://other.com/...`) does not trigger a replacement at that boundary.
  - [ ] An encoded Google `site:` operator glued directly to the domain (e.g. `site%3Adomain.id`) may fail the left-boundary check and be left unreplaced.
- [ ] `tests/test_target_replace.py` created with table-driven test cases covering all of the above, in the style of `tests/test_utils.py`.
