# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Doorstop extracts Queensland ministerial diaries (published as FOI PDFs) into structured, queryable data. Each diary PDF lists a minister's meetings for a reporting period as a three-column table (Date of Meeting / Name of Organisation(s) or Person(s) / Purpose of Meeting), preceded by a title block giving portfolio, minister name, and reporting period.

## Commands

Uses `uv` with a `.venv` already present (Python 3.13, per `.python-version`).

The CLI is built with [Typer](https://typer.tiangolo.com/) (`doorstop/cli.py`, `app = typer.Typer()`), exposed via `doorstop/__main__.py` so it runs as `python -m doorstop`, and also installable as a `doorstop` script via the `[project.scripts]` entry in `pyproject.toml`.

```bash
# Discover every reachable diary PDF URL and reconcile into DuckDB
python -m doorstop discover-ministerial-diaries   # add --dry-run

# Download every still-listed diary PDF and verify what's being served
python -m doorstop download-ministerial-diaries   # add --dry-run

# Run the full pipeline: PDFs in a directory -> CSV -> DuckDB table
python -m doorstop extract [input_dir]   # defaults to data/samples
python -m doorstop extract --from-downloads   # sources PDFs from ref_diary_pdf_snapshots instead

# See all commands/options
python -m doorstop --help
```

`discover-ministerial-diaries` crawls cabinet.qld.gov.au's minister navtree (following any
"Diaries of Former Ministers" sub-pages it links to) purely to find every minister page,
then visits each of those pages to collect its diary PDF links, and reconciles the result
into `data/doorstop.duckdb` (table `ref_ministerial_diaries`, override with `--db-path`).
This is a Type 2 merge, not a wipe-and-rebuild: a URL found again gets `last_seen` bumped, a
URL not found this run gets `still_listed` flipped to `false` (never deleted), and a URL
with no currently-open row gets a brand-new row — including one reappearing after a gap,
which opens a *second* row rather than reviving the first, so the gap stays visible.
`--dry-run` crawls and prints the same new/extended/newly-delisted/reappeared summary
without writing anything.

`download-ministerial-diaries` reads the currently `still_listed` URLs out of
`ref_ministerial_diaries` (not a fresh crawl — discovery already owns "is this URL still
published"), downloads each one, and rejects anything whose response body doesn't start
with the PDF magic bytes (`%PDF-`) before it's hashed or written anywhere — a defensive
file-type check only, not content validation, which stays `extract`'s job. Accepted PDFs
are stored content-addressed under `data/raw_pdfs/<sha256[:2]>/<sha256>.pdf` (override
with `--out-dir`) and reconciled into `data/doorstop.duckdb` (table
`ref_diary_pdf_snapshots`, override with `--db-path`) as a Type 2 merge keyed on
`(pdf_url, content_hash)`: a hash matching the URL's current row gets `last_seen`
bumped, a *different* hash for a previously-current row closes that row
(`still_current = false`) and opens a new one — so a URL serving different bytes over
time is a queryable fact, not a silent overwrite. Failed or rejected downloads are never
persisted, only reported as a `failed` count and warning. `--dry-run` still performs the
real downloads (so the new/unchanged/content_changed preview is accurate) but skips the
disk write and DB merge.

`extract` glob-matches `*.pdf` in `input_dir` (default `data/samples`), or, with `--from-downloads`, reads distinct `storage_path` values from `still_current` rows of `ref_diary_pdf_snapshots` instead — decoupling extraction from the download stage's own crawl the same way `still_listed_diary_urls` decouples download from discovery. Each PDF is extracted independently with a try/except-collect-continue loop (mirroring `download_ministerial_diaries`'s `(downloads, failed)` shape): a PDF that fails to parse is skipped and counted, never aborting the whole run. Remaining entries are written to `data/processed/raw_diary_entries.csv` (override with `--csv-path`) and loaded into `data/doorstop.duckdb` (table `raw_diary_entries`, override with `--db-path`) via DuckDB's `read_csv_auto`. Note: `storage_path` is persisted relative to whatever cwd `download-ministerial-diaries` ran from (the default `data/raw_pdfs` is a relative path) — both commands are documented as run via `python -m doorstop <cmd>` from the repo root.

There is no test suite, linter, or formatter configured yet.

## Pipeline stages

| # | CLI command | Module | Input | Output |
|---|---|---|---|---|
| 1 | `discover-ministerial-diaries` | `cabinet.py` | cabinet.qld.gov.au minister navtree | `ref_ministerial_diaries` (DuckDB only) |
| 2 | `download-ministerial-diaries` | `download.py` | `ref_ministerial_diaries.pdf_url` (`still_listed`) | `ref_diary_pdf_snapshots` (DuckDB) + downloaded PDFs under `data/raw_pdfs/` |
| 3 | `extract` | `extract.py` + `load.py` | a directory of PDFs, or (`--from-downloads`) `ref_diary_pdf_snapshots.storage_path` (`still_current`) | `raw_diary_entries` (CSV + DuckDB) |

Naming convention for discovery stages: `discover_<noun>()` function, `discover-<noun>` CLI
command, `ref_<noun>` DuckDB table. "Discover" names the intent (what exists); "scrape" is
an implementation detail that stays internal to a module, never a public name.

## Architecture

Pipeline stages, each in its own module under `doorstop/`:

- **`extract.py`** — parses one diary PDF into a list of `DiaryEntry` dataclass rows. This is the core of the project and the thing most likely to need changes when a new diary format shows up. pdfplumber's built-in table extractor doesn't work on these documents (column count detected varies per page, and attendee lists that wrap across a page break get split into unrelated rows), so extraction instead pulls words with x/y coordinates via `extract_words()` and rebuilds rows manually:
  - `_lines_from_words` groups words into visual lines by vertical (`top`) position.
  - `_find_header_bounds` locates the "Date / Name / Purpose" header row on page 1 and derives the two x-coordinate boundaries between columns from the header text itself (column positions shift slightly between diaries depending on portfolio title length — don't hardcode x-coordinates).
  - `_bucket_line` assigns each word on a line to date/name/purpose by x-position against those boundaries.
  - `_extract_header_metadata` pulls minister name, portfolio, and reporting period from the title block above the header row.
  - The main loop in `extract_pdf` walks lines across all pages; a line matching `DATE_RE` starts a new entry, and any non-dated line that follows is treated as a continuation (wrapped attendee list or purpose text) of the current entry — this continuation can span a page break. `BOILERPLATE_RE` filters out the footnote/lobbyist-notice boilerplate that appears inside the table's vertical span on the first and last pages.
  - Everything on `DiaryEntry` is a `_raw` field: this stage does no cleaning, typing, or normalization by design.

- **`load.py`** — the sole persistence layer; neither `cabinet.py`, `download.py`, nor `extract.py` imports `duckdb`. Three independent persistence shapes, used by different stages depending on volume and purpose. `write_csv`/`load_duckdb` persist a raw CSV layer (used by `extract`'s `raw_diary_entries`) intended to eventually double as the source database for a dbt-duckdb project — keep that CSV/DuckDB layer unmodified by downstream logic rather than mutating it in place. `diary_diff`/`merge_ministerial_diaries` maintain `ref_ministerial_diaries` as a Type 2 slowly-changing table (used by `discover-ministerial-diaries`) — read-only diffing is split out from the write path (`diary_diff`) specifically so `--dry-run` can print the same new/extended/newly-delisted/reappeared counts without touching the table; `merge_ministerial_diaries` then does the actual `UPDATE`/`UPDATE`/`INSERT` sequence (extend rows found again, close rows not found, insert brand-new-or-reappeared rows) against a plain `CREATE TABLE IF NOT EXISTS`, not a wipe-and-rebuild. `still_listed_diary_urls` reads that table back out (the `still_listed` rows only) as the download stage's input, decoupling it from `ref_ministerial_diaries`'s own crawl. `pdf_diff`/`merge_pdf_snapshots` follow the same read/write split one grain deeper, maintaining `ref_diary_pdf_snapshots` as a Type 2 table keyed on `(pdf_url, content_hash)` rather than just `pdf_url` — a URL whose served content changes closes its previously-`still_current` row and opens a new one, exactly mirroring the reappearance-opens-a-new-row philosophy of `merge_ministerial_diaries`, just at content-snapshot granularity. `still_current_pdf_paths` mirrors `still_listed_diary_urls` one stage further down the pipeline, reading distinct `storage_path` values back out of `still_current` rows as `extract`'s optional `--from-downloads` input — DISTINCT because two different `pdf_url`s have, at least once, served byte-identical content, and extracting the same file twice would double-count its entries.

- **`cabinet.py`** — crawls cabinet.qld.gov.au's minister/portfolio directory (`discover_ministerial_diaries`) purely to find every diary PDF URL reachable from it; it has no persisted concept of "minister" as an entity, only of the PDF links their pages expose (named for the site it navigates, not for what it discovers — a prior version of this module was called `ministers.py`, which became misleading once minister/term/role stopped being persisted at all). The navtree walk (`_find_minister_page_urls`) recurses to find every minister page — current, historical, and reshuffled-out mid-term via each term's "Diaries of Former Ministers" follow-up page (`_find_former_minister_page_urls`, scoped to `<li class="list-group-item">` — the page also renders the full sitewide nav sidebar and a breadcrumb, which must not be swept up as if they were ministers). That follow-up page is a genuinely separate fetch, not content already nested in the main page's response — only one such link exists on the site today (2020–2024), but the crawl follows one generically for any term, current included, in case QLD adds another later. Each minister page found is then fetched and its diary PDF links collected (`_find_diary_pdf_urls`, also scoped to `<li class="list-group-item">`, which is what excludes the page's other PDF link — a "Ministerial Charter Letter" living in a `<p>`, not a diary). Minister name, term, role, and portfolio are never recorded here; portfolio in particular is published nowhere on this site outside each diary PDF's own title block, which `extract.py` already parses — a prior design that persisted minister/term/role as their own reference table was tried and abandoned, since that table's job (visiting minister pages) is better done as an internal, unpersisted traversal step inside this stage rather than as its own audited entity.

- **`download.py`** — downloads every URL `load.py`'s `still_listed_diary_urls` reads back out of `ref_ministerial_diaries`, mirroring `cabinet.py`'s shape (one reused `httpx.Client`, per-URL try/except-collect-continue, flat rate limit, no `duckdb` import). `fetch_pdf` rejects a response (raises `ValueError`) unless its body starts with the PDF magic bytes (`%PDF-`), checked before anything is hashed or written to disk — deliberately a file-*type* check only, not a content check, since validating a PDF's actual structure is `extract.py`'s job. `download_ministerial_diaries` returns `(downloads, failed)`: `failed` maps url -> reason and is never persisted, only reported as a warning + count, matching how `cabinet.py` handles per-page fetch failures. Passing `out_dir=None` hashes without writing to disk — this is what powers `--dry-run` (the download itself still happens over the network; only the disk write and DB merge are skipped).

- **`cli.py`** — Typer app wiring the pipelines together: `discover-ministerial-diaries` (`cabinet.py` -> `load.py`), `download-ministerial-diaries` (`load.py`'s `still_listed_diary_urls` -> `download.py` -> `load.py`'s `pdf_diff`/`merge_pdf_snapshots`), and `extract` (`extract.py` -> `load.py`, sourcing PDF paths either from a directory glob or, with `--from-downloads`, from `load.py`'s `still_current_pdf_paths`). Unlike `discover`/`download`, `extract`'s per-PDF loop follows the same try/except-collect-continue-plus-warning pattern as `download.py`'s per-URL loop, added specifically so `--from-downloads` can run against thousands of real-world PDFs spanning years of format drift without one bad file aborting the batch — a failure here stays a `cli.py`-level concern, not something pushed down into `extract.py`, which stays duckdb-free and only ever parses one given path.

Sample source PDFs live in `data/samples/`, named `<portfolio>_<year>-<month>.pdf`.
