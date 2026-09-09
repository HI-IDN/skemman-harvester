"""Read DSpace's `xoai` metadata format.

Skemman publishes twelve metadata formats over OAI-PMH. `oai_dc` is the one
every repository must offer, and it is what the rest of this package harvests;
it carries the bibliographic record and nothing else.

`xoai` is DSpace's own format, and it carries the item's **bundles**: every
attached file, with its name, byte size, MIME type, download URL, checksum,
sequence id, and -- the field that matters most here -- the description DSpace
files it under, `COMPLETE_TEXT` or `DECLARATION`. That is the repository itself
saying which attachment is the thesis and which is the form the student signs.

That inventory is otherwise only available by fetching each item's HTML page,
one request per thesis. Over `xoai` it arrives in the same paged sweep as the
records: about 66 requests for the whole collection instead of 6291.

What `xoai` does **not** carry is the access status. The bundle listing shows
an embargoed file exactly as it shows an open one -- Skemman states `Opinn` and
`Lokaður til dd.mm.yyyy` only on the item page. Rows loaded from here therefore
leave `access` null, and whether a file can actually be downloaded is found out
by trying, which costs a request that would be spent on the download anyway.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from tqdm import tqdm

from .files_index import (
    _NOT_THESIS_FILENAME,
    FULLTEXT_DESCRIPTION,
    NOT_THESIS_DESCRIPTIONS,
    _looks_like_the_thesis,
)

XOAI_NS = "http://www.lyncode.com/xoai"
OAI_NS = "http://www.openarchives.org/OAI/2.0/"
NS = {"oai": OAI_NS, "x": XOAI_NS}

# The bundle holding what the student uploaded. LICENSE, THUMBNAIL, TEXT and
# the rest are DSpace's own derivatives and are not part of the submission.
ORIGINAL_BUNDLE = "ORIGINAL"

# DSpace's bundle descriptions, mapped to the Icelandic labels Skemman prints on
# the item page. files-index has always stored the Icelandic ones, and the
# selection SQL in titlepage_load matches on 'Heildartexti', so translating here
# keeps one vocabulary in the column no matter which path filled it.
DESCRIPTIONS = {
    "COMPLETE_TEXT": "Heildartexti",
    "DECLARATION": "Yfirlýsing",
}

MIME_TYPES = {
    "application/pdf": "PDF",
    "application/zip": "ZIP",
    "application/msword": "DOC",
    "text/plain": "TXT",
}


def _field(element: ET.Element, name: str) -> str | None:
    node = element.find('x:field[@name="' + name + '"]', NS)
    if node is None or node.text is None:
        return None
    text = node.text.strip()
    return text or None


def _int_field(element: ET.Element, name: str) -> int | None:
    raw = _field(element, name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _values(metadata: ET.Element, *path: str) -> list[str]:
    """Collect every `field[@name="value"]` under a chain of named elements.

    xoai nests one `<element>` per metadata segment, so `dc.description.advisor`
    is three levels deep, and the leaf below it is the language -- `none`, `is`,
    `en`. The language level is skipped by searching for the fields anywhere
    beneath, which also keeps multiple advisors on one item together.
    """
    node: ET.Element | None = metadata
    for name in path:
        if node is None:
            return []
        node = node.find('x:element[@name="' + name + '"]', NS)
    if node is None:
        return []
    return [f.text.strip() for f in node.findall('.//x:field[@name="value"]', NS) if f.text]


def _filetype(mime: str | None, filename: str | None) -> str | None:
    if mime and mime.lower() in MIME_TYPES:
        return MIME_TYPES[mime.lower()]
    if filename and "." in filename:
        return filename.rsplit(".", 1)[1].upper()
    return None


def _id_from_handle(handle: str | None) -> int | None:
    if not handle or "/" not in handle:
        return None
    try:
        return int(handle.rsplit("/", 1)[1])
    except ValueError:
        return None


def parse_xoai_bitstreams(metadata: ET.Element) -> list[dict[str, Any]]:
    """Every file in the ORIGINAL bundle, in the order DSpace lists them."""
    files: list[dict[str, Any]] = []
    bundle_path = './/x:element[@name="bundles"]/x:element[@name="bundle"]'
    for bundle in metadata.findall(bundle_path, NS):
        if _field(bundle, "name") != ORIGINAL_BUNDLE:
            continue
        for bitstream in bundle.findall('.//x:element[@name="bitstream"]', NS):
            filename = _field(bitstream, "name") or _field(bitstream, "originalName")
            raw_description = _field(bitstream, "description")
            mime = _field(bitstream, "format")
            files.append(
                {
                    "filename": filename,
                    "size_bytes": _int_field(bitstream, "size"),
                    "description": DESCRIPTIONS.get(raw_description or "", raw_description),
                    "filetype": _filetype(mime, filename),
                    "url": _field(bitstream, "url"),
                    "sid": _int_field(bitstream, "sid"),
                    "checksum": _field(bitstream, "checksum"),
                }
            )
    return files


def _thesis_score(entry: dict[str, Any]) -> int:
    """How strongly the two things Skemman records say this file is the thesis.

    The description is what DSpace filed the bitstream under, and it is right
    almost always: `COMPLETE_TEXT` for the thesis, `DECLARATION` for the form
    the student signs. The filename is the weaker signal on its own -- plenty of
    theses are called "skemman.pdf" -- but when it names a form outright it is
    the more trustworthy of the two, because the description is set by a menu
    and the name is set by the person who made the file.

    They disagree on two items in 6305, and in both the submitter attached the
    pair the wrong way round: 35842 files "declaration of access.pdf" as
    COMPLETE_TEXT and "Mesay Fekadu Biru Thesis.pdf" as the declaration; 41556
    does the same with "yfirl.pdf". Weighting the filename heavier than the
    description resolves both without a special case, and leaves every item
    where the two agree exactly where it was.
    """
    score = 0
    description = str(entry.get("description") or "").strip().lower()
    if description == FULLTEXT_DESCRIPTION:
        score += 2
    elif description in NOT_THESIS_DESCRIPTIONS:
        score -= 2
    if _NOT_THESIS_FILENAME.search(str(entry.get("filename") or "")):
        score -= 5
    return score


def assign_roles(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the one file that is the thesis `primary`, the rest `secondary`.

    Size is only a tie-breaker, never a signal. It is wrong on its own in 469 of
    2410 multi-file items, where the declaration is the larger file: it is a
    scan and the thesis is not.

    An item whose every file is a form -- it happens -- gets no primary rather
    than a wrong one.
    """
    for entry in files:
        entry["role"] = "secondary"

    candidates = [f for f in files if f["filetype"] == "PDF"]
    if not candidates:
        return files

    best = max(candidates, key=lambda f: (_thesis_score(f), f["size_bytes"] or 0))
    # Passed over only when both signals agree it is a form. One of the two
    # saying so is a disagreement, and a disagreement still leaves the better
    # of the files on the item -- 35842's thesis is filed as the declaration,
    # and it is still the thesis.
    if not _looks_like_the_thesis(best) and _thesis_score(best) <= -7:
        return files
    best["role"] = "primary"
    return files


def parse_xoai_records(xml: str) -> tuple[list[dict[str, Any]], str | None]:
    """Records from one xoai page, plus the resumption token if there is one."""
    root = ET.fromstring(xml)
    error = root.find("oai:error", NS)
    if error is not None:
        code = error.attrib.get("code", "unknown")
        if code == "noRecordsMatch":
            return [], None
        raise ValueError(code + ": " + (error.text or "").strip())

    rows: list[dict[str, Any]] = []
    for record in root.findall(".//oai:record", NS):
        header = record.find("oai:header", NS)
        if header is not None and header.attrib.get("status") == "deleted":
            continue
        metadata = record.find(".//x:metadata", NS)
        if metadata is None:
            continue
        handle = None
        others = metadata.find('x:element[@name="others"]', NS)
        if others is not None:
            handle = _field(others, "handle")
        thesis_id = _id_from_handle(handle)
        if thesis_id is None:
            continue
        rows.append(
            {
                "id": thesis_id,
                "advisors": _values(metadata, "dc", "description", "advisor"),
                "authors": _values(metadata, "dc", "contributor", "author"),
                "degree": (_values(metadata, "dc", "type", "degree") or [None])[0],
                "files": assign_roles(parse_xoai_bitstreams(metadata)),
            }
        )

    token_node = root.find(".//oai:resumptionToken", NS)
    token = token_node.text.strip() if token_node is not None and token_node.text else None
    return rows, token


def load_files_from_xoai(
        db: str | Path = "data/processed/thesis.db",
        oai_dir: str | Path = "data/raw/oai",
        ids: str | None = None,
) -> tuple[int, int]:
    """Fill `thesis_file` from cached xoai pages. No network.

    Returns the number of theses and the number of file rows written. Existing
    rows for a thesis are replaced, so this is safe to re-run, and `access` is
    carried over from whatever filled the row before -- files-index knows the
    access status and this does not.

    It also records `dc.type.degree`, which xoai carries and oai_dc does not.
    """
    import duckdb

    from .files_index import _create_table
    from .metadata_load import normalise_degree

    selected = {int(i) for i in ids.split(",") if i.strip()} if ids else None

    pages = sorted(Path(oai_dir).glob("xoai_*.xml"))
    if not pages:
        raise SystemExit(
            "No xoai pages in " + str(oai_dir) + ". Harvest them first:\n"
            "  skemman oai-pmh --metadata-prefix xoai"
        )

    theses = 0
    written = 0
    degrees = 0
    with duckdb.connect(str(db)) as con:
        _create_table(con)
        # dc.type.degree is the registrar's own field and it names two levels
        # OAI's dc:type does not: "Doctoral", and the two diplomas. 841 records
        # reach thesis_metadata with no degree level at all, and all but 191 of
        # them are named here. The raw string is kept beside the normalized one
        # so "Undergraduate diploma" -- idnfraedi, outside a master's
        # population -- stays distinguishable from "Graduate diploma".
        con.execute("alter table thesis_metadata add column if not exists degree_raw varchar")
        for path in tqdm(pages, desc="Loading xoai files", unit="page"):
            rows, _ = parse_xoai_records(path.read_text(encoding="utf-8", errors="replace"))
            for row in rows:
                thesis_id = row["id"]
                if selected is not None and thesis_id not in selected:
                    continue
                if row["degree"]:
                    # Only where OAI said nothing. Where both speak they agree,
                    # and overwriting a value this study has already reasoned
                    # about is not this loader's business.
                    # DuckDB returns the number of rows an UPDATE touched as
                    # the statement's own result. There is no changes() to ask
                    # afterwards -- that is SQLite.
                    updated = con.execute(
                        "update thesis_metadata "
                        "set degree_raw = ?, "
                        "    degree_level = coalesce(degree_level, ?) "
                        "where thesis_id = ?",
                        [row["degree"], normalise_degree(row["degree"]), thesis_id],
                    ).fetchone()
                    degrees += (updated[0] if updated else 0) or 0
                if not row["files"]:
                    continue
                known_access = dict(
                    con.execute(
                        "select filename, access from thesis_file "
                        "where thesis_id = ? and access is not null",
                        [thesis_id],
                    ).fetchall()
                )
                con.execute("delete from thesis_file where thesis_id = ?", [thesis_id])
                theses += 1
                for entry in row["files"]:
                    con.execute(
                        "insert into thesis_file "
                        "(thesis_id, filename, size_label, size_bytes, access, "
                        " description, filetype, url, role) "
                        "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            thesis_id,
                            entry["filename"],
                            None,
                            entry["size_bytes"],
                            known_access.get(entry["filename"]),
                            entry["description"],
                            entry["filetype"],
                            entry["url"],
                            entry["role"],
                        ],
                    )
                    written += 1
    print(f"Set degree_raw on {degrees} records.")
    return theses, written
