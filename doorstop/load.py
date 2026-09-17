"""Persists extracted diary entries as a CSV snapshot and a DuckDB table,
and separately, merges freshly discovered diary PDF URLs into a Type 2
slowly-changing reference table.

The CSV is the raw layer: one row per meeting entry, as close to the source
PDF as extraction allows, with nothing cleaned or typed. DuckDB just loads
that CSV so it can be queried with SQL — this is also the file a future dbt
project (dbt-duckdb) would treat as its source database.

`merge_ministerial_diaries` is a different persistence shape entirely: an
upsert against a small, durable table that tracks reachability over time
(`first_seen`/`last_seen`/`still_listed`), not a wipe-and-rebuild of a CSV
snapshot - closing a row on disappearance and opening a *new* row on
reappearance rather than reviving the old one, so a gap in listing is never
silently erased.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from doorstop.extract import DiaryEntry

RAW_TABLE_NAME = "raw_diary_entries"


def write_csv(entries: list[DiaryEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(entries[0].to_dict().keys()))
        writer.writeheader()
        for entry in entries:
            writer.writerow(entry.to_dict())


def load_duckdb(csv_path: Path, db_path: Path, table_name: str = RAW_TABLE_NAME) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_csv_auto(?)",
            [str(csv_path)],
        )


def _existing_diary_urls(con: duckdb.DuckDBPyConnection, table_name: str) -> tuple[set[str], set[str]]:
    """Returns (previously_open, previously_seen) pdf_url sets from
    table_name, or two empty sets if the table doesn't exist yet (first
    run)."""
    exists = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table_name]
    ).fetchone()
    if exists is None:
        return set(), set()
    previously_open = {
        row[0] for row in con.execute(f'SELECT pdf_url FROM "{table_name}" WHERE still_listed').fetchall()
    }
    previously_seen = {row[0] for row in con.execute(f'SELECT DISTINCT pdf_url FROM "{table_name}"').fetchall()}
    return previously_open, previously_seen


def diary_diff(pdf_urls: list[str], db_path: Path, table_name: str) -> dict[str, int]:
    """Read-only comparison of freshly discovered `pdf_urls` against
    `table_name`'s current state, without writing anything - used for the
    `--dry-run` summary and to print the same counts alongside a real
    write."""
    found = set(pdf_urls)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        previously_open, previously_seen = _existing_diary_urls(con, table_name)
    return {
        "new": len((found - previously_open) - previously_seen),
        "extended": len(previously_open & found),
        "newly_delisted": len(previously_open - found),
        "reappeared": len((found - previously_open) & previously_seen),
    }


def merge_ministerial_diaries(pdf_urls: list[str], db_path: Path, table_name: str) -> None:
    """Type 2 slowly-changing merge of freshly discovered diary PDF URLs
    into `table_name`: extends already-open rows found again, closes open
    rows not found this run (`still_listed = false`, never deleted), and
    opens a *new* row for anything with no currently-open row - covering
    both brand-new URLs and URLs reappearing after a gap, so a disappearance
    is never silently erased by reviving the same row."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Stored as naive UTC (not TIMESTAMPTZ) so values are unambiguous regardless of the
    # machine's local timezone, without pulling in DuckDB's timezone-conversion behaviour.
    now = datetime.now(UTC).replace(tzinfo=None)
    found = list(set(pdf_urls))
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{table_name}" (
                pdf_url VARCHAR,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                still_listed BOOLEAN
            )
            """
        )
        con.execute("CREATE OR REPLACE TEMP TABLE _found_diary_urls AS SELECT UNNEST(?) AS pdf_url", [found])

        con.execute(
            f"""
            UPDATE "{table_name}"
            SET last_seen = ?
            WHERE still_listed AND pdf_url IN (SELECT pdf_url FROM _found_diary_urls)
            """,
            [now],
        )

        con.execute(
            f"""
            UPDATE "{table_name}"
            SET still_listed = false
            WHERE still_listed AND pdf_url NOT IN (SELECT pdf_url FROM _found_diary_urls)
            """
        )

        con.execute(
            f"""
            INSERT INTO "{table_name}" (pdf_url, first_seen, last_seen, still_listed)
            SELECT pdf_url, ?, ?, true
            FROM _found_diary_urls
            WHERE pdf_url NOT IN (SELECT pdf_url FROM "{table_name}" WHERE still_listed)
            """,
            [now, now],
        )
