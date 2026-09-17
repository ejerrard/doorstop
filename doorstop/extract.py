"""Parses Queensland ministerial diary PDFs into raw diary entry rows.

The diaries share one template: a title block (portfolio, minister name,
reporting period) followed by a three-column table (Date of Meeting / Name
of Organisation/s or Person/s / Purpose of Meeting). pdfplumber's table
extractor doesn't cope with these documents — the column count it detects
varies per page, and entries whose attendee list wraps across a page break
get split into unrelated table rows. So instead we extract words with their
x/y coordinates and rebuild rows ourselves.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import pdfplumber

DATE_RE = re.compile(r"^\d{1,2} [A-Za-z]+ \d{4}$")
RANGE_RE = re.compile(r"(\d{1,2} [A-Za-z]+ \d{4})\s*[–-]\s*(\d{1,2} [A-Za-z]+ \d{4})")

# Boilerplate that appears on the first page (footnote) and last page (lobbyist
# notice) of every diary. It sits inside the table's vertical span, so without
# this filter it gets swept up as a continuation of the last real row.
BOILERPLATE_RE = re.compile(
    r"Does not include personal|Registered lobbyists|Integrity Commissioner"
    r"|lobbyists\.integrity\.qld\.gov\.au"
)


@dataclass
class DiaryEntry:
    source_file: str
    row_index: int
    page_start: int
    minister_name: str
    portfolio: str
    period_start_raw: str
    period_end_raw: str
    meeting_date_raw: str
    attendees_raw: str
    purpose_raw: str

    def to_dict(self) -> dict:
        return asdict(self)


def _lines_from_words(words: list[dict], tol: float = 2.5) -> list[list[dict]]:
    """Groups words into visual lines by vertical position."""
    lines: dict[float, list[dict]] = defaultdict(list)
    current_top = None
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if current_top is None or abs(w["top"] - current_top) > tol:
            current_top = w["top"]
        lines[round(current_top, 1)].append(w)
    return [lines[top] for top in sorted(lines)]


def _find_header_bounds(page0) -> tuple[float, float, float]:
    """Locates the table header row and the x-boundaries between its three
    columns, using the header's own text rather than fixed coordinates —
    column positions shift slightly between diaries depending on portfolio
    title length."""
    for line in _lines_from_words(page0.extract_words()):
        texts = [w["text"] for w in line]
        if texts[:1] == ["Date"] and "Purpose" in texts:
            idx_name = texts.index("Name")
            idx_purpose = texts.index("Purpose")
            date_group, name_group, purpose_group = (
                line[:idx_name],
                line[idx_name:idx_purpose],
                line[idx_purpose:],
            )
            b1 = (max(w["x1"] for w in date_group) + min(w["x0"] for w in name_group)) / 2
            b2 = (max(w["x1"] for w in name_group) + min(w["x0"] for w in purpose_group)) / 2
            return line[0]["top"], b1, b2
    raise ValueError("could not find table header row")


def _bucket_line(line: list[dict], b1: float, b2: float) -> tuple[str, str, str]:
    date_words, name_words, purpose_words = [], [], []
    for w in line:
        bucket = date_words if w["x0"] < b1 else name_words if w["x0"] < b2 else purpose_words
        bucket.append(w)
    join = lambda ws: " ".join(w["text"] for w in ws).strip()
    return join(date_words), join(name_words), join(purpose_words)


def _extract_header_metadata(page0, header_top: float) -> dict:
    words = page0.extract_words()
    title_lines = [l for l in _lines_from_words(words) if l[0]["top"] < header_top - 1]
    texts = [" ".join(w["text"] for w in l) for l in title_lines]
    # First line is the "Ministerial Diary" title; a lone "1" is the
    # footnote marker rendered as its own line in some diaries.
    body = [t for t in texts[1:] if t.strip() != "1"]

    range_idx, match = next(
        (i, m) for i, t in enumerate(body) if (m := RANGE_RE.search(t))
    )

    return {
        "minister_name": body[range_idx - 1].replace("The Hon ", "").strip(),
        "portfolio": "; ".join(body[: range_idx - 1]),
        "period_start_raw": match.group(1),
        "period_end_raw": match.group(2),
    }


def extract_pdf(path: Path) -> list[DiaryEntry]:
    """Extracts every meeting entry from one ministerial diary PDF, in the
    order it appears in the document."""
    with pdfplumber.open(path) as pdf:
        header_top, b1, b2 = _find_header_bounds(pdf.pages[0])
        metadata = _extract_header_metadata(pdf.pages[0], header_top)

        rows: list[dict] = []
        current: dict | None = None
        for page_number, page in enumerate(pdf.pages, start=1):
            for line in _lines_from_words(page.extract_words()):
                if page_number == 1 and line[0]["top"] <= header_top + 1:
                    continue
                full_text = " ".join(w["text"] for w in line)
                if BOILERPLATE_RE.search(full_text):
                    continue

                date_text, name_text, purpose_text = _bucket_line(line, b1, b2)
                if not (date_text or name_text or purpose_text):
                    continue

                if DATE_RE.match(date_text):
                    if current is not None:
                        rows.append(current)
                    current = {
                        "meeting_date_raw": date_text,
                        "page_start": page_number,
                        "name_parts": [name_text] if name_text else [],
                        "purpose_parts": [purpose_text] if purpose_text else [],
                    }
                elif current is not None:
                    # A row with no date is the wrapped remainder of the
                    # previous entry's attendee list or purpose text —
                    # sometimes spanning a page break.
                    if name_text:
                        current["name_parts"].append(name_text)
                    if purpose_text:
                        current["purpose_parts"].append(purpose_text)
        if current is not None:
            rows.append(current)

    entries = []
    for row_index, row in enumerate(rows):
        entries.append(
            DiaryEntry(
                source_file=Path(path).name,
                row_index=row_index,
                page_start=row["page_start"],
                attendees_raw=" ".join(row["name_parts"]),
                purpose_raw=" ".join(row["purpose_parts"]),
                meeting_date_raw=row["meeting_date_raw"],
                **metadata,
            )
        )
    return entries
