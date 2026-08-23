"""redirecthunter/run.py — the generic lifecycle of one "run".

A **run** is one execution of `scan`, `crawl`, or `bl-check`: created,
given an id, worked, and eventually completed/interrupted/failed. See
``CONTEXT.md``'s "Run" entry for the full domain-model writeup and the
design-tree trace behind this module (architecture review, 2026-08-20).

``RunLifecycle`` owns exactly the mechanical part of that concept --
create / exists / get_config / update_status / resolve_id / delete --
proven byte-identical in shape across ``scan``/``crawl``/``backlink_check``
(only the table name, id column, and a handful of kind-specific extra
columns differ). Each run kind's *results* (what got checked, and how)
stay owned by that kind's own save/iter methods on :class:`Database` --
``scan``/``backlink_check`` each have exactly one result table, but
``crawl`` has two (``crawl_pages`` and ``crawl_links``) with genuinely
different ID-generation and timestamp-column conventions, so forcing a
single generic "ResultStream" shape onto all three would just relocate
the shallow-interface problem this module exists to remove. That
generalization is deliberately deferred, not attempted here.

Like :mod:`redirecthunter.backlink`, this module sits below
:mod:`redirecthunter.database` in the import graph and knows nothing
about it -- callers translate :class:`RunLifecycleError` into whatever
their own error type is.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import aiosqlite
from pydantic import BaseModel

from redirecthunter.models import RunStatus


class RunLifecycleError(Exception):
    """Raised when a run id can't be resolved (missing, or an ambiguous prefix)."""


@dataclass(frozen=True, slots=True)
class RunLifecycle[TConfig: BaseModel]:
    """Descriptor + operations for one run kind's lifecycle table.

    Every lifecycle table shares the same core columns (id, label,
    status, config_json, started_at, finished_at) plus a handful of
    kind-specific extra columns (e.g. crawl's ``seed_mode``/``seed_url``/
    ``seed_input_path``, or backlink-check's ``domain``/``input_path``).
    ``extra_columns``/``extract_extra`` parameterize that difference so
    one implementation serves every kind -- see the module docstring for
    why results (not lifecycle) are excluded from this generalization.

    Instances are stateless descriptors: they take the live connection
    and write-lock as explicit call arguments rather than storing them,
    so the same instance can be shared as a module-level constant and
    tested without a real ``Database`` object attached.
    """

    table: str
    id_column: str
    config_model: type[TConfig]
    get_id: Callable[[TConfig], str]
    get_label: Callable[[TConfig], str | None]
    kind_label: str
    extra_columns: tuple[str, ...] = ()
    extract_extra: Callable[[TConfig], tuple[object, ...]] = field(default=lambda _config: ())

    async def exists(self, conn: aiosqlite.Connection, run_id: str) -> bool:
        """Return True if a run with this id has been recorded."""
        cursor = await conn.execute(f"SELECT 1 FROM {self.table} WHERE {self.id_column} = ?", (run_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None

    async def create(self, conn: aiosqlite.Connection, config: TConfig) -> None:
        """Insert a new run record."""
        columns = (self.id_column, "label", *self.extra_columns, "status", "config_json", "started_at")
        placeholders = ", ".join("?" for _ in columns)
        values = (
            self.get_id(config),
            self.get_label(config),
            *self.extract_extra(config),
            RunStatus.RUNNING.value,
            config.model_dump_json(),
            datetime.now(UTC).isoformat(),
        )
        await conn.execute(
            f"INSERT INTO {self.table} ({', '.join(columns)}, finished_at) "
            f"VALUES ({placeholders}, NULL)",
            values,
        )
        await conn.commit()

    async def get_config(self, conn: aiosqlite.Connection, run_id: str) -> TConfig | None:
        """Fetch and rebuild the config originally used for ``run_id``."""
        cursor = await conn.execute(
            f"SELECT config_json FROM {self.table} WHERE {self.id_column} = ?", (run_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return self.config_model.model_validate_json(row["config_json"])

    async def update_status(
        self, conn: aiosqlite.Connection, run_id: str, status: RunStatus, *, finished: bool = False
    ) -> None:
        """Update a run's lifecycle status, optionally stamping ``finished_at``."""
        if finished:
            await conn.execute(
                f"UPDATE {self.table} SET status = ?, finished_at = ? WHERE {self.id_column} = ?",
                (status.value, datetime.now(UTC).isoformat(), run_id),
            )
        else:
            await conn.execute(
                f"UPDATE {self.table} SET status = ? WHERE {self.id_column} = ?", (status.value, run_id)
            )
        await conn.commit()

    async def resolve_id(self, conn: aiosqlite.Connection, partial_id: str) -> str:
        """Resolve a full or shortened (prefix) run id to its full UUID."""
        if await self.exists(conn, partial_id):
            return partial_id

        cursor = await conn.execute(
            f"SELECT {self.id_column} FROM {self.table} WHERE {self.id_column} LIKE ? ESCAPE '\\'",
            (partial_id.replace("%", r"\%").replace("_", r"\_") + "%",),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        matches: Sequence[str] = [str(row[self.id_column]) for row in rows]

        if not matches:
            raise RunLifecycleError(f"No {self.kind_label} found matching '{partial_id}'.")
        if len(matches) > 1:
            preview = ", ".join(m[:8] for m in matches[:5])
            suffix = ", ..." if len(matches) > 5 else ""
            raise RunLifecycleError(
                f"'{partial_id}' matches {len(matches)} {self.kind_label}s ({preview}{suffix}). "
                f"Use more characters to disambiguate."
            )
        return matches[0]

    async def delete(self, conn: aiosqlite.Connection, run_id: str) -> None:
        """Delete the lifecycle row. Relies on ``ON DELETE CASCADE`` to remove its results."""
        await conn.execute(f"DELETE FROM {self.table} WHERE {self.id_column} = ?", (run_id,))
        await conn.commit()


def new_run_id() -> str:
    """Generate a new run id. Shared so every kind's id looks the same shape."""
    return str(uuid.uuid4())
