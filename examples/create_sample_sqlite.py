#!/usr/bin/env python3
"""Generate examples/urls.db — a sample SQLite candidate-URL input file.

RedirectHunter's SQLite input format reads candidate URLs from an
arbitrary table/column (see --input-table / --input-column), rather than
requiring a fixed schema. This script builds a small, realistic example
using the defaults (table 'urls', column 'url') so
`redirecthunter scan examples/urls.db` works with no extra flags.

Run with:
    python3 examples/create_sample_sqlite.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

OUTPUT_PATH = Path(__file__).parent / "urls.db"

SAMPLE_ROWS = [
    ("https://example.com/go?url={TARGET}", "spring-launch", "marketing-team"),
    ("https://example.com/redirect?to={TARGET}", "newsletter-2026", "marketing-team"),
    ("https://example.com/out.php?goto={TARGET}", "legacy-affiliate", "partnerships-team"),
    ("https://example.com/click?redirect={TARGET}", "social-share", "social-team"),
]


def main() -> None:
    if OUTPUT_PATH.exists():
        OUTPUT_PATH.unlink()

    conn = sqlite3.connect(OUTPUT_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                campaign TEXT,
                owner TEXT
            )
            """
        )
        conn.executemany("INSERT INTO urls (url, campaign, owner) VALUES (?, ?, ?)", SAMPLE_ROWS)
        conn.commit()
    finally:
        conn.close()

    print(f"Wrote {len(SAMPLE_ROWS)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
