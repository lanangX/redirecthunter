# 01 — Multi-target split in `loader.py`

**What to build:** Extend the per-row `|target` override so its
right-hand side may be a `;`-separated list, across all three input
formats:

- `_split_target_override(stripped: str) -> tuple[str, tuple[str, ...]]`
  -- change return type from `str | None` to `tuple[str, ...]` (empty
  tuple = no override). After `partition("|")`, split the target part on
  `;`, `.strip()` each piece, drop empty pieces. A bare single target
  (no `;`) yields a one-element tuple, matching today's behavior under
  the new type.
- `_iter_txt`: `metadata = {"target": targets} if targets else {}` --
  same shape as today, just carrying a tuple instead of a bare string.
- `_iter_csv`: the `target` column's raw cell value goes through the
  same split-and-strip logic before being stored in
  `row_metadata["target"]`.
- `_iter_json`: the `"target"` key's raw string value goes through the
  same split-and-strip logic.
- Guard: if every piece is blank after stripping (`a|;;`, `a|`), treat
  as no override at all -- do not store an empty tuple/set.

**Blocked by:** None.

**Status:** done

- [x] `_split_target_override` returns `tuple[str, ...]`; single-target
      and no-target callers get the same effective values as before
      under the new type.
- [x] `_iter_txt` multi-target rows produce `row_metadata["target"]` as
      a tuple with all non-empty, stripped pieces.
- [x] `_iter_csv` `target` column supports the same `;`-list syntax.
- [x] `_iter_json` `"target"` key supports the same `;`-list syntax.
- [x] All-blank-after-split rows (`a|;;`, `a|`) produce no override key,
      not an empty tuple.
- [x] `tests/test_loader.py`: new cases for `_iter_txt`/`_iter_csv`/
      `_iter_json` covering multi-target, whitespace-padded entries,
      trailing/duplicate `;`, and the existing single-target/no-target
      cases still passing unmodified.
