"""Index the file table Skemman shows on each item page.

The item page already states every file's size, access status and type. That is
enough to plan the download queue before touching the network: skip what is
closed, take the small files first, and treat the very large ones as the risk
they are. The HTML is already cached by `metadata-load`, so this costs nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
from bs4 import BeautifulSoup
from tqdm import tqdm

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
            url         varchar
        )
        """
    )


def load_file_index(
    db: Path,
    items_dir: Path = Path("data/raw/items"),
    base_url: str = "https://skemman.is",
) -> tuple[int, int]:
    """Populate thesis_file from the cached item HTML. Returns (theses, files)."""
    files = sorted(items_dir.glob("*.html"))
    theses = rows_written = 0

    with duckdb.connect(str(db)) as con:
        _create_table(con)
        con.execute("delete from thesis_file")

        for path in tqdm(files, desc="Indexing files", unit="item"):
            try:
                thesis_id = int(path.stem)
            except ValueError:
                continue
            entries = parse_file_table(path.read_text(encoding="utf-8", errors="replace"))
            if not entries:
                continue
            theses += 1
            for entry in entries:
                href = entry["href"]
                url = f"{base_url}{href}" if href and href.startswith("/") else href
                con.execute(
                    "insert into thesis_file "
                    "(thesis_id, filename, size_label, size_bytes, access, "
                    " description, filetype, url) values (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        thesis_id,
                        entry["filename"],
                        entry["size_label"],
                        entry["size_bytes"],
                        entry["access"],
                        entry["description"],
                        entry["filetype"],
                        url,
                    ],
                )
                rows_written += 1

        con.execute("checkpoint")

    return theses, rows_written
