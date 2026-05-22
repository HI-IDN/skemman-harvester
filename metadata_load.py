from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import duckdb
from bs4 import BeautifulSoup
from tqdm import tqdm

from skemman_scraper.utils import PoliteSession

NOTE_KEYS = ["Athugasemdir", "Athugasemd", "Notes", "Note"]
SPONSOR_KEYS = ["Styrktaraðili", "Sponsor"]
RELATED_URL_KEYS = ["Tengd vefslóð", "Related URL", "DCTERMS.relation"]
PDF_URL_KEYS = ["citation_pdf_url", "PDF", "Bitstream"]

NOTE_PREFIXES = ["athugasemdir", "athugasemd", "athugsemd"]
NOTE_PHRASES = ["ritgerðin er lokuð", "vantar forsíðu"]
NOTE_KEYWORDS = ["closed"]

PDF_LABEL_PRIORITY = ["Heildartexti", "Meginmál"]
SKEMMAN_BASE_URL = "https://skemman.is"

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Skemman item pages, parse metadata, and insert into DuckDB."
    )
    parser.add_argument(
        "--db",
        default="data/processed/thesis.db",
        help="DuckDB database path",
    )
    parser.add_argument(
        "--ids",
        help="Comma-separated thesis ids (e.g., 4445,25337)",
    )
    parser.add_argument(
        "--urls",
        help="Comma-separated item URLs (overrides --ids)",
    )
    parser.add_argument(
        "--out-html",
        default="data/raw/items",
        help="Directory to save raw HTML pages",
    )
    parser.add_argument(
        "--user-agent",
        default="skemman-metadata-loader",
        help="User-Agent header for requests",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay between requests (seconds)",
    )
    return parser.parse_args()


def normalise_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    return text or None


def metadata_from_html(html: str) -> dict[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    metadata: dict[str, list[str]] = {}

    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) >= 2:
            key = normalise_text(cells[0].get_text(" "))
            val = normalise_text(cells[1].get_text(" "))
            if key and val:
                metadata.setdefault(key.rstrip(":"), []).append(val)

    for dt in soup.find_all("dt"):
        key = normalise_text(dt.get_text(" "))
        dd = dt.find_next_sibling("dd")
        val = normalise_text(dd.get_text(" ")) if dd else None
        if key and val:
            metadata.setdefault(key.rstrip(":"), []).append(val)

    for meta in soup.find_all("meta"):
        key = meta.get("name") or meta.get("property")
        val = meta.get("content")
        if key and val:
            metadata.setdefault(key, []).append(normalise_text(val) or val)

    for attr in soup.select("div.attr"):
        label = attr.select_one(".attrLabel")
        content = attr.select_one(".attrContent")
        if not label or not content:
            continue
        key = normalise_text(label.get_text(" "))
        if not key:
            continue
        items = [normalise_text(li.get_text(" ")) for li in content.find_all("li")]
        values = [v for v in items if v] if items else [normalise_text(content.get_text(" "))]
        for val in values:
            if val:
                metadata.setdefault(key.rstrip(":"), []).append(val)

    return metadata


def get_first(metadata: dict[str, list[str]], *keys: str) -> str | None:
    lowered = {k.lower(): v for k, v in metadata.items()}
    for key in keys:
        values = lowered.get(key.lower())
        if values:
            return values[0]
    return None


def get_all(metadata: dict[str, list[str]], *keys: str) -> list[str]:
    lowered = {k.lower(): v for k, v in metadata.items()}
    out: list[str] = []
    for key in keys:
        out.extend(lowered.get(key.lower(), []))
    return [v for v in out if v]


def get_joined(metadata: dict[str, list[str]], *keys: str) -> str | None:
    values = get_all(metadata, *keys)
    return "; ".join(values) if values else None


def merge_note_values(*values: str | None) -> str | None:
    parts: list[str] = []
    for value in values:
        cleaned = normalise_text(value)
        if not cleaned:
            continue
        if cleaned in parts:
            continue
        parts.append(cleaned)
    return "; ".join(parts) if parts else None


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


def is_person_candidate(text: str) -> bool:
    if re.search(r"\d{4}-(\d{4})?$", text):
        return True
    if re.search(r"\b\d{4}\b", text) and len(text) <= 120:
        return True
    # Accept only a single-comma "Last, First" pattern.
    return bool(re.match(r"^[^,]+,\s*[^,]+$", text))


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
    return None


def parse_person_name(text: str) -> tuple[str, int | None, int | None]:
    cleaned = normalise_text(text) or ""
    match = re.match(r"^(.*?)(?:\s+(\d{4})-(\d{4})?)?$", cleaned)
    if not match:
        return cleaned, None, None
    name = match.group(1).strip()
    year_born = int(match.group(2)) if match.group(2) else None
    year_died = int(match.group(3)) if match.group(3) else None
    return name, year_born, year_died


def ensure_person(
        con: duckdb.DuckDBPyConnection,
        name: str,
        year_born: int | None,
        year_died: int | None,
) -> int:
    row = con.execute(
        """
        select id
        from people
        where name = ?
          and coalesce(year_born, -1) = coalesce(?, -1)
        """,
        [name, year_born],
    ).fetchone()
    if row:
        return int(row[0])
    inserted = con.execute(
        "insert into people (name, year_born, year_died) values (?, ?, ?) returning id",
        [name, year_born, year_died],
    ).fetchone()
    return int(inserted[0])


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


def insert_people_links(
        con: duckdb.DuckDBPyConnection,
        thesis_id: int,
        people: Iterable[tuple[str, int | None, int | None]],
        role: str,
) -> None:
    for sort_order, (name, year_born, year_died) in enumerate(people):
        if not name:
            continue
        person_id = ensure_person(con, name, year_born, year_died)
        con.execute(
            """
            insert into thesis_people (thesis_id, person_id, role, sort_order)
            select ?,
                   ?,
                   ?,
                   ? where not exists (
                select 1
                from thesis_people
                where thesis_id = ?
                  and person_id = ?
                  and role = ?
            )
            """,
            [thesis_id, person_id, role, sort_order, thesis_id, person_id, role],
        )


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


def resolve_urls(ids: str | None, urls: str | None) -> list[str]:
    if urls:
        return [u.strip() for u in urls.split(",") if u.strip()]
    if ids:
        out: list[str] = []
        for part in ids.split(","):
            value = part.strip()
            if value:
                out.append(f"https://skemman.is/handle/1946/{value}")
        return out
    return []


def extract_id_from_url(url: str) -> int | None:
    match = re.search(r"/handle/\d+/(\d+)$", url)
    return int(match.group(1)) if match else None


def split_people_values(values: list[str]) -> list[str]:
    out: list[str] = []
    year_pattern = r"[A-Za-zÁ-ÖÞÆÖáðéíóúýþæö][^,\d]{2,}\d{4}-(?:\d{4})?"
    comma_pattern = r"[^,]+?,\s*[^,]+"
    combined = re.compile(rf"({year_pattern}|{comma_pattern})")
    for value in values:
        matches = [m.group(0).strip() for m in combined.finditer(value)]
        if matches:
            out.extend(matches)
        else:
            out.append(value)
    return [normalise_text(v) for v in out if normalise_text(v)]


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


def pick_titles_from_metadata(metadata: dict[str, list[str]]) -> tuple[str | None, str | None]:
    values_all = get_all(
        metadata,
        "DCTERMS.title",
        "DCTERMS.alternative",
        "citation_title",
        "Titill",
        "Title",
        "dc.title",
        "dc_title_alternative",
    )
    if not values_all:
        return None, None

    alternatives = get_all(metadata, "DCTERMS.alternative", "dc_title_alternative")
    icelandic_values = [v for v in values_all if is_icelandic_text(v)]
    if icelandic_values:
        title_is_candidates = icelandic_values
    elif alternatives:
        title_is_candidates = alternatives
    else:
        title_is_candidates = []
    title_en_candidates = [v for v in values_all if not is_icelandic_text(v)]

    def unique(values: list[str]) -> list[str]:
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

    title_is_list = unique(title_is_candidates)
    title_en_list = unique(title_en_candidates)

    title_is = "; ".join(title_is_list) if title_is_list else None
    title_en = title_en_list[0] if title_en_list else None

    return title_is, title_en


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


def extract_breadcrumbs(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = soup.select("span.trail a")
    return [normalise_text(link.get_text(" ")) for link in links if
            normalise_text(link.get_text(" "))]


def pick_pdf_url(html: str, metadata: dict[str, list[str]]) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.t-data-grid")
    pdf_rows: list[tuple[str, str]] = []
    if table:
        for row in table.select("tbody tr"):
            cells: dict[str, str | None] = {}
            for cell in row.find_all("td"):
                headers = cell.get("headers")
                if isinstance(headers, (list, tuple)):
                    header_keys = [str(h) for h in headers]
                elif headers:
                    header_keys = [str(headers)]
                else:
                    header_keys = []

                value = normalise_text(cell.get_text(" "))
                for header_key in header_keys:
                    cells[header_key] = value

            file_type = (cells.get("t5") or "").casefold()
            if file_type != "pdf":
                continue
            label = cells.get("t4") or ""
            link = row.select_one("a[href]")
            if not link:
                continue
            href = link.get("href") or ""
            pdf_rows.append((label, href))
    if pdf_rows:
        for preferred in PDF_LABEL_PRIORITY:
            for label, href in pdf_rows:
                if preferred.casefold() in (label or "").casefold():
                    return urljoin(SKEMMAN_BASE_URL, href)
        return urljoin(SKEMMAN_BASE_URL, pdf_rows[0][1])
    return get_first(metadata, *PDF_URL_KEYS)


def load_metadata(
        db: str | Path = "data/processed/thesis.db",
        ids: str | None = None,
        urls: str | None = None,
        out_html: str | Path = "data/raw/items",
        user_agent: str = "skemman-metadata-loader",
        delay: float = 2.0,
) -> int:
    item_urls = resolve_urls(ids, urls)

    if not item_urls:
        with duckdb.connect(db) as con:
            rows = con.execute(
                """
                select v.item_url
                from v_thesis v
                         left join thesis_metadata m
                                   on m.thesis_id = v.id
                where m.thesis_id is null
                order by v.id
                """
            ).fetchall()

            item_urls = [row[0] for row in rows]
            print(f"Found {len(item_urls)} thesis ids missing metadata.")

    if not item_urls:
        raise SystemExit("Provide --ids or --urls.")

    out_dir = Path(out_html)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = PoliteSession(
        user_agent=user_agent,
        delay_seconds=delay,
        timeout_seconds=30,
        cache_dir=None,
    )

    loaded = 0
    with duckdb.connect(db) as con:
        for url in tqdm(item_urls, desc="Processing metadata"):
            thesis_id = extract_id_from_url(url)
            if thesis_id is None:
                continue

            html_path = out_dir / f"{thesis_id}.html"
            if html_path.exists():
                html = html_path.read_text(encoding="utf-8")
            else:
                html = session.get_text(url, use_cache=False)
                html_path.write_text(html, encoding="utf-8")
            metadata = metadata_from_html(html)
            breadcrumbs = extract_breadcrumbs(html)
            university = breadcrumbs[0] if len(breadcrumbs) >= 1 else None
            faculty = breadcrumbs[1] if len(breadcrumbs) >= 2 else None
            study_category = breadcrumbs[2] if len(breadcrumbs) >= 3 else None
            thesis_type_label = breadcrumbs[3] if len(breadcrumbs) >= 4 else None
            institution = university
            school = faculty

            title_is, title_en = pick_titles_from_metadata(metadata)
            abstract_is = get_first(metadata, "DCTERMS.abstract", "Útdráttur")
            abstract_en = get_first(metadata, "Abstract", "dc.description.abstract")
            if abstract_is and not abstract_en and not is_icelandic_text(abstract_is):
                abstract_en = abstract_is
                abstract_is = None

            types = get_all(metadata, "DCTERMS.type", "Type", "dc.type")
            degree_raw = get_first(metadata, "Námsstig", "Degree", "dc.description.degree")
            degree_level = normalise_degree(degree_raw) or pick_degree(types)
            thesis_type = "; ".join(normalize_thesis_types(types, degree_level)) if types else None

            sponsor = get_first(metadata, *SPONSOR_KEYS)
            note = get_joined(metadata, *NOTE_KEYS)
            related_url = get_first(metadata, *RELATED_URL_KEYS)
            pdf_url = pick_pdf_url(html, metadata)
            keywords = split_keywords(
                get_all(metadata, "DCTERMS.subject", "citation_keywords", "Efnisorð", "dc.subject")
            )

            descriptions = get_all(metadata, "DCTERMS.description")
            description_abstract_is, descriptions = extract_icelandic_abstract(descriptions)
            if description_abstract_is and not abstract_is:
                abstract_is = description_abstract_is

            extracted_note, _ = extract_notes(descriptions)
            note = merge_note_values(note, extracted_note)

            con.execute("delete from thesis_metadata where thesis_id = ?", [thesis_id])
            con.execute("delete from thesis_people where thesis_id = ?", [thesis_id])
            con.execute("delete from thesis_keywords where thesis_id = ?", [thesis_id])
            con.execute(
                """
                insert into thesis_metadata (thesis_id,
                                             title_is,
                                             title_en,
                                             abstract_is,
                                             abstract_en,
                                             degree_level,
                                             thesis_type,
                                             sponsor,
                                             note,
                                             related_url,
                                             raw_keywords,
                                             pdf_url,
                                             institution,
                                             school,
                                             university,
                                             faculty,
                                             study_category,
                                             thesis_type_label)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    thesis_id,
                    title_is,
                    title_en,
                    abstract_is,
                    abstract_en,
                    degree_level,
                    thesis_type,
                    sponsor,
                    note,
                    related_url,
                    "; ".join(keywords) if keywords else None,
                    pdf_url,
                    institution,
                    school,
                    university,
                    faculty,
                    study_category,
                    thesis_type_label,
                ],
            )

            authors = get_all(
                metadata,
                "DCTERMS.creator",
                "citation_author",
                "Höfundur",
                "Höfundar",
                "Author",
                "dc.contributor.author",
            )
            advisors = get_all(
                metadata,
                "Leiðbeinandi",
                "Leiðbeinendur",
                "Advisor",
                "dc.contributor.advisor",
            )

            authors = split_people_values(authors)
            advisors = split_people_values(advisors)

            author_people = [parse_person_name(a) for a in authors]
            advisor_people = [parse_person_name(a) for a in advisors]

            insert_people_links(con, thesis_id, author_people, "author")
            insert_people_links(con, thesis_id, advisor_people, "advisor")
            insert_keyword_links(con, thesis_id, keywords)
            loaded += 1

    return loaded


def main() -> None:
    args = parse_args()
    load_metadata(
        db=args.db,
        ids=args.ids,
        urls=args.urls,
        out_html=args.out_html,
        user_agent=args.user_agent,
        delay=args.delay,
    )


if __name__ == "__main__":
    main()
