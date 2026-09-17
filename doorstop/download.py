"""Downloads every still-listed diary PDF URL from ref_ministerial_diaries and
verifies what each one actually serves.

URLs are sourced from the persisted ref_ministerial_diaries table rather than a
fresh crawl - discovery already owns "is this URL still published," so a
delisted URL isn't retried here, and a URL reappearing after a gap (which
discovery reopens as a new still_listed row) is simply picked up on this
stage's next run.

Each response is rejected unless its body starts with the PDF magic bytes,
checked before anything is hashed or written to disk - a non-PDF response
never enters the store. This is a defensive file-type check only: it does not
validate the PDF's internal structure or content, which remains extract.py's
job.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

USER_AGENT = "doorstop-diary-download/1.0 (+https://github.com/)"
REQUEST_DELAY_SECONDS = 0.2
PDF_MAGIC = b"%PDF-"


@dataclass
class PdfDownload:
    pdf_url: str
    content_hash: str
    storage_path: Path | None
    content_type: str | None
    content_length: int


def fetch_pdf(url: str, client: httpx.Client, out_dir: Path | None) -> PdfDownload:
    """Fetches url, rejecting it (raises ValueError) unless the response body
    starts with the PDF magic bytes. Content-Type is recorded as an
    informational field only - never used to accept/reject a response, since
    servers on this site are known to misreport it; the body itself is the
    only authoritative signal. When out_dir is None (dry run), the file is
    hashed but never written to disk."""
    response = client.get(url)
    response.raise_for_status()
    content = response.content
    if not content.startswith(PDF_MAGIC):
        raise ValueError(f"response is not a PDF (missing {PDF_MAGIC!r} signature)")

    content_hash = hashlib.sha256(content).hexdigest()
    storage_path: Path | None = None
    if out_dir is not None:
        storage_path = out_dir / content_hash[:2] / f"{content_hash}.pdf"
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        if not storage_path.exists():
            storage_path.write_bytes(content)

    return PdfDownload(
        pdf_url=url,
        content_hash=content_hash,
        storage_path=storage_path,
        content_type=response.headers.get("content-type"),
        content_length=len(content),
    )


def download_ministerial_diaries(
    pdf_urls: list[str], out_dir: Path | None
) -> tuple[list[PdfDownload], dict[str, str]]:
    """Downloads every pdf_url with one reused client, verifying each is a
    PDF before it's counted as a success. Returns (downloads, failed) where
    failed maps url -> reason (HTTP error or non-PDF rejection) for anything
    skipped; a run continues past individual failures rather than aborting,
    same as cabinet.py's page-fetch loop."""
    downloads: list[PdfDownload] = []
    failed: dict[str, str] = {}
    with httpx.Client(
        follow_redirects=True, timeout=30, headers={"User-Agent": USER_AGENT}
    ) as client:
        for url in pdf_urls:
            try:
                downloads.append(fetch_pdf(url, client, out_dir))
            except (httpx.HTTPError, ValueError) as exc:
                failed[url] = str(exc)
            time.sleep(REQUEST_DELAY_SECONDS)

    if failed:
        print(f"warning: failed to download {len(failed)} PDF(s), skipped: {failed}")

    return downloads, failed
