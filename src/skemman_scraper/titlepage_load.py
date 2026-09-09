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
from datetime import datetime
from pathlib import Path

import duckdb
import pypdf
from tqdm import tqdm

from .config import load_config
from .utils import PoliteSession

# pypdf narrates every font and xref oddity it meets. Thousands of theses means
# thousands of lines of "Advanced encoding /SymbolSetEncoding not implemented
# yet" scrolling over the progress bar, and none of it is actionable: the text
# still extracts. Errors are still shown.
logging.getLogger("pypdf").setLevel(logging.ERROR)

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
    r"(?i)^([A-ZÁÉÍÓÚÝÞÆÖÐ][A-Za-zÁÉÍÓÚÝÞÆÖÐáéíóúýþæöð\-]{2,}"
    r"(?:[-\s]og[-\s][A-Za-zÁÉÍÓÚÝÞÆÖÐáéíóúýþæöð\-]+)*deild)\s*$"
)
_SVID = re.compile(
    r"(?i)^([A-ZÁÉÍÓÚÝÞÆÖÐ][A-Za-zÁÉÍÓÚÝÞÆÖÐáéíóúýþæöð\-]{2,}"
    r"(?:[-\s]og[-\s][A-Za-zÁÉÍÓÚÝÞÆÖÐáéíóúýþæöð\-]+)*svið)\s*$"
)

_ECTS = re.compile(r"(?i)\b(\d{1,3})\s*ECTS\b")
_DEGREE = re.compile(
    r"(?i)\b(Magister Scientiarum|Master of Science|Master of Arts|"
    r"Master of Engineering|Master of Project Management|Magister Paedagogiae)\b"
)
# English "degree in X" and Icelandic "meistaraprofs (MSc) i X".
_SUBJECT_EN = re.compile(r"(?i)\bdegree in\s+([A-Za-zÁÉÍÓÚÝÞÆÖÐáéíóúýþæöð&,\- ]{3,60})")
_SUBJECT_IS = re.compile(
    r"(?i)meistarapr[óo]fs?\s*(?:\([^)]*\))?\s*í\s+([A-Za-zÁÉÍÓÚÝÞÆÖÐáéíóúýþæöð&,\- ]{3,60})"
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


def extract_text(pdf: Path, pages: int | None = PAGES) -> tuple[str, int]:
    """Return the first pages as text, plus the document's total page count."""
    reader = pypdf.PdfReader(str(pdf))
    total = len(reader.pages)
    page_count = total if pages is None else min(pages, total)
    text = "\n".join((reader.pages[i].extract_text() or "") for i in range(page_count))
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


class TitlepageError(Exception):
    """A thesis that could not be turned into text, with why and whether to retry."""

    def __init__(self, reason: str, *, permanent: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.permanent = permanent


def _ensure_text(
    thesis_id: int,
    url: str,
    text_dir: Path,
    pdf_dir: Path,
    session: PoliteSession,
    keep_pdf: bool,
    pages: int | None,
) -> tuple[str | None, int | None, bool]:
    """Return the cached title-page text, page count, and whether it came from cache."""
    text_path = text_dir / f"{thesis_id}.txt"
    if text_path.exists():
        text, n_pages = _split_marker(text_path.read_text(encoding="utf-8", errors="replace"))
        return text or None, n_pages, True

    pdf_path = pdf_dir / f"{thesis_id}.pdf"
    fetched_now = False
    if not pdf_path.exists():
        try:
            session.download_binary(url, pdf_path)
        except Exception as exc:  # noqa: BLE001 - HTTP and network faults both land here
            status = getattr(getattr(exc, "response", None), "status_code", None)
            # 403/404/410 mean the file is closed or gone: fetching it again will
            # never help, so it is recorded and skipped from now on.
            raise TitlepageError(
                f"download failed: {type(exc).__name__} {status or ''}".strip(),
                permanent=status in (403, 404, 410),
            ) from exc
        fetched_now = True

        # A restricted item can answer 200 with a login page rather than a PDF.
        if pdf_path.read_bytes()[:5] != b"%PDF-":
            if not keep_pdf:
                pdf_path.unlink(missing_ok=True)
            raise TitlepageError("not a PDF (restricted or a landing page)", permanent=True)

    try:
        text, n_pages = extract_text(pdf_path, pages=pages)
    except Exception as exc:  # noqa: BLE001 - a broken PDF should not stop the run
        # Not cached: a failed read is usually a truncated download, so it is
        # worth one more try on a later run.
        if fetched_now and not keep_pdf:
            pdf_path.unlink(missing_ok=True)
        raise TitlepageError(
            f"unreadable PDF: {type(exc).__name__}: {str(exc)[:120]}", permanent=False
        ) from exc

    # An empty text with a good page count means a scanned PDF. That is a real
    # answer, so it is cached -- otherwise every run would fetch it again.
    text_dir.mkdir(parents=True, exist_ok=True)
    text_path.write_text(f"{PAGE_MARKER}{n_pages}\n{text}", encoding="utf-8")

    # The text is what we keep; the PDF can always be fetched again.
    if fetched_now and not keep_pdf:
        pdf_path.unlink(missing_ok=True)

    return text or None, n_pages, False


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
    # Why a thesis produced no text. `permanent` marks the ones worth skipping
    # for good -- a closed item will not open on the next run.
    con.execute(
        """
        create table if not exists thesis_titlepage_failure (
            thesis_id  integer,
            item_url   varchar,
            pdf_url    varchar,
            reason     varchar,
            permanent  boolean,
            failed_at  timestamp
        )
        """
    )
    con.execute(
        "create unique index if not exists thesis_titlepage_failure_pk "
        "on thesis_titlepage_failure (thesis_id)"
    )


def item_url(thesis_id: int) -> str:
    """The Skemman item page, matching the item_url column in v_thesis."""
    return f"https://skemman.is/handle/1946/{thesis_id}"


def _record_failure(
    con: duckdb.DuckDBPyConnection,
    log,  # noqa: ANN001 - a plain text handle
    thesis_id: int,
    url: str,
    reason: str,
    permanent: bool,
) -> None:
    """Write the failure to the log file and to the table that suppresses retries."""
    stamp = datetime.now()
    # The handle URL comes first: it opens the record a human can look at, while
    # the bitstream URL is the thing that actually failed.
    log.write(
        f"{stamp:%Y-%m-%d %H:%M:%S}\t{thesis_id}\t"
        f"{'permanent' if permanent else 'retry'}\t{reason}\t"
        f"{item_url(thesis_id)}\t{url}\n"
    )
    log.flush()
    con.execute("delete from thesis_titlepage_failure where thesis_id = ?", [thesis_id])
    con.execute(
        "insert into thesis_titlepage_failure "
        "(thesis_id, item_url, pdf_url, reason, permanent, failed_at) "
        "values (?, ?, ?, ?, ?, ?)",
        [thesis_id, item_url(thesis_id), url, reason, permanent, stamp],
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
    log_path: Path = Path("logs/titlepage.log"),
    retry_failed: bool = False,
    max_bytes: int | None = None,
    include_closed: bool = False,
    pages: int | None = None,
) -> tuple[int, int, int]:
    """Load title-page fields for theses that do not have them yet.

    Returns (processed, with_faculty, failed).
    """
    cfg = load_config(config)
    if pages is None:
        pages = cfg.get("titlepage_pages", PAGES)
    session = PoliteSession(
        user_agent=cfg.get("user_agent", "skemman-harvester"),
        delay_seconds=float(cfg.get("request_delay_seconds", 2.0)),
        timeout_seconds=int(cfg.get("timeout_seconds", 30)),
    )

    text_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")
    log.write(f"# run {datetime.now():%Y-%m-%d %H:%M:%S}\n")

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
            if not retry_failed:
                # A closed item will not open next time; do not spend a request on it.
                where.append(
                    "m.thesis_id not in "
                    "(select thesis_id from thesis_titlepage_failure where permanent)"
                )

        # The item page states every file's size and access, so the queue is
        # planned before touching the network: closed files are never requested,
        # and the small ones go first so most theses land early. Skemman serves
        # the very large files unreliably, so they are worth leaving till last.
        if max_bytes:
            where.append("coalesce(f.size_bytes, 0) <= ?")
            params.append(max_bytes)
        if not include_closed:
            where.append("(f.access is null or f.access = 'Opinn')")

        sql = f"""
            select m.thesis_id, m.pdf_url
            from thesis_metadata m
            left join thesis_titlepage p on p.thesis_id = m.thesis_id
            left join (
                select thesis_id, min(size_bytes) as size_bytes, min(access) as access
                from thesis_file
                where filetype = 'PDF'
                group by thesis_id
            ) f on f.thesis_id = m.thesis_id
            where {' and '.join(where)}
            order by coalesce(f.size_bytes, 9223372036854775807), m.thesis_id
        """
        if limit:
            sql += f" limit {int(limit)}"

        rows = con.execute(sql, params).fetchall()

        processed = with_faculty = failed = from_cache = 0
        bar = tqdm(rows, desc="Reading title pages", unit="thesis")
        for thesis_id, url in bar:
            try:
                text, n_pages, cached = _ensure_text(
                    thesis_id, url, text_dir, pdf_dir, session, keep_pdf, pages
                )
            except TitlepageError as exc:
                failed += 1
                _record_failure(con, log, thesis_id, url, exc.reason, exc.permanent)
                bar.set_postfix(parsed=processed, faculty=with_faculty, failed=failed)
                continue
            except Exception as exc:  # noqa: BLE001 - never let one thesis stop the run
                failed += 1
                _record_failure(
                    con, log, thesis_id, url, f"unexpected: {type(exc).__name__}", False
                )
                bar.set_postfix(parsed=processed, faculty=with_faculty, failed=failed)
                continue

            from_cache += cached
            if not text and n_pages is None:
                failed += 1
                _record_failure(con, log, thesis_id, url, "no text and no page count", False)
                bar.set_postfix(parsed=processed, faculty=with_faculty, failed=failed)
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
            con.execute("delete from thesis_titlepage_failure where thesis_id = ?", [thesis_id])
            processed += 1
            if fields.get("faculty") or fields.get("deild"):
                with_faculty += 1
            bar.set_postfix(
                parsed=processed, faculty=with_faculty, failed=failed, cached=from_cache
            )

        con.execute("checkpoint")

    log.write(
        f"# done {datetime.now():%Y-%m-%d %H:%M:%S}  "
        f"parsed={processed} from_cache={from_cache} failed={failed}\n"
    )
    log.close()

    return processed, with_faculty, failed
