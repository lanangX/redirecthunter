"""Tests for redirecthunter.run's generic RunLifecycle.

Exercised against a throwaway sqlite table shaped like a real lifecycle
table (mirrors ``crawls``), not against ``crawl_run``/``backlink_run``
directly -- those are covered by test_crawl_database.py/test_backlink.py
continuing to pass unchanged (see the architecture-review session for
why that's the regression guard for this deepening).
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from pydantic import BaseModel

from redirecthunter.models import RunStatus
from redirecthunter.run import RunLifecycle, RunLifecycleError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS widget_runs (
    widget_run_id TEXT PRIMARY KEY,
    label         TEXT,
    flavor        TEXT NOT NULL,
    status        TEXT NOT NULL,
    config_json   TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT
);
"""


class WidgetRunConfig(BaseModel):
    widget_run_id: str
    label: str | None = None
    flavor: str = "vanilla"


LIFECYCLE: RunLifecycle[WidgetRunConfig] = RunLifecycle(
    table="widget_runs",
    id_column="widget_run_id",
    config_model=WidgetRunConfig,
    get_id=lambda c: c.widget_run_id,
    get_label=lambda c: c.label,
    kind_label="widget run",
    extra_columns=("flavor",),
    extract_extra=lambda c: (c.flavor,),
)


@pytest.fixture
async def conn(tmp_path: Path):
    connection = await aiosqlite.connect(tmp_path / "widget.db")
    connection.row_factory = aiosqlite.Row
    await connection.executescript(_SCHEMA)
    await connection.commit()
    yield connection
    await connection.close()


class TestRunLifecycle:
    async def test_create_then_exists(self, conn: aiosqlite.Connection) -> None:
        config = WidgetRunConfig(widget_run_id="w-1", label="first", flavor="mint")
        await LIFECYCLE.create(conn, config)
        assert await LIFECYCLE.exists(conn, "w-1") is True
        assert await LIFECYCLE.exists(conn, "nope") is False

    async def test_create_persists_extra_columns_and_config_json(self, conn: aiosqlite.Connection) -> None:
        config = WidgetRunConfig(widget_run_id="w-2", flavor="mint")
        await LIFECYCLE.create(conn, config)

        cursor = await conn.execute("SELECT flavor, status FROM widget_runs WHERE widget_run_id = ?", ("w-2",))
        row = await cursor.fetchone()
        await cursor.close()
        assert row["flavor"] == "mint"
        assert row["status"] == RunStatus.RUNNING.value

        fetched = await LIFECYCLE.get_config(conn, "w-2")
        assert fetched == config

    async def test_get_config_missing_returns_none(self, conn: aiosqlite.Connection) -> None:
        assert await LIFECYCLE.get_config(conn, "nope") is None

    async def test_update_status_without_finished(self, conn: aiosqlite.Connection) -> None:
        await LIFECYCLE.create(conn, WidgetRunConfig(widget_run_id="w-3"))
        await LIFECYCLE.update_status(conn, "w-3", RunStatus.FAILED)

        cursor = await conn.execute("SELECT status, finished_at FROM widget_runs WHERE widget_run_id = ?", ("w-3",))
        row = await cursor.fetchone()
        await cursor.close()
        assert row["status"] == RunStatus.FAILED.value
        assert row["finished_at"] is None

    async def test_update_status_with_finished_stamps_timestamp(self, conn: aiosqlite.Connection) -> None:
        await LIFECYCLE.create(conn, WidgetRunConfig(widget_run_id="w-4"))
        await LIFECYCLE.update_status(conn, "w-4", RunStatus.COMPLETED, finished=True)

        cursor = await conn.execute("SELECT status, finished_at FROM widget_runs WHERE widget_run_id = ?", ("w-4",))
        row = await cursor.fetchone()
        await cursor.close()
        assert row["status"] == RunStatus.COMPLETED.value
        assert row["finished_at"] is not None

    async def test_resolve_id_full_and_prefix(self, conn: aiosqlite.Connection) -> None:
        await LIFECYCLE.create(conn, WidgetRunConfig(widget_run_id="abcdef12-full"))
        assert await LIFECYCLE.resolve_id(conn, "abcdef12-full") == "abcdef12-full"
        assert await LIFECYCLE.resolve_id(conn, "abcdef12") == "abcdef12-full"

    async def test_resolve_id_not_found_message_uses_kind_label(self, conn: aiosqlite.Connection) -> None:
        with pytest.raises(RunLifecycleError, match="No widget run found matching 'nonexistent'"):
            await LIFECYCLE.resolve_id(conn, "nonexistent")

    async def test_resolve_id_ambiguous_prefix_raises(self, conn: aiosqlite.Connection) -> None:
        await LIFECYCLE.create(conn, WidgetRunConfig(widget_run_id="dupe-1"))
        await LIFECYCLE.create(conn, WidgetRunConfig(widget_run_id="dupe-2"))
        with pytest.raises(RunLifecycleError, match="matches 2 widget runs"):
            await LIFECYCLE.resolve_id(conn, "dupe-")

    async def test_delete_removes_row(self, conn: aiosqlite.Connection) -> None:
        await LIFECYCLE.create(conn, WidgetRunConfig(widget_run_id="w-5"))
        await LIFECYCLE.delete(conn, "w-5")
        assert await LIFECYCLE.exists(conn, "w-5") is False
