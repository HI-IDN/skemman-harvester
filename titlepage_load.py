"""Read the ground truth off a thesis title page.

Subject keywords in Skemman are a suggestion. The title page states the faculty,
the credits and the degree outright, so it is the authority when the two
disagree.

Only the first few pages are kept, as text. A cached `.txt` means the PDF is
never fetched again, and re-parsing with better patterns costs nothing.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
import pypdf
from tqdm import tqdm

# pypdf narrates every font and xref oddity it meets. Thousands of theses means
# thousands of lines of "Advanced encoding /SymbolSetEncoding not implemented
# yet" scrolling over the progress bar, and none of it is actionable: the text
# still extracts. Errors are still shown.
logging.getLogger("pypdf").setLevel(logging.ERROR)

from .config import load_config
from .utils import PoliteSession

PAGES = 8

# The cached text carries the PDF's page count on a first marker line, so a
# re-parse does not need the PDF back just to know how long the thesis was.
PAGE_MARKER = "%%PAGES	"

# --- title page fields -----------------------------------------------------

_PREFIXED = {
    "faculty": re.compile(r"(?i)^faculty of\s+(.{3,70})$"),
    "school": re.compile(r"(?i)^school of\s+(.{3,70})$"),
    "department": re.compile(r"(?i)^department of\s+(.{3,70})$"),
}

# An Icelandic unit name: "Umhverfis- og byggingarverkfraedideild".
_DEILD = re.compile(
    r"(?i)^([A-ZÁÉÍÓÚÝÞÆÖ][A-Za-zÁÉÍÓÚÝÞÆÖáéíóúýþæö\-]{2,}"
    r"(?:[-\s]og[-\s][A-Za-zÁÉÍÓÚÝÞÆÖáéíóúýþæö\-]+)*deild)\s*$"
)
_SVID = re.compile(
    r"(?i)^([A-ZÁÉÍÓÚÝÞÆÖ][A-Za-zÁÉÍÓÚÝÞÆÖáéíóúýþæö\-]{2,}"
    r"(?:[-\s]og[-\s][A-Za-zÁÉÍÓÚÝÞÆÖáéíóúýþæö\-]+)*svið)\s*$"
)

_ECTS = re.compile(r"(?i)\b(\d{1,3})\s*ECTS\b")
_DEGREE = re.compile(
    r"(?i)\b(Magister Scientiarum|Master of Science|Master of Arts|"
    r"Master of Engineering|Master of Project Management|Magister Paedagogiae)\b"
)
# English "degree in X" and Icelandic "meistaraprofs (MSc) i X".
_SUBJECT_EN = re.compile(r"(?i)\bdegree in\s+([A-Za-zÁÉÍÓÚÝÞÆÖáéíóúýþæö&,\- ]{3,60})")
_SUBJECT_IS = re.compile(
    r"(?i)meistarapr[óo]fs?\s*(?:\([^)]*\))?\s*í\s+([A-Za-zÁÉÍÓÚÝÞÆÖáéíóúýþæö&,\- ]{3,60})"
)
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")

# Headings that introduce a list of people.
_ROLES = {
    "committee": re.compile(r"(?i)^(ms|msc|m\.s\.|master'?s?)\s*committee\s*:?$"),
    "examiner": re.compile(r"(?i)^(master'?s? examiner|prófdómari)\s*:?$"),
    "advisors": re.compile(
        r"(?i)^(advisors?|supervisors?|leiðbeinand[iu]r?|leiðbeinendur|umsjónarkennari)\s*:?$"
    ),
}

# A person line ends where an institution, a role or a place begins.
_STOP = re.compile(
    r"(?i)^(faculty|school|department|university|háskóli|reykjav[ií]k|akureyri|"
    r"master|ms committee|advisor|supervisor|leiðbein|examiner|prófdómari|"
    r"faculty representative|fulltrúi)|(deild|svið)\s*$"
)
_NAME = re.compile(r"^[A-ZÁÉÍÓÚÝÞÆÖ][^\d]{4,60}$")


def _lines(text: str) -> list[str]:
    out = (re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines())
    return [ln for ln in out if ln]


def _people(lines: list[str], head: re.Pattern[str]) -> list[str]:
    for i, ln in enumerate(lines):
        if not head.match(ln):
            continue
        names: list[str] = []
        for nxt in lines[i + 1:]:
            if _STOP.search(nxt) or any(h.match(nxt) for h in _ROLES.values()):
                break
            if not _NAME.match(nxt):
                break
            names.append(nxt)
        return names
    return []


def parse_titlepage(text: str) -> dict[str, object]:
    """Pull the stated faculty, credits, degree and people off a title page."""
    lines = _lines(text)
    out: dict[str, object] = {}

    for key, pattern in _PREFIXED.items():
        for ln in lines:
            if m := pattern.match(ln):
                out[key] = m.group(1).strip(" ,.")
                break

    for key, pattern in (("deild", _DEILD), ("svid", _SVID)):
        for ln in lines:
            if m := pattern.match(ln):
                out[key] = m.group(1)
                break

    joined = " ".join(lines)
    if m := _ECTS.search(joined):
        out["ects"] = int(m.group(1))
    if m := _DEGREE.search(joined):
        out["degree"] = m.group(1)

    # Read the subject off its own line so it cannot run into the next heading.
    for ln in lines:
        for pattern in (_SUBJECT_EN, _SUBJECT_IS):
            if m := pattern.search(ln):
                out["subject"] = m.group(1).strip(" ,.")
                break
        if "subject" in out:
            break

    for role, head in _ROLES.items():
        if names := _people(lines, head):
            out[role] = "; ".join(names)

    if years := _YEAR.findall(" ".join(lines[-15:])):
        out["year_on_page"] = int(years[-1])

    return out


# --- pdf handling ----------------------------------------------------------


def extract_text(pdf: Path, pages: int = PAGES) -> tuple[str, int]:
    """Return the first pages as text, plus the document's total page count."""
    reader = pypdf.PdfReader(str(pdf))
    total = len(reader.pages)
    text = "\n".join((reader.pages[i].extract_text() or "") for i in range(min(pages, total)))
    return text, total


def _split_marker(raw: str) -> tuple[str, int | None]:
    """Split the cached page-count marker off the stored text."""
    if raw.startswith(PAGE_MARKER):
        head, _, rest = raw.partition("\n")
        try:
            return rest, int(head[len(PAGE_MARKER):])
        except ValueError:
            return rest, None
    return raw, None


def _ensure_text(
    thesis_id: int,
    url: str,
    text_dir: Path,
    pdf_dir: Path,
    session: PoliteSession,
    keep_pdf: bool,
) -> tuple[str | None, int | None]:
    """Return the cached title-page text and page count, fetching only if needed."""
    text_path = text_dir / f"{thesis_id}.txt"
    if text_path.exists():
        text, n_pages = _split_marker(text_path.read_text(encoding="utf-8", errors="replace"))
        return text or None, n_pages

    pdf_path = pdf_dir / f"{thesis_id}.pdf"
    fetched_now = False
    if not pdf_path.exists():
        session.download_binary(url, pdf_path)
        fetched_now = True

    try:
        text, n_pages = extract_text(pdf_path)
    except Exception:  # noqa: BLE001 - a broken PDF should not stop the run
        # Nothing is cached: a failed read is usually a truncated download, and
        # caching it would skip the thesis silently on every later run.
        if fetched_now and not keep_pdf:
            pdf_path.unlink(missing_ok=True)
        return None, None

    # An empty text with a good page count means a scanned PDF. That is a real
    # answer, so it is cached -- otherwise every run would fetch it again.
    text_dir.mkdir(parents=True, exist_ok=True)
    text_path.write_text(f"{PAGE_MARKER}{n_pages}\n{text}", encoding="utf-8")

    # The text is what we keep; the PDF can always be fetched again.
    if fetched_now and not keep_pdf:
        pdf_path.unlink(missing_ok=True)

    return text or None, n_pages


def _create_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        create table if not exists thesis_titlepage (
            thesis_id    integer,
            faculty      varchar,
            school       varchar,
            department   varchar,
            deild        varchar,
            svid         varchar,
            ects         integer,
            degree       varchar,
            subject      varchar,
            committee    varchar,
            examiner     varchar,
            advisors     varchar,
            year_on_page integer,
            n_pages      integer,
            text_chars   integer
        )
        """
    )
    con.execute(
        "create unique index if not exists thesis_titlepage_pk "
        "on thesis_titlepage (thesis_id)"
    )


def load_titlepages(
    db: Path,
    limit: int | None = None,
    ids: str | None = None,
    text_dir: Path = Path("data/raw/pdf_text"),
    pdf_dir: Path = Path("data/raw/pdfs"),
    config: Path = Path("config/collections.yaml"),
    keep_pdf: bool = False,
    degree_level: str = "master",
) -> tuple[int, int]:
    """Load title-page fields for theses that do not have them yet.

    Returns (processed, with_faculty).
    """
    cfg = load_config(config)
    session = PoliteSession(
        user_agent=cfg.get("user_agent", "skemman-scraper"),
        delay_seconds=float(cfg.get("request_delay_seconds", 2.0)),
        timeout_seconds=int(cfg.get("timeout_seconds", 30)),
    )

    text_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(db)) as con:
        _create_table(con)

        where = ["m.pdf_url is not null"]
        params: list[object] = []
        if ids:
            wanted = [int(x) for x in ids.split(",") if x.strip()]
            where.append(f"m.thesis_id in ({','.join('?' * len(wanted))})")
            params.extend(wanted)
        else:
            where.append("m.degree_level = ?")
            params.append(degree_level)
            where.append("p.thesis_id is null")

        sql = f"""
            select m.thesis_id, m.pdf_url
            from thesis_metadata m
            left join thesis_titlepage p on p.thesis_id = m.thesis_id
            where {' and '.join(where)}
            order by m.thesis_id
        """
        if limit:
            sql += f" limit {int(limit)}"

        rows = con.execute(sql, params).fetchall()

        processed = with_faculty = 0
        bar = tqdm(rows, desc="Reading title pages", unit="thesis")
        for thesis_id, url in bar:
            try:
                text, n_pages = _ensure_text(
                    thesis_id, url, text_dir, pdf_dir, session, keep_pdf
                )
            except Exception:  # noqa: BLE001 - restricted or missing files are expected
                continue
            if not text and n_pages is None:
                continue

            # A scanned PDF yields no text but still has a real page count, so the
            # row is written with whatever could be read.
            fields = parse_titlepage(text or "")
            fields["thesis_id"] = thesis_id
            fields["n_pages"] = n_pages
            fields["text_chars"] = len(text or "")

            columns = [
                "thesis_id", "faculty", "school", "department", "deild", "svid",
                "ects", "degree", "subject", "committee", "examiner", "advisors",
                "year_on_page", "n_pages", "text_chars",
            ]
            con.execute("delete from thesis_titlepage where thesis_id = ?", [thesis_id])
            con.execute(
                f"insert into thesis_titlepage ({', '.join(columns)}) "
                f"values ({', '.join('?' * len(columns))})",
                [fields.get(c) for c in columns],
            )
            processed += 1
            if fields.get("faculty") or fields.get("deild"):
                with_faculty += 1
            bar.set_postfix(parsed=processed, faculty=with_faculty)

        con.execute("checkpoint")

    return processed, with_faculty
