from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

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


def parse_next_page(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        label = clean_text(a.get_text(" "))
        if label and label.lower() in {"next", "naesta", "næsta", ">"}:
            return normalise_url(base_url, a["href"])
    rel_next = soup.find("a", rel=lambda value: value and "next" in value)
    if rel_next and rel_next.get("href"):
        return normalise_url(base_url, rel_next["href"])
    return None


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
        rows.append(
            {
                "date_accepted": clean_text(cells[0].get_text(" ")),
                "title": clean_text(link.get_text(" ")),
                "authors": clean_text(cells[2].get_text(" ")),
                "item_url": normalise_url(base_url, link["href"]),
            }
        )

    return rows
