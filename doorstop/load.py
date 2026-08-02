"""Persists extracted diary entries as a CSV snapshot and a DuckDB table.

The CSV is the raw layer: one row per meeting entry, as close to the source
PDF as extraction allows, with nothing cleaned or typed. DuckDB just loads
that CSV so it can be queried with SQL — this is also the file a future dbt
project (dbt-duckdb) would treat as its source database.
"""

from __future__ import annotations

import csv
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
