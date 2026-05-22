from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any
from urllib.parse import urlencode

import pandas as pd
from tqdm import tqdm

from .parse import parse_next_page, parse_simple_search_rows
from .utils import PoliteSession


def _normalise_record(record: Any) -> dict[str, Any]:
    if is_dataclass(record):
        record = asdict(record)
    return dict(record)


def _normalize_location(location: str) -> str:
    value = location.strip()
    if "/" in value:
        return value
    if " " in value:
        return "/".join(part for part in value.split() if part)
    return value


def build_simple_search_url(
        base_url: str,
        location: str,
        year: int | None,
        rpp: int,
        start: int = 0,
) -> str:
    location = _normalize_location(location)
    params = {
        "location": location,
        "query": "",
        "rpp": str(rpp),
        "sort_by": "score",
        "order": "desc",
    }
    if start:
        params["start"] = str(start)
    if year is not None:
        params.update(
            {
                "filtername": "dateIssued",
                "filtertype": "equals",
                "filterquery": str(year),
                "doSearch": "Leita",
            }
        )
    query = urlencode(params)
    return f"{base_url.rstrip('/')}/simple-search?{query}"


def harvest_simple_search(
        config: dict,
        url: str | None,
        location: str | None,
        year: int | None,
        rpp: int,
        paginate: bool,
) -> pd.DataFrame:
    base_url = config["base_url"].rstrip("/")
    session = PoliteSession(
        user_agent=config["user_agent"],
        delay_seconds=float(config.get("request_delay_seconds", 2.0)),
        timeout_seconds=int(config.get("timeout_seconds", 30)),
        cache_dir=None,
    )

    start = 0
    if url:
        target_url = url
    elif location:
        target_url = build_simple_search_url(base_url, location, year, rpp, start=start)
    else:
        raise ValueError("Either url or location must be provided.")

    rows: list[dict[str, Any]] = []
    desc_parts = ["simple-search"]
    if location:
        desc_parts.append(_normalize_location(location))
    if year is not None:
        desc_parts.append(str(year))
    with tqdm(desc=" ".join(desc_parts), unit="page") as progress:
        while target_url:
            html = session.get_text(target_url, use_cache=False)
            page_rows = [
                _normalise_record(row)
                for row in parse_simple_search_rows(html, base_url)
                if row
            ]
            for row in page_rows:
                item_url = row.get("item_url")
                if not item_url:
                    continue
                if "/handle/" in item_url:
                    item_id = item_url.split("/")[-1]
                    if item_id.isdigit():
                        row["id"] = int(item_id)
                row.pop("item_url", None)
            rows.extend(page_rows)
            progress.update(1)
            progress.set_postfix(records=len(rows), page_records=len(page_rows))
            if not paginate:
                break
            next_url = parse_next_page(html, base_url)
            if next_url:
                target_url = next_url
                continue
            if len(page_rows) < rpp:
                break
            start += rpp
            target_url = build_simple_search_url(base_url, location or "", year, rpp, start=start)

    return pd.DataFrame(rows)
