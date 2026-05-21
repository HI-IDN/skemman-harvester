from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .models import ThesisItem
from .utils import normalise_url

HANDLE_RE = re.compile(r"/handle/([^?#]+)")


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def handle_from_url(url: str) -> str | None:
    match = HANDLE_RE.search(urlparse(url).path)
    return match.group(1) if match else None


def parse_collection_links(html: str, base_url: str) -> list[str]:
    """Return collection/item links from a Skemman/DSpace-like listing page."""
    soup = BeautifulSoup(html, "html.parser")
    links: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/handle/" in href:
            links.add(normalise_url(base_url, href))
    return sorted(links)


def parse_next_page(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        label = clean_text(a.get_text(" "))
        if label and label.lower() in {"next", "næsta", ">"}:
            return normalise_url(base_url, a["href"])
    rel_next = soup.find("a", rel=lambda value: value and "next" in value)
    if rel_next and rel_next.get("href"):
        return normalise_url(base_url, rel_next["href"])
    return None


def _metadata_from_tables(soup: BeautifulSoup) -> dict[str, list[str]]:
    metadata: dict[str, list[str]] = {}

    # DSpace item pages often store metadata in simple tables or definition lists.
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) >= 2:
            key = clean_text(cells[0].get_text(" "))
            val = clean_text(cells[1].get_text(" "))
            if key and val:
                metadata.setdefault(key.rstrip(":"), []).append(val)

    for dt in soup.find_all("dt"):
        key = clean_text(dt.get_text(" "))
        dd = dt.find_next_sibling("dd")
        val = clean_text(dd.get_text(" ")) if dd else None
        if key and val:
            metadata.setdefault(key.rstrip(":"), []).append(val)

    # Generic fallback: meta tags.
    for meta in soup.find_all("meta"):
        key = meta.get("name") or meta.get("property")
        val = meta.get("content")
        if key and val:
            metadata.setdefault(key, []).append(clean_text(val) or val)

    return metadata


def _first(metadata: dict[str, list[str]], *keys: str) -> str | None:
    lowered = {k.lower(): v for k, v in metadata.items()}
    for key in keys:
        vals = lowered.get(key.lower())
        if vals:
            return vals[0]
    return None


def _all(metadata: dict[str, list[str]], *keys: str) -> list[str]:
    lowered = {k.lower(): v for k, v in metadata.items()}
    out: list[str] = []
    for key in keys:
        out.extend(lowered.get(key.lower(), []))
    return out


def parse_item_page(
        html: str,
        item_url: str,
        base_url: str,
        institution: str | None = None,
        institution_short: str | None = None,
) -> ThesisItem:
    soup = BeautifulSoup(html, "html.parser")
    metadata = _metadata_from_tables(soup)

    h1 = soup.find(["h1", "h2"])
    title = clean_text(h1.get_text(" ")) if h1 else None
    title = title or _first(metadata, "dc.title", "Title", "Titill")

    abstract = _first(
        metadata,
        "dc.description.abstract",
        "Abstract",
        "Útdráttur",
        "Lýsing",
        "Description",
    )

    file_urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = clean_text(a.get_text(" ")) or ""
        if ".pdf" in href.lower() or "bitstream" in href.lower() or text.lower().endswith(".pdf"):
            file_urls.append(normalise_url(base_url, href))

    authors = _all(metadata, "dc.contributor.author", "Author", "Höfundur", "Höfundar")
    advisors = _all(metadata, "dc.contributor.advisor", "Advisor", "Leiðbeinandi", "Leiðbeinendur")
    keywords = _all(metadata, "dc.subject", "Subject", "Keywords", "Efnisorð")

    return ThesisItem(
        item_url=item_url,
        handle=handle_from_url(item_url),
        institution=institution,
        institution_short=institution_short,
        title=title,
        authors=authors,
        advisors=advisors,
        date_accepted=_first(metadata, "dc.date.issued", "dc.date.accepted", "Date", "Dagsetning"),
        abstract=abstract,
        keywords=keywords,
        degree=_first(metadata, "dc.description.degree", "Degree", "Prófgráða"),
        department=_first(metadata, "dc.contributor.department", "Department", "Deild"),
        file_urls=sorted(set(file_urls)),
        language=_first(metadata, "dc.language.iso", "Language", "Tungumál"),
        raw_metadata=metadata,
    )


def parse_simple_search_rows(html: str, base_url: str) -> list[dict[str, str | None]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, str | None]] = []

    for tr in soup.find_all("tr"):
        link = tr.find("a", href=True)
        if not link or "/handle/" not in link["href"]:
            continue
        cells = tr.find_all(["th", "td"])
        if len(cells) < 3:
            continue
        date_text = clean_text(cells[0].get_text(" "))
        title_text = clean_text(link.get_text(" "))
        author_text = clean_text(cells[2].get_text(" "))
        item_url = normalise_url(base_url, link["href"])
        rows.append(
            {
                "date_accepted": date_text,
                "title": title_text,
                "authors": author_text,
                "item_url": item_url,
            }
        )

    if rows:
        return rows

    for row in soup.select(".ds-artifact-item"):
        title_link = row.find("a", href=True)
        if not title_link or "/handle/" not in title_link["href"]:
            continue
        item_url = normalise_url(base_url, title_link["href"])
        title_text = clean_text(title_link.get_text(" "))
        meta = row.get_text(" ")
        rows.append(
            {
                "date_accepted": clean_text(meta),
                "title": title_text,
                "authors": None,
                "item_url": item_url,
            }
        )

    return rows
