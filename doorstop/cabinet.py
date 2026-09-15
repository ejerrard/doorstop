"""Discovers every diary PDF URL reachable from the QLD Cabinet
minister/portfolio directory (cabinet.qld.gov.au/ministers-portfolios.aspx).

This module has no persisted concept of "minister" as an entity - only of
the PDF links their pages expose. The navtree is walked purely as a means to
find every minister page (current, historical, and reshuffled-out via each
term's "Diaries of Former Ministers" follow-up page), each of those pages is
fetched, and every diary PDF link on it is collected. Minister name, term,
and role are never recorded; that would be a separate reference table with
its own audited grain, and this project deliberately doesn't build one -
those facts, along with portfolio, live inside each diary PDF's own title
block and are parsed later by extract.py.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

BASE_URL = "https://cabinet.qld.gov.au/ministers-portfolios.aspx"
SITE_ROOT = "https://cabinet.qld.gov.au"
USER_AGENT = "doorstop-diary-discovery/1.0 (+https://github.com/)"
REQUEST_DELAY_SECONDS = 0.2

FORMER_MINISTERS_LINK_RE = re.compile(r"^Diaries of Former Ministers", re.IGNORECASE)


def _is_minister_page_href(href: str) -> bool:
    """A real minister page is an .aspx page on this site - historical term
    lists occasionally link straight to a PDF (on-site or on an external
    domain like parliament.qld.gov.au) instead, which isn't a page we can
    treat as "this minister's diary listing"."""
    if not href.lower().endswith(".aspx"):
        return False
    return not href.startswith("http") or href.startswith(SITE_ROOT)


def fetch_soup(url: str = BASE_URL) -> BeautifulSoup:
    response = httpx.get(url, follow_redirects=True, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "lxml")


def _find_former_minister_page_urls(url: str) -> list[str]:
    """Parses a "Diaries of Former Ministers" sub-page: a flat list of
    ministers who left partway through this term and so don't have a
    top-level slot in the main navtree. This is a genuinely separate fetch -
    the page's content isn't nested inside the main navtree response, it
    only exists at this URL."""
    soup = fetch_soup(url)
    urls: list[str] = []
    for li in soup.find_all("li", class_="list-group-item"):
        anchor = li.a
        if anchor is None or not anchor.has_attr("href"):
            continue
        href = anchor["href"]
        if _is_minister_page_href(href):
            urls.append(urljoin(url, href))
    return urls


def _find_minister_page_urls(ul: Tag) -> list[str]:
    """Recurses through the navtree's <ul>/<li> structure, returning every
    minister page URL reachable from it - current, historical, and via each
    term's "Diaries of Former Ministers" follow-up page. Pure page
    discovery: no minister facts (name/term/role) are recorded, only the
    URLs of pages that might list diary PDFs."""
    urls: list[str] = []

    for li in ul.find_all("li", recursive=False):
        anchor = li.a
        if anchor is None or "nav-home" in li.get("class", []):
            continue

        href = anchor["href"]
        name_raw = anchor.get_text(strip=True)
        nested_ul = li.find("ul", recursive=False)

        if nested_ul is None:
            if FORMER_MINISTERS_LINK_RE.match(name_raw):
                urls.extend(_find_former_minister_page_urls(urljoin(SITE_ROOT, href)))
            elif _is_minister_page_href(href):
                urls.append(urljoin(SITE_ROOT, href))
            continue

        urls.extend(_find_minister_page_urls(nested_ul))

    return urls


def _find_diary_pdf_urls(page_url: str, soup: BeautifulSoup) -> list[str]:
    """Diary PDF links on a minister page are scoped to <li
    class="list-group-item"> - the same convention already used for the
    "Diaries of Former Ministers" sub-page - which reliably excludes the
    page's other PDF link (a "Ministerial Charter Letter", which lives in a
    <p>, not a list-group-item).

    The `?v=...` query string on these links is a per-request cache-busting
    token, not a stable content version - two fetches of the same page
    return different values for the same file - so it's stripped before the
    URL becomes this table's natural key, or every run would show spurious
    churn (confirmed live: refetching one page a second apart changed
    `?v=`)."""
    urls: list[str] = []
    for li in soup.find_all("li", class_="list-group-item"):
        anchor = li.a
        if anchor is None or not anchor.has_attr("href"):
            continue
        href = anchor["href"].split("?")[0]
        if href.lower().endswith(".pdf"):
            urls.append(urljoin(page_url, href))
    return urls


def discover_ministerial_diaries() -> list[str]:
    """Walks the navtree, visits every minister page reachable from it, and
    returns every diary PDF URL found (deduplicated). Minister identity is
    discovered only as a means of finding these pages; none of it is
    recorded anywhere - the returned URLs are the sole output. A full crawl
    issues one request for the main page, one per minister page found, plus
    one per historical term that carries a former-ministers link (currently
    one, for 2020-2024)."""
    soup = fetch_soup()
    navtree = soup.find("ul", id="navtree")
    if navtree is None:
        raise RuntimeError("could not find #navtree in cabinet.qld.gov.au page markup")
    minister_page_urls = _find_minister_page_urls(navtree)

    pdf_urls: list[str] = []
    failed_pages: list[str] = []
    with httpx.Client(follow_redirects=True, timeout=30, headers={"User-Agent": USER_AGENT}) as client:
        for page_url in minister_page_urls:
            try:
                response = client.get(page_url)
                response.raise_for_status()
            except httpx.HTTPError:
                failed_pages.append(page_url)
                continue
            pdf_urls.extend(_find_diary_pdf_urls(page_url, BeautifulSoup(response.text, "lxml")))
            time.sleep(REQUEST_DELAY_SECONDS)

    if failed_pages:
        print(f"warning: failed to fetch {len(failed_pages)} minister page(s), skipped: {failed_pages}")

    return sorted(set(pdf_urls))
