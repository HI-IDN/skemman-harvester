from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from tqdm import tqdm

from skemman_scraper.oai_pmh import parse_oai_pmh_records

if TYPE_CHECKING:
    import duckdb


NOTE_PREFIXES = ["athugasemdir", "athugasemd", "athugsemd"]
NOTE_PHRASES = ["ritgerðin er lokuð", "vantar forsíðu"]
NOTE_KEYWORDS = ["closed"]


def normalise_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    return text or None


def split_keywords(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        parts = [p.strip() for p in re.split(r"[;,]", value)]
        for part in parts:
            cleaned = normalise_text(part)
            if not cleaned:
                continue
            norm = cleaned.casefold()
            if norm == "thesis":
                continue
            if norm in seen:
                continue
            seen.add(norm)
            out.append(cleaned)
    return out


def is_icelandic_text(text: str) -> bool:
    return bool(re.search(r"[áðéíóúýþæö]", text.lower()))


def pick_degree(types: list[str]) -> str | None:
    for value in types:
        degree = normalise_degree(value)
        if degree:
            return degree
    return None


def normalise_degree(value: str | None) -> str | None:
    if not value:
        return None
    low = value.lower()
    if "doktor" in low or "phd" in low:
        return "phd"
    if "meist" in low or "master" in low or "msc" in low:
        return "master"
    if "bachelor" in low or "b.sc" in low or "bsc" in low:
        return "bachelor"
    # xoai states the registrar's own value, which includes two levels OAI's
    # dc:type never names: "Doctoral", and the diplomas. Undergraduate diploma
    # is idnfraedi and the like; graduate diploma sits beside a master's. Both
    # land on "diploma" -- degree_raw keeps which one it was.
    if "doctoral" in low:
        return "phd"
    if "diploma" in low or "diplóma" in low:
        return "diploma"
    return None


def parse_person_name(text: str) -> tuple[str, int | None, int | None]:
    cleaned = normalise_text(text) or ""
    year_pattern = r"(?<!\d)((?:19|20)\d{2})(?:\s*[-–]\s*((?:19|20)\d{2})?)?(?!\d)"
    match = re.search(year_pattern, cleaned)
    year_born = int(match.group(1)) if match else None
    year_died = int(match.group(2)) if match and match.group(2) else None

    name = re.sub(year_pattern, " ", cleaned)
    name = re.sub(r"\([^)]*\)", " ", name)
    name = normalise_text(name)
    if name:
        name = name.strip(" ,;-")
    return name or "", year_born, year_died


def clean_people_table(db: str | Path) -> int:
    import duckdb

    changed = 0
    with duckdb.connect(db) as con:
        rows = con.execute("select id, name, year_born, year_died from people").fetchall()
        for person_id, raw_name, raw_year_born, raw_year_died in rows:
            name, parsed_year_born, parsed_year_died = parse_person_name(str(raw_name or ""))
            if not name:
                continue
            year_born = int(raw_year_born) if raw_year_born is not None else parsed_year_born
            year_died = int(raw_year_died) if raw_year_died is not None else parsed_year_died
            if (
                    name == raw_name
                    and year_born == raw_year_born
                    and year_died == raw_year_died
            ):
                continue

            existing = con.execute(
                """
                select id
                from people
                where id <> ?
                  and name = ?
                  and coalesce(year_born, -1) = coalesce(?, -1)
                order by id
                limit 1
                """,
                [person_id, name, year_born],
            ).fetchone()
            if existing:
                target_id = int(existing[0])
                con.execute(
                    """
                    update thesis_people
                    set person_id = ?
                    where person_id = ?
                      and not exists (
                          select 1
                          from thesis_people existing_link
                          where existing_link.thesis_id = thesis_people.thesis_id
                            and existing_link.person_id = ?
                            and existing_link.role = thesis_people.role
                      )
                    """,
                    [target_id, person_id, target_id],
                )
                con.execute("delete from thesis_people where person_id = ?", [person_id])
                con.execute("delete from people where id = ?", [person_id])
            else:
                con.execute(
                    "update people set name = ?, year_born = ?, year_died = ? where id = ?",
                    [name, year_born, year_died, person_id],
                )
            changed += 1
    return changed


def keyword_norm(value: str) -> str:
    return value.casefold().strip()


def ensure_keyword(
        con: duckdb.DuckDBPyConnection,
        keyword: str,
) -> int:
    norm = keyword_norm(keyword)
    row = con.execute(
        "select id from keywords where keyword_norm = ?",
        [norm],
    ).fetchone()
    if row:
        return int(row[0])

    inserted = con.execute(
        "insert into keywords (keyword, keyword_norm) values (?, ?) returning id",
        [keyword, norm],
    ).fetchone()
    return int(inserted[0])


def insert_keyword_links(
        con: duckdb.DuckDBPyConnection,
        thesis_id: int,
        keywords: Iterable[str],
) -> None:
    for sort_order, keyword in enumerate(keywords):
        cleaned = normalise_text(keyword)
        if not cleaned:
            continue
        keyword_id = ensure_keyword(con, cleaned)
        con.execute(
            """
            insert into thesis_keywords (thesis_id, keyword_id, sort_order)
            select ?,
                   ?,
                   ? where not exists (
                select 1
                from thesis_keywords
                where thesis_id = ?
                  and keyword_id = ?
            )
            """,
            [thesis_id, keyword_id, sort_order, thesis_id, keyword_id],
        )


def _parse_ids(ids: str | None) -> set[int] | None:
    if not ids:
        return None
    return {int(part.strip()) for part in ids.split(",") if part.strip()}


def _pick_oai_abstract(descriptions: list[str]) -> tuple[str | None, str | None, str | None]:
    descriptions = [cleaned for value in descriptions if (cleaned := normalise_text(value))]
    abstract_is, descriptions = extract_icelandic_abstract(descriptions)
    note, descriptions = extract_notes(descriptions)

    icelandic = [value for value in descriptions if is_icelandic_text(value)]
    other = [value for value in descriptions if not is_icelandic_text(value)]
    if not abstract_is and icelandic:
        abstract_is = icelandic[0]
    abstract_en = other[0] if other else None
    return abstract_is, abstract_en, note


def _pick_oai_title(title: str | None) -> tuple[str | None, str | None]:
    title = normalise_text(title)
    if not title:
        return None, None
    if is_icelandic_text(title):
        return title, None
    return None, title


def _as_str_list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return [str(value)]


def _create_metadata_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("create sequence if not exists keyword_id_seq start 1")
    con.execute(
        """
        create table if not exists thesis_metadata (
            thesis_id integer,
            title_is varchar,
            title_en varchar,
            abstract_is varchar,
            abstract_en varchar,
            degree_level varchar,
            thesis_type varchar,
            sponsor varchar,
            note varchar,
            related_url varchar,
            raw_keywords varchar,
            pdf_url varchar,
            institution varchar,
            school varchar,
            university varchar,
            faculty varchar,
            study_category varchar,
            thesis_type_label varchar
        )
        """
    )
    con.execute(
        """
        create table if not exists keywords (
            id bigint default nextval('keyword_id_seq'),
            keyword varchar,
            keyword_norm varchar
        )
        """
    )
    con.execute(
        """
        create table if not exists thesis_keywords (
            thesis_id integer,
            keyword_id bigint,
            sort_order integer
        )
        """
    )
    con.execute("create unique index if not exists keywords_norm_uq on keywords (keyword_norm)")
    con.execute(
        "create unique index if not exists thesis_keywords_uq on thesis_keywords "
        "(thesis_id, keyword_id)"
    )


def write_oai_metadata_rows(rows: list[dict[str, object]], db_path: Path) -> int:
    import duckdb

    loaded = 0
    with duckdb.connect(str(db_path)) as con:
        _create_metadata_tables(con)
        for row in rows:
            thesis_id = int(row["id"])
            keywords = split_keywords(_as_str_list(row.get("subjects")))
            abstract_is, abstract_en, note = _pick_oai_abstract(
                _as_str_list(row.get("descriptions"))
            )
            title_is, title_en = _pick_oai_title(row.get("title"))
            types = _as_str_list(row.get("types"))
            degree_level = pick_degree(types)
            thesis_type = "; ".join(normalize_thesis_types(types, degree_level)) if types else None
            contributors = dedupe_preserve_order(_as_str_list(row.get("contributors")))
            institution = contributors[0] if contributors else None
            related_urls = dedupe_preserve_order(_as_str_list(row.get("relations")))
            related_url = related_urls[0] if related_urls else None

            con.execute(
                """
                insert into thesis_metadata (thesis_id)
                select ?
                where not exists (
                    select 1
                    from thesis_metadata
                    where thesis_id = ?
                )
                """,
                [thesis_id, thesis_id],
            )
            con.execute(
                """
                update thesis_metadata
                set title_is = coalesce(?, title_is),
                    title_en = coalesce(?, title_en),
                    abstract_is = coalesce(?, abstract_is),
                    abstract_en = coalesce(?, abstract_en),
                    degree_level = coalesce(?, degree_level),
                    thesis_type = coalesce(?, thesis_type),
                    note = coalesce(?, note),
                    related_url = coalesce(?, related_url),
                    raw_keywords = coalesce(?, raw_keywords),
                    institution = coalesce(?, institution),
                    university = coalesce(?, university)
                where thesis_id = ?
                """,
                [
                    title_is,
                    title_en,
                    abstract_is,
                    abstract_en,
                    degree_level,
                    thesis_type,
                    note,
                    related_url,
                    "; ".join(keywords) if keywords else None,
                    institution,
                    institution,
                    thesis_id,
                ],
            )
            con.execute("delete from thesis_keywords where thesis_id = ?", [thesis_id])
            insert_keyword_links(con, thesis_id, keywords)
            loaded += 1
        con.execute("checkpoint")
    return loaded


def load_oai_metadata(
        db: str | Path = "data/processed/thesis.db",
        oai_dir: str | Path = "data/raw/oai",
        ids: str | None = None,
        metadata_prefix: str = "oai_dc",
) -> int:
    import duckdb

    selected_ids = _parse_ids(ids)
    if selected_ids is None:
        with duckdb.connect(db) as con:
            selected_ids = {int(row[0]) for row in con.execute("select id from thesis").fetchall()}

    # One directory holds every format the harvest has fetched, named by
    # prefix: oai_dc_*.xml beside xoai_*.xml. Reading them all would parse the
    # xoai pages with the oai_dc parser, which finds the record ids and nothing
    # else -- harmless, because every column is written with coalesce, but it
    # doubles the work and reports twice the records it loaded. A cache from
    # before the names carried a prefix falls back to everything.
    pages = sorted(Path(oai_dir).glob(f"{metadata_prefix}_*.xml"))
    if not pages:
        pages = sorted(Path(oai_dir).glob("*.xml"))

    loaded = 0
    for path in tqdm(pages, desc="Loading OAI metadata"):
        page_rows, _ = parse_oai_pmh_records(path.read_text(encoding="utf-8", errors="replace"))
        rows = [row for row in page_rows if int(row["id"]) in selected_ids]
        if not rows:
            continue
        loaded += write_oai_metadata_rows(rows, Path(db))
    return loaded


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = normalise_text(value)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def normalize_thesis_types(values: list[str], degree_level: str | None) -> list[str]:
    normalized = dedupe_preserve_order(values)
    if degree_level != "master":
        return normalized

    cleaned: list[str] = []
    for value in normalized:
        low = value.casefold()
        if "graduate diploma" in low:
            continue
        if "master" in low:
            cleaned.append("Master's")
            continue
        cleaned.append(value)

    cleaned = dedupe_preserve_order(cleaned)
    has_thesis = any(value.casefold() == "thesis" for value in cleaned)
    has_master = any(value.casefold() == "master's" for value in cleaned)

    ordered: list[str] = []
    if has_thesis:
        ordered.append("Thesis")
    if has_master:
        ordered.append("Master's")
    ordered.extend(value for value in cleaned if value.casefold() not in {"thesis", "master's"})

    return ordered


def extract_icelandic_abstract(descriptions: list[str]) -> tuple[str | None, list[str]]:
    prefixes = [
        "íslenskt ágrip:",
        "íslenskt ágrip",
        "ágrip:",
        "ágrip",
    ]
    remaining: list[str] = []
    abstract_is: str | None = None
    for value in descriptions:
        cleaned = normalise_text(value)
        if not cleaned:
            continue
        lower = cleaned.casefold()
        matched = False
        for prefix in prefixes:
            if lower.startswith(prefix):
                matched = True
                abstract_is = cleaned[len(prefix):].strip() or abstract_is
                break
        if not matched:
            remaining.append(cleaned)
    return abstract_is, remaining


def extract_notes(descriptions: list[str]) -> tuple[str | None, list[str]]:
    notes: list[str] = []
    remaining: list[str] = []
    for value in descriptions:
        cleaned = normalise_text(value)
        if not cleaned:
            continue
        lower = cleaned.casefold()
        if any(lower.startswith(prefix) for prefix in NOTE_PREFIXES):
            note = cleaned.split(":", 1)[1].strip() if ":" in cleaned else cleaned
            notes.append(note or cleaned)
            continue
        if any(phrase in lower for phrase in NOTE_PHRASES) or any(
                keyword in lower for keyword in NOTE_KEYWORDS
        ):
            notes.append(cleaned)
            continue
        remaining.append(cleaned)
    return ("; ".join(notes) if notes else None), remaining
