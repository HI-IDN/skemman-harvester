from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import pandas as pd
from tqdm import tqdm

from .utils import PoliteSession

OAI_NS = "http://www.openarchives.org/OAI/2.0/"
DC_NS = "http://purl.org/dc/elements/1.1/"
NS = {"oai": OAI_NS, "dc": DC_NS}


@dataclass
class OaiRecord:
    id: int
    date_accepted: str | None
    title: str | None
    authors: str | None


def _normalize_location(location: str) -> str:
    value = location.strip()
    if "/" in value:
        return value
    if " " in value:
        return "/".join(part for part in value.split() if part)
    return value


def set_spec_from_location(location: str) -> str:
    """Map a DSpace handle to Skemman's OAI-PMH setSpec."""
    value = _normalize_location(location)
    if value.startswith(("com_", "col_")):
        return value
    return "com_" + value.replace("/", "_")


def build_oai_pmh_url(
        base_url: str,
        *,
        metadata_prefix: str = "oai_dc",
        set_spec: str | None = None,
        resumption_token: str | None = None,
) -> str:
    endpoint = f"{base_url.rstrip('/')}/oai/request"
    if resumption_token:
        params = {"verb": "ListRecords", "resumptionToken": resumption_token}
        return f"{endpoint}?{urlencode(params)}"

    params = {"verb": "ListRecords", "metadataPrefix": metadata_prefix}
    if set_spec:
        params["set"] = set_spec
    return f"{endpoint}?{urlencode(params)}"


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _dc_values(record: ET.Element, name: str) -> list[str]:
    return [
        value
        for value in (_text(el) for el in record.findall(f".//dc:{name}", NS))
        if value is not None
    ]


def _id_from_record(record: ET.Element) -> int | None:
    identifiers = _dc_values(record, "identifier")
    identifiers.append(_text(record.find("oai:header/oai:identifier", NS)) or "")
    for identifier in identifiers:
        match = re.search(r"1946/(\d+)", identifier)
        if match:
            return int(match.group(1))
    return None


def _normalise_dc_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.match(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", value.strip())
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{month or '01'}-{day or '01'}"


def _date_year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.match(r"^(\d{4})", value)
    return int(match.group(1)) if match else None


def parse_oai_pmh_records(xml: str) -> tuple[list[dict[str, Any]], str | None]:
    root = ET.fromstring(xml)
    error = root.find("oai:error", NS)
    if error is not None:
        code = error.attrib.get("code", "unknown")
        message = _text(error) or "OAI-PMH request failed"
        if code == "noRecordsMatch":
            return [], None
        raise ValueError(f"{code}: {message}")

    rows: list[dict[str, Any]] = []
    for record in root.findall(".//oai:record", NS):
        header = record.find("oai:header", NS)
        if header is not None and header.attrib.get("status") == "deleted":
            continue
        thesis_id = _id_from_record(record)
        if thesis_id is None:
            continue
        dc_date = _normalise_dc_date((_dc_values(record, "date") or [None])[0])
        title = (_dc_values(record, "title") or [None])[0]
        creators = _dc_values(record, "creator")
        rows.append(
            {
                "id": thesis_id,
                "date_accepted": dc_date,
                "title": title,
                "authors": "; ".join(creators) if creators else None,
            }
        )

    token = _text(root.find(".//oai:resumptionToken", NS))
    return rows, token


def harvest_oai_pmh(
        config: dict,
        *,
        location: str | None,
        set_spec: str | None,
        year_start: int | None,
        year_end: int | None,
        metadata_prefix: str = "oai_dc",
        paginate: bool = True,
) -> pd.DataFrame:
    base_url = config["base_url"].rstrip("/")
    target_set = set_spec or (set_spec_from_location(location) if location else None)
    session = PoliteSession(
        user_agent=config["user_agent"],
        delay_seconds=float(config.get("request_delay_seconds", 30.0)),
        timeout_seconds=int(config.get("timeout_seconds", 30)),
        cache_dir=None,
    )

    rows: list[dict[str, Any]] = []
    next_token: str | None = None
    target_url = build_oai_pmh_url(
        base_url,
        metadata_prefix=metadata_prefix,
        set_spec=target_set,
    )
    desc_parts = ["oai-pmh"]
    if target_set:
        desc_parts.append(target_set)

    with tqdm(desc=" ".join(desc_parts), unit="page") as progress:
        while target_url:
            xml = session.get_text(target_url, use_cache=False)
            page_rows, next_token = parse_oai_pmh_records(xml)
            rows.extend(page_rows)
            progress.update(1)
            progress.set_postfix(records=len(rows), page_records=len(page_rows))
            if not paginate or not next_token:
                break
            target_url = build_oai_pmh_url(base_url, resumption_token=next_token)

    if year_start is not None or year_end is not None:
        start = year_start if year_start is not None else -9999
        end = year_end if year_end is not None else 9999
        rows = [
            row
            for row in rows
            if (year := _date_year(row.get("date_accepted"))) is not None and start <= year <= end
        ]

    return pd.DataFrame(rows)
