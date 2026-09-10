"""Index the file table Skemman shows on each item page."""

from __future__ import annotations
import requests

import re
from pathlib import Path

import duckdb
from bs4 import BeautifulSoup
from tqdm import tqdm

from skemman_scraper.utils import PoliteSession

# "86,08 MB" -- Icelandic decimal comma, unit separated by a space.
_SIZE = re.compile(r"^\s*([\d.,]+)\s*(B|KB|MB|GB)\s*$", re.I)
_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}


def parse_size(label: str) -> int | None:
    """Turn a Skemman size label into bytes."""
    m = _SIZE.match(label or "")
    if not m:
        return None
    number = m.group(1).replace(".", "").replace(",", ".")
    try:
        return int(float(number) * _UNITS[m.group(2).upper()])
    except ValueError:
        return None


def parse_file_table(html: str) -> list[dict[str, object]]:
    """Read the Skrár table: one row per file attached to the item."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, object]] = []

    for table in soup.select("table.t-data-grid"):
        headers = [th.get_text(strip=True).lower() for th in table.select("thead th")]
        if "skráarnafn" not in headers:
            continue
        for row in table.select("tbody tr"):
            cells = [td.get_text(" ", strip=True) for td in row.select("td")]
            if len(cells) < 5:
                continue
            link = row.select_one("a[href]")
            size_label = cells[1]
            out.append(
                {
                    "filename": cells[0],
                    "size_label": size_label,
                    "size_bytes": parse_size(size_label),
                    "access": cells[2],
                    "description": cells[3],
                    "filetype": cells[4],
                    "href": link["href"] if link else None,
                }
            )
    return out


# An item usually carries more than one PDF, and only one of them is the thesis.
# The rest are the library's declaration form, appendices, drawings, a request to
# close the item. Size does not separate them: a scanned one-page declaration is
# often larger than the thesis it accompanies.
#
# The description column is the reliable signal. Where it is missing -- and it is
# missing on about a hundred items -- the filename gives it away instead:
# "Yfirlysing um medferd lokaverkefna.pdf", "Landsbokasafn Islands.pdf".
NOT_THESIS_DESCRIPTIONS = {
    "yfirlýsing",
    "beiðni um lokun",
    "viðauki",
    "fylgiskjöl",
    "forsíða",
    "titilsíða",
    "kápa",
    "teikning",
    "heimildaskrá",
    "efnisyfirlit",
}

_NOT_THESIS_FILENAME = re.compile(
    "yfirl[yý]s"
    # "yfirl.pdf" -- abbreviated, on an item where the description was left
    # blank. Bounded, so "efnisyfirlit" is untouched.
    r"|\byfirl\b"
    r"|bei[ðd]ni\s*um\s*lokun"
    "|lokunarbei[ðd]ni"
    "|landsb[oó]kasafn"
    "|declaration",
    re.I,
)

FULLTEXT_DESCRIPTION = "heildartexti"


def _looks_like_the_thesis(entry: dict[str, object]) -> bool:
    description = str(entry.get("description") or "").strip().lower()
    filename = str(entry.get("filename") or "")
    if description in NOT_THESIS_DESCRIPTIONS:
        return False
    return not _NOT_THESIS_FILENAME.search(filename)


def classify_files(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    """Mark one PDF per item `primary` -- the thesis -- and the rest `secondary`.

    Returns the same dicts with a `role` key added, so every file on the item is
    accounted for rather than silently filtered away.
    """
    for entry in entries:
        entry["role"] = "secondary"

    pdfs = [e for e in entries if str(e.get("filetype") or "").upper() == "PDF"]
    if not pdfs:
        return entries

    def size(entry: dict[str, object]) -> int:
        value = entry.get("size_bytes")
        return int(value) if isinstance(value, int) else -1

    fulltext = [
        e
        for e in pdfs
        if str(e.get("description") or "").strip().lower() == FULLTEXT_DESCRIPTION
    ]
    # Several files can share the description when a thesis is split in parts;
    # the largest is the one worth reading.
    candidates = fulltext or [e for e in pdfs if _looks_like_the_thesis(e)]
    if candidates:
        max(candidates, key=size)["role"] = "primary"
    return entries


def _create_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        create table if not exists thesis_file (
            thesis_id   integer,
            filename    varchar,
            size_label  varchar,
            size_bytes  bigint,
            access      varchar,
            description varchar,
            filetype    varchar,
            url         varchar,
            role        varchar
        )
        """
    )
    # A database created before `role` existed -- by this package or by the
    # study's own create_thesis_db.sql -- keeps its old table, because
    # `create table if not exists` says nothing about columns. Add it here so
    # every path into this function ends with a table the insert below fits.
    con.execute("alter table thesis_file add column if not exists role varchar")
    con.execute(
        """
        create table if not exists thesis_file_index_status (
            thesis_id integer,
            indexed_at timestamp default current_timestamp,
            file_count integer
        )
        """
    )
    con.execute(
        """
        create unique index if not exists thesis_file_index_status_uq
        on thesis_file_index_status (thesis_id)
        """
    )


def load_file_index(
    db: Path,
    base_url: str = "https://skemman.is",
    ids: str | None = None,
    limit: int | None = None,
    user_agent: str = "skemman-file-indexer",
    delay: float = 30.0,
    timeout: int = 30,
) -> tuple[int, int]:
    """Populate thesis_file from Skemman item pages. Returns (theses, files)."""
    selected_ids = [int(part.strip()) for part in ids.split(",") if part.strip()] if ids else []
    theses = rows_written = 0
    session = PoliteSession(
        user_agent=user_agent,
        delay_seconds=delay,
        timeout_seconds=timeout,
        cache_dir=None,
    )

    with duckdb.connect(str(db)) as con:
        _create_table(con)
        if selected_ids:
            placeholders = ", ".join("?" for _ in selected_ids)
            query = f"""
                select id, item_url
                from v_thesis
                where id in ({placeholders})
                order by id
            """
            item_rows = con.execute(query, selected_ids).fetchall()
        else:
            query = """
                select v.id, v.item_url
                from v_thesis v
                left join thesis_file_index_status s
                  on s.thesis_id = v.id
                where s.thesis_id is null
                order by v.id
            """
            if limit is not None:
                query += " limit ?"
                item_rows = con.execute(query, [limit]).fetchall()
            else:
                item_rows = con.execute(query).fetchall()

        for thesis_id, item_url in tqdm(item_rows, desc="Indexing files", unit="item"):
            try:
                html = session.get_text(item_url, use_cache=False)
            except requests.RequestException as exc:
                # A withdrawn item answers 404 at its handle while OAI-PMH still lists
                # it -- 23865 did, and stopped the run 52 items into 256, leaving the
                # rest unasked. Say so and move on; it is retried on the next run.
                tqdm.write(f"skipped {thesis_id}: {exc}")
                continue
            entries = classify_files(parse_file_table(html))
            con.execute("delete from thesis_file where thesis_id = ?", [thesis_id])
            con.execute("delete from thesis_file_index_status where thesis_id = ?", [thesis_id])
            theses += 1
            for entry in entries:
                href = entry["href"]
                url = f"{base_url}{href}" if href and href.startswith("/") else href
                con.execute(
                    "insert into thesis_file "
                    "(thesis_id, filename, size_label, size_bytes, access, "
                    " description, filetype, url, role) "
                    "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        thesis_id,
                        entry["filename"],
                        entry["size_label"],
                        entry["size_bytes"],
                        entry["access"],
                        entry["description"],
                        entry["filetype"],
                        url,
                        entry["role"],
                    ],
                )
                rows_written += 1
            con.execute(
                """
                insert into thesis_file_index_status (thesis_id, file_count)
                values (?, ?)
                """,
                [thesis_id, len(entries)],
            )

        con.execute("checkpoint")

    return theses, rows_written
