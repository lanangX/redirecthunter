# 01 — Extract matching logic into `redirecthunter/backlink.py`

**What to build:** Move `BacklinkResult`, `check_one()`, and every
hostname/pattern-matching helper (`normalize_domain`, `normalize_hostname`,
`extract_hostname`, `hostname_matches`, `looks_like_login_wall`,
`looks_like_bot_block_status`, `looks_like_challenge_page`,
`build_text_mention_pattern`) out of `backlink_checker.py` and into a new
`redirecthunter/backlink.py` module. `BacklinkResult` becomes a Pydantic
model (not a dataclass) so it can later flow through the same
database-serialization path `CrawlPageResult`/`CrawlLinkResult` already
use. `backlink_checker.py` keeps its own argparse CLI, CSV writer, and Rich
summary table, but imports the model/logic from the new module.
`backlink_checker_js.py`'s existing import is repointed at the new module.

From the operator's perspective, nothing changes: both root scripts behave
identically before and after this ticket -- same flags, same CSV output,
same summary table.

**Blocked by:** None — can start immediately.

**Status:** done

- [x] `redirecthunter/backlink.py` exists and exports `BacklinkResult` (Pydantic model) plus every matching/detection helper listed above.
- [x] `backlink_checker.py` imports these from `redirecthunter/backlink.py` instead of defining them; its CLI, output, and behavior are unchanged.
- [x] `backlink_checker_js.py`'s import is repointed at `redirecthunter/backlink.py`; its behavior is unchanged.
- [x] `tests/test_backlink_checker.py` passes unmodified — proof the extraction didn't change behavior.
- [x] New `tests/test_backlink.py` covers the moved helpers directly (can be ported from whatever `tests/test_backlink_checker.py` already exercises).
