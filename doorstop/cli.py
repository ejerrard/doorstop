"""Entry points for the doorstop pipelines.

python -m doorstop extract [input_dir]
python -m doorstop discover-ministerial-diaries
"""

from __future__ import annotations

from pathlib import Path

import typer

from doorstop.cabinet import discover_ministerial_diaries
from doorstop.extract import extract_pdf
from doorstop.load import (
    RAW_TABLE_NAME,
    diary_diff,
    load_duckdb,
    merge_ministerial_diaries,
    write_csv,
)

DEFAULT_INPUT_DIR = Path("data/samples")
CSV_PATH = Path("data/processed/raw_diary_entries.csv")
DB_PATH = Path("data/doorstop.duckdb")
MINISTERIAL_DIARIES_TABLE_NAME = "ref_ministerial_diaries"

app = typer.Typer()


@app.command()
def extract(
    input_dir: Path = typer.Argument(
        DEFAULT_INPUT_DIR, help="Directory of diary PDFs to extract."
    ),
    csv_path: Path = typer.Option(CSV_PATH, help="Where to write the raw entries CSV."),
    db_path: Path = typer.Option(DB_PATH, help="DuckDB database to load the CSV into."),
) -> None:
    """Extract every diary PDF in a directory into a CSV and a DuckDB table."""
    pdf_paths = sorted(input_dir.glob("*.pdf"))
    if not pdf_paths:
        raise SystemExit(f"no PDFs found in {input_dir}")

    entries = []
    for pdf_path in pdf_paths:
        pdf_entries = extract_pdf(pdf_path)
        print(f"{pdf_path.name}: {len(pdf_entries)} entries")
        entries.extend(pdf_entries)

    write_csv(entries, csv_path)
    load_duckdb(csv_path, db_path)
    print(
        f"\n{len(entries)} total entries -> {csv_path} and {db_path} ({RAW_TABLE_NAME})"
    )


@app.command("discover-ministerial-diaries")
def discover_ministerial_diaries_cmd(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Crawl and print a summary; write nothing."
    ),
    db_path: Path = typer.Option(DB_PATH, help="DuckDB database to merge into."),
) -> None:
    """Discover every reachable diary PDF link and reconcile it into ref_ministerial_diaries."""
    pdf_urls = discover_ministerial_diaries()
    if not pdf_urls:
        raise SystemExit("no diary PDF URLs discovered from cabinet.qld.gov.au")

    summary = diary_diff(pdf_urls, db_path, table_name=MINISTERIAL_DIARIES_TABLE_NAME)

    if dry_run:
        print(f"{len(pdf_urls)} diary PDF URLs discovered (dry run, nothing written)")
        print(f"  {summary}")
        return

    merge_ministerial_diaries(
        pdf_urls, db_path, table_name=MINISTERIAL_DIARIES_TABLE_NAME
    )
    print(
        f"{len(pdf_urls)} diary PDF URLs -> {db_path} ({MINISTERIAL_DIARIES_TABLE_NAME})"
    )
    print(f"  {summary}")


if __name__ == "__main__":
    app()
