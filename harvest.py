from __future__ import annotations

import re
from collections import deque
from pathlib import Path

from rich.console import Console
from tqdm import tqdm

from .config import iter_seeds
from .models import ThesisItem
from .parse import handle_from_url, parse_collection_links, parse_item_page
from .utils import PoliteSession, normalise_url

console = Console()


def looks_like_item(html: str) -> bool:
    markers = [
        "dc.title",
        "dc.contributor.author",
        "dc.description.abstract",
        "Útdráttur",
        "Höfundur",
        "Leiðbeinandi",
    ]
    return any(marker in html for marker in markers)


def _extract_year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(19|20)\d{2}", value)
    return int(match.group(0)) if match else None


def harvest(config: dict, limit: int | None = None) -> list[ThesisItem]:
    base_url = config["base_url"].rstrip("/")
    session = PoliteSession(
        user_agent=config["user_agent"],
        delay_seconds=float(config.get("request_delay_seconds", 2.0)),
        timeout_seconds=int(config.get("timeout_seconds", 30)),
        cache_dir=Path("data/raw/html") if config.get("cache_html", True) else None,
    )

    results: list[ThesisItem] = []
    seen_urls: set[str] = set()

    year_min = config.get("year_min")
    year_max = config.get("year_max")

    seeds = iter_seeds(config)
    queue: deque[tuple[str, str, str]] = deque()
    for seed in seeds:
        url = f"{base_url}/handle/{seed.handle}"
        queue.append((url, seed.institution, seed.short_name))

    pbar = tqdm(total=limit, desc="Harvesting items", unit="item") if limit else None

    while queue and (limit is None or len(results) < limit):
        url, institution, short_name = queue.popleft()
        if url in seen_urls:
            continue
        seen_urls.add(url)

        try:
            html = session.get_text(url, use_cache=bool(config.get("cache_html", True)))
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Skipping {url}: {exc}[/yellow]")
            continue

        if looks_like_item(html):
            item = parse_item_page(
                html,
                item_url=url,
                base_url=base_url,
                institution=institution,
                institution_short=short_name,
            )
            year = _extract_year(item.date_accepted)
            if year_min and year is not None and year < int(year_min):
                continue
            if year_max and year is not None and year > int(year_max):
                continue
            results.append(item)
            if pbar:
                pbar.update(1)
            continue

        for link in parse_collection_links(html, base_url):
            handle = handle_from_url(link)
            if not handle:
                continue
            # Keep crawl inside Skemman handle space. More specific filtering can be added later.
            if link not in seen_urls:
                queue.append((normalise_url(base_url, link), institution, short_name))

    if pbar:
        pbar.close()

    return results
