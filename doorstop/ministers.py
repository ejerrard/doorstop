"""Scrapes the QLD Cabinet minister/portfolio directory into a reference
table of minister names and their diary-listing page URLs.

cabinet.qld.gov.au/ministers-portfolios.aspx does not publish portfolio
titles anywhere (checked both the main list and individual minister pages)
- portfolio only exists inside each diary PDF's own title block, which
extract.py already parses. What this page does have, entirely inline in its
sidebar navigation tree (#navtree), is every current and former minister
back to 2013, each linking to a page listing their monthly diary PDFs. That
page_url is raw/uncleaned like everything else in this pipeline, and is also
the seed a future task would need to bulk-download diary PDFs per minister.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://cabinet.qld.gov.au/ministers-portfolios.aspx"
SITE_ROOT = "https://cabinet.qld.gov.au"

TERM_RE = re.compile(r"\((\d{4})[–-](\d{4})\)")
FORMER_MINISTERS_LINK_RE = re.compile(r"^Diaries of Former Ministers", re.IGNORECASE)


def _is_minister_page_href(href: str) -> bool:
    """A real minister page is an .aspx page on this site - historical term
    lists occasionally link straight to a PDF (on-site or on an external
    domain like parliament.qld.gov.au) instead, which isn't a page we can
    treat as "this minister's diary listing"."""
    if not href.lower().endswith(".aspx"):
        return False
    return not href.startswith("http") or href.startswith(SITE_ROOT)


@dataclass
class MinisterRecord:
    term: str
    role: str
    name_raw: str
    page_url: str

    def to_dict(self) -> dict:
        return asdict(self)


def fetch_soup(url: str = BASE_URL) -> BeautifulSoup:
    response = httpx.get(url, follow_redirects=True, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "lxml")


def parse_navtree(soup: BeautifulSoup) -> list[MinisterRecord]:
    navtree = soup.find("ul", id="navtree")
    if navtree is None:
        raise RuntimeError("could not find #navtree in cabinet.qld.gov.au page markup")
    records: list[MinisterRecord] = []

    for li in navtree.find_all("li", recursive=False):
        anchor = li.a
        if anchor is None or "nav-home" in li.get("class", []):
            continue

        nested_ul = li.find("ul", recursive=False)
        if nested_ul is None:
            records.append(
                MinisterRecord(
                    term="current",
                    role="Minister",
                    name_raw=anchor.get_text(strip=True),
                    page_url=urljoin(SITE_ROOT, anchor["href"]),
                )
            )
            continue

        heading = anchor.get_text(strip=True)
        term_match = TERM_RE.search(heading)
        if term_match:
            term = f"{term_match.group(1)}-{term_match.group(2)}"
            role = "Minister"
        elif heading == "Assistant Ministers":
            term = "current"
            role = "Assistant Minister"
        else:
            continue

        for nested_li in nested_ul.find_all("li", recursive=False):
            nested_anchor = nested_li.a
            if nested_anchor is None:
                continue
            name_raw = nested_anchor.get_text(strip=True)
            if FORMER_MINISTERS_LINK_RE.match(name_raw):
                continue
            if not _is_minister_page_href(nested_anchor["href"]):
                continue
            records.append(
                MinisterRecord(
                    term=term,
                    role=role,
                    name_raw=name_raw,
                    page_url=urljoin(SITE_ROOT, nested_anchor["href"]),
                )
            )

    return records


def scrape_all() -> list[MinisterRecord]:
    return parse_navtree(fetch_soup())
