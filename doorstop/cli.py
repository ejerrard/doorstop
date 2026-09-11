"""Entry points for the doorstop pipelines.

    python -m doorstop extract [input_dir]
    python -m doorstop scrape-refs
"""

from __future__ import annotations

from pathlib import Path

import typer

from doorstop.extract import extract_pdf
from doorstop.load import RAW_TABLE_NAME, load_duckdb, write_csv
from doorstop.ministers import scrape_all

DEFAULT_INPUT_DIR = Path("data/samples")
CSV_PATH = Path("data/processed/raw_diary_entries.csv")
DB_PATH = Path("data/doorstop.duckdb")
MINISTERS_CSV_PATH = Path("data/processed/ref_ministers.csv")
MINISTERS_TABLE_NAME = "ref_ministers"

app = typer.Typer()


@app.command()
def extract(
    input_dir: Path = typer.Argument(DEFAULT_INPUT_DIR, help="Directory of diary PDFs to extract."),
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
    print(f"\n{len(entries)} total entries -> {csv_path} and {db_path} ({RAW_TABLE_NAME})")


@app.command("scrape-refs")
def scrape_refs(
    csv_path: Path = typer.Option(MINISTERS_CSV_PATH, help="Where to write the minister reference CSV."),
    db_path: Path = typer.Option(DB_PATH, help="DuckDB database to load the CSV into."),
) -> None:
    """Scrape the QLD Cabinet minister/portfolio directory into a reference table."""
    records = scrape_all()
    if not records:
        raise SystemExit("no minister records scraped from cabinet.qld.gov.au")

    write_csv(records, csv_path)
    load_duckdb(csv_path, db_path, table_name=MINISTERS_TABLE_NAME)
    print(f"{len(records)} minister records -> {csv_path} and {db_path} ({MINISTERS_TABLE_NAME})")


if __name__ == "__main__":
    app()
