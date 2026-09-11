# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Doorstop extracts Queensland ministerial diaries (published as FOI PDFs) into structured, queryable data. Each diary PDF lists a minister's meetings for a reporting period as a three-column table (Date of Meeting / Name of Organisation(s) or Person(s) / Purpose of Meeting), preceded by a title block giving portfolio, minister name, and reporting period.

## Commands

Uses `uv` with a `.venv` already present (Python 3.13, per `.python-version`).

The CLI is built with [Typer](https://typer.tiangolo.com/) (`doorstop/cli.py`, `app = typer.Typer()`), exposed via `doorstop/__main__.py` so it runs as `python -m doorstop`, and also installable as a `doorstop` script via the `[project.scripts]` entry in `pyproject.toml`.

```bash
# Run the full pipeline: PDFs in a directory -> CSV -> DuckDB table
python -m doorstop extract [input_dir]   # defaults to data/samples

# Scrape the QLD Cabinet minister/portfolio directory into a reference table
python -m doorstop scrape-refs

# See all commands/options
python -m doorstop --help
```

`extract` glob-matches `*.pdf` in the input directory, extracts every entry from each, writes `data/processed/raw_diary_entries.csv` (override with `--csv-path`), and loads that CSV into `data/doorstop.duckdb` (table `raw_diary_entries`, override with `--db-path`) via DuckDB's `read_csv_auto`.

There is no test suite, linter, or formatter configured yet.

## Architecture

Two-stage pipeline, each stage in its own module under `doorstop/`:

- **`extract.py`** — parses one diary PDF into a list of `DiaryEntry` dataclass rows. This is the core of the project and the thing most likely to need changes when a new diary format shows up. pdfplumber's built-in table extractor doesn't work on these documents (column count detected varies per page, and attendee lists that wrap across a page break get split into unrelated rows), so extraction instead pulls words with x/y coordinates via `extract_words()` and rebuilds rows manually:
  - `_lines_from_words` groups words into visual lines by vertical (`top`) position.
  - `_find_header_bounds` locates the "Date / Name / Purpose" header row on page 1 and derives the two x-coordinate boundaries between columns from the header text itself (column positions shift slightly between diaries depending on portfolio title length — don't hardcode x-coordinates).
  - `_bucket_line` assigns each word on a line to date/name/purpose by x-position against those boundaries.
  - `_extract_header_metadata` pulls minister name, portfolio, and reporting period from the title block above the header row.
  - The main loop in `extract_pdf` walks lines across all pages; a line matching `DATE_RE` starts a new entry, and any non-dated line that follows is treated as a continuation (wrapped attendee list or purpose text) of the current entry — this continuation can span a page break. `BOILERPLATE_RE` filters out the footnote/lobbyist-notice boilerplate that appears inside the table's vertical span on the first and last pages.
  - Everything on `DiaryEntry` is a `_raw` field: this stage does no cleaning, typing, or normalization by design.

- **`load.py`** — persists the raw entries as `data/processed/raw_diary_entries.csv` (one row per meeting, untouched from extraction) and loads that same CSV into DuckDB. The CSV is treated as the raw source layer; DuckDB just makes it SQL-queryable. This is intended to eventually be the source database for a dbt-duckdb project — keep the CSV/DuckDB raw layer unmodified by downstream logic rather than mutating it in place.

- **`ministers.py`** — scrapes cabinet.qld.gov.au's minister/portfolio directory into a `MinisterRecord` reference table (name, term, role, and the page URL listing that minister's diary PDFs). Portfolio titles aren't published there — they only exist inside each diary PDF's title block, which `extract.py` already parses — so this is purely a name/URL reference, and `page_url` is also the seed a future task would need to bulk-download diary PDFs per minister.

- **`cli.py`** — Typer app wiring the two pipelines together: `extract` (`extract.py` -> `load.py` for a directory of PDFs) and `scrape-refs` (`ministers.py` -> `load.py`).

Sample source PDFs live in `data/samples/`, named `<portfolio>_<year>-<month>.pdf`.
