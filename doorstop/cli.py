"""Entry point: extracts every diary PDF in a directory into data/processed/
and data/doorstop.duckdb.

    python -m doorstop.cli [input_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

from doorstop.extract import extract_pdf
from doorstop.load import RAW_TABLE_NAME, load_duckdb, write_csv

DEFAULT_INPUT_DIR = Path("data/samples")
CSV_PATH = Path("data/processed/raw_diary_entries.csv")
DB_PATH = Path("data/doorstop.duckdb")


def main() -> None:
    input_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT_DIR
    pdf_paths = sorted(input_dir.glob("*.pdf"))
    if not pdf_paths:
        raise SystemExit(f"no PDFs found in {input_dir}")

    entries = []
    for pdf_path in pdf_paths:
        pdf_entries = extract_pdf(pdf_path)
        print(f"{pdf_path.name}: {len(pdf_entries)} entries")
        entries.extend(pdf_entries)

    write_csv(entries, CSV_PATH)
    load_duckdb(CSV_PATH, DB_PATH)
    print(f"\n{len(entries)} total entries -> {CSV_PATH} and {DB_PATH} ({RAW_TABLE_NAME})")


if __name__ == "__main__":
    main()
