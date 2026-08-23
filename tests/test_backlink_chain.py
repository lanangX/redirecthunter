"""End-to-end tests for the `bl-chain` command (ticket 03).

Style matches `tests/test_cli.py`'s `TestBacklinkCheckCommands`: Typer's
CliRunner drives the real command, respx intercepts httpx at the URL
level (no real sockets) -- the same "local HTTP fixture" pattern
`tests/test_backlink.py`'s `TestCheckOne` class already uses for
matching-core tests, just wired through the full CLI/DB path here.

Covers (per `.scratch/bl-chain/spec.md`'s "Testing Decisions"):
  - A 2-tier chain where tier 2's *default* target set is derived from
    tier 1's own input URLs, in both `--require-confirmed-parent` states.
  - A per-row `|target` override winning over that derived default.
  - A 3-tier chain, proving tier 3 derives correctly from tier 2.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from redirecthunter.cli import app
from redirecthunter.database import Database

runner = CliRunner(env={"COLUMNS": "220", "TERM": "xterm-256color"})


def _mock_page(mock: respx.MockRouter, url: str, *, links_to: str | None) -> None:
    """Mock one GET returning a minimal HTML page, optionally with one outbound anchor."""
    body = "<html><body>no links here</body></html>"
    if links_to is not None:
        body = f'<html><body><a href="{links_to}">link</a></body></html>'
    mock.get(url).mock(return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, text=body))


class TestBlChainTwoTier:
    """Tier 1: two rows, one that confirms against the root domain and one that doesn't.
    Tier 2: one row that only matches if the *unconfirmed* tier-1 host is included in
    the derived default target set, plus one row with a per-row |target override that
    must win regardless of the derived default."""

    def _write_tiers(self, tmp_path: Path) -> tuple[Path, Path]:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text(
            "https://tier1a.test/page\n"
            "https://tier1b.test/page\n"
        )
        tier2 = tmp_path / "tier2.txt"
        tier2.write_text(
            "https://tier2main.test/page\n"
            "https://tier2override.test/page|override.test\n"
        )
        return tier1, tier2

    def _mock_common(self, mock: respx.MockRouter) -> None:
        # Tier 1: tier1a confirms against the root domain, tier1b does not.
        _mock_page(mock, "https://tier1a.test/page", links_to="https://money.test/landing")
        _mock_page(mock, "https://tier1b.test/page", links_to=None)
        # Tier 2: tier2main links to the *unconfirmed* tier1b host -- only found
        # when the derived default set includes all of tier 1's input URLs.
        _mock_page(mock, "https://tier2main.test/page", links_to="https://tier1b.test/other")
        # Tier 2: tier2override carries a per-row |target override to
        # override.test, and its page links there -- must match regardless
        # of what tier 1 derived.
        _mock_page(mock, "https://tier2override.test/page", links_to="https://override.test/x")

    def test_default_derivation_includes_all_tier1_input_urls(self, tmp_path: Path) -> None:
        tier1, tier2 = self._write_tiers(tmp_path)
        db_path = tmp_path / "chain.db"

        with respx.mock(assert_all_called=True) as mock:
            self._mock_common(mock)
            result = runner.invoke(
                app,
                [
                    "bl-chain", str(tier1), str(tier2),
                    "-d", "money.test",
                    "--database", str(db_path),
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Backlink Chain Summary" in result.output

        import re

        chain_id_match = re.search(r"chain_id:\s*(\S+)", result.output)
        assert chain_id_match is not None

        async def _check() -> None:
            db = Database(db_path)
            await db.connect()
            try:
                summary = await db.get_backlink_chain_summary(chain_id_match.group(1))
                assert summary is not None
                assert len(summary.tiers) == 2
                tier1_summary, tier2_summary = summary.tiers
                assert tier1_summary.confirmed == 1  # tier1a only
                assert tier1_summary.total_urls == 2
                # tier2main matched (tier1b.test was in the derived "all inputs"
                # set) AND tier2override matched (its own |target override) --
                # both tier-2 rows confirmed.
                assert tier2_summary.confirmed == 2
                assert tier2_summary.total_urls == 2
            finally:
                await db.close()

        import asyncio

        asyncio.run(_check())

    def test_require_confirmed_parent_excludes_unconfirmed_tier1_hosts(self, tmp_path: Path) -> None:
        tier1, tier2 = self._write_tiers(tmp_path)
        db_path = tmp_path / "chain.db"

        with respx.mock(assert_all_called=True) as mock:
            self._mock_common(mock)
            result = runner.invoke(
                app,
                [
                    "bl-chain", str(tier1), str(tier2),
                    "-d", "money.test",
                    "--require-confirmed-parent",
                    "--database", str(db_path),
                ],
            )

        assert result.exit_code == 0, result.output

        import re

        chain_id_match = re.search(r"chain_id:\s*(\S+)", result.output)
        assert chain_id_match is not None

        async def _check() -> None:
            db = Database(db_path)
            await db.connect()
            try:
                summary = await db.get_backlink_chain_summary(chain_id_match.group(1))
                assert summary is not None
                tier1_summary, tier2_summary = summary.tiers
                assert tier1_summary.confirmed == 1
                # Under --require-confirmed-parent, tier 2's derived default
                # target set only contains tier1a.test (the confirmed row) --
                # tier2main links to tier1b.test, which is now excluded, so it
                # must NOT match. tier2override still matches via its own
                # per-row override, independent of the derived default.
                assert tier2_summary.confirmed == 1
                assert tier2_summary.not_found == 1
                assert tier2_summary.total_urls == 2
            finally:
                await db.close()

        import asyncio

        asyncio.run(_check())


class TestBlChainThreeTier:
    def test_tier_three_derives_from_tier_two_inputs(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://tier1.test/page\n")
        tier2 = tmp_path / "tier2.txt"
        tier2.write_text("https://tier2.test/page\n")
        tier3 = tmp_path / "tier3.txt"
        tier3.write_text("https://tier3.test/page\n")
        db_path = tmp_path / "chain.db"

        with respx.mock(assert_all_called=True) as mock:
            _mock_page(mock, "https://tier1.test/page", links_to="https://money.test/landing")
            _mock_page(mock, "https://tier2.test/page", links_to="https://tier1.test/other")
            # tier3 links to tier2.test -- only found if tier3's derived
            # default target set correctly comes from tier 2's own input URLs.
            _mock_page(mock, "https://tier3.test/page", links_to="https://tier2.test/other")

            result = runner.invoke(
                app,
                [
                    "bl-chain", str(tier1), str(tier2), str(tier3),
                    "-d", "money.test",
                    "--database", str(db_path),
                ],
            )

        assert result.exit_code == 0, result.output

        import re

        chain_id_match = re.search(r"chain_id:\s*(\S+)", result.output)
        assert chain_id_match is not None

        async def _check() -> None:
            db = Database(db_path)
            await db.connect()
            try:
                summary = await db.get_backlink_chain_summary(chain_id_match.group(1))
                assert summary is not None
                assert len(summary.tiers) == 3
                t1, t2, t3 = summary.tiers
                assert t1.confirmed == 1
                assert t2.confirmed == 1
                assert t3.confirmed == 1
            finally:
                await db.close()

        import asyncio

        asyncio.run(_check())


class TestBlChainValidation:
    def test_single_tier_is_rejected(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://tier1.test/page\n")
        result = runner.invoke(app, ["bl-chain", str(tier1), "-d", "money.test"])
        assert result.exit_code == 1
        assert "at least 2 tier files" in result.output

    def test_headed_without_browser_is_rejected(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("https://tier1.test/page\n")
        tier2 = tmp_path / "tier2.txt"
        tier2.write_text("https://tier2.test/page\n")
        result = runner.invoke(
            app, ["bl-chain", str(tier1), str(tier2), "-d", "money.test", "--headed"]
        )
        assert result.exit_code == 1
        assert "--headed only makes sense with --browser" in result.output

    def test_empty_tier_file_aborts_chain(self, tmp_path: Path) -> None:
        tier1 = tmp_path / "tier1.txt"
        tier1.write_text("")
        tier2 = tmp_path / "tier2.txt"
        tier2.write_text("https://tier2.test/page\n")
        result = runner.invoke(
            app, ["bl-chain", str(tier1), str(tier2), "-d", "money.test"]
        )
        assert result.exit_code == 1
        assert "has no URLs" in result.output
