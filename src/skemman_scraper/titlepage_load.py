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
# thousands of lines scrolling over the progress bar, and none of it is
# actionable: the text still extracts. Real errors are still shown.
logging.getLogger("pypdf").setLevel(logging.ERROR)


class _UnimplementedEncoding(logging.Filter):
    """Drop pypdf's "not implemented yet" notes, whatever level it logs them at.

    "Advanced encoding /SymbolSetEncoding not implemented yet" goes through
    pypdf's `logger_error`, so raising the level does not silence it, and it
    arrives once per font on any document that sets a heading in a symbol font.
    The page still extracts -- the note only says a glyph table went unmapped.

    pypdf logs under the name of the module that spoke, so this has to be
    attached to `pypdf._cmap` itself: a filter on the parent `pypdf` logger is
    never consulted for records a child logger creates. Filtering the message
    rather than muting the logger keeps genuine cmap errors visible.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "not implemented yet" not in str(record.msg)


logging.getLogger("pypdf._cmap").addFilter(_UnimplementedEncoding())

PAGES = 20

# The cached text carries the PDF's page count on a first marker line, so a
# re-parse does not need the PDF back just to know how long the thesis was.
PAGE_MARKER = "%%PAGES	"

# --- title page fields -----------------------------------------------------

LETTERS = "A-Za-zÁÉÍÓÚÝÞÆÖÐáéíóúýþæöð"
PATTERN_TAIL = r"\s+([^\n]{3,70})"
PATTERN_EN_STOP = r"\s+(?:university of iceland|reykjav[ií]k university|háskól\w+)\b.*$"

# "Faculty of Physical Sciences University of Iceland" -- the unit runs until the
# university's own name starts, not to the end of the line.
_EN_TAIL = re.compile(r"(?i)" + PATTERN_EN_STOP)

_PREFIXED = {
    "faculty": re.compile(r"(?i)faculty of" + PATTERN_TAIL),
    "school": re.compile(r"(?i)school of" + PATTERN_TAIL),
    "department": re.compile(r"(?i)department of" + PATTERN_TAIL),
}

# An Icelandic unit name: "Umhverfis- og byggingarverkfraedideild",
# "Idnadarverkfraedi-, velaverkfraedi- og tolvunarfraedideild".
#
# Three things this has to allow, each of which cost real coverage when it did
# not. The line is not the name: "HASKOLI ISLANDS Jardvisindadeild" puts the
# university in front of it, so the pattern searches rather than anchors. The
# join is "- og " -- a hyphen, a space and the word -- not a single character.
# And the name is often inflected: "Raunvisindadeildar", "skor" for the older
# sub-units, "sviods" for a school.
_UNIT_WORD = "(?:deild(?:ar|in|inni|arinnar)?|svið[si]?|skor(?:ar|inni)?)"
_UNIT = re.compile(
    # The fragment must end on a hyphen or a comma. Letting it end on nothing
    # made the prefix and the head noun able to match the same characters,
    # and the pattern went quadratic on long lines: the suite took a minute.
    "(?i)((?:[" + LETTERS + r"]{2,}[-,]+\s*(?:og\s+)?){0,4}"
    "[" + LETTERS + "]{2,}" + _UNIT_WORD + ")",
)

_ECTS = re.compile(r"(?i)\b(\d{1,3})\s*ECTS\b")
_DEGREE = re.compile(
    r"(?i)\b(Magister Scientiarum|Master of Science|Master of Arts|"
    r"Master of Engineering|Master of Project Management|Magister Paedagogiae)\b"
)
# English "degree in X" and Icelandic "meistaraprofs (MSc) i X".
# Two English forms. The template most theses follow splits them over two
# lines -- "...for the degree of" then "Master of Science in X" -- so the
# degree name itself has to introduce the subject, not just "degree in".
# 164 of the 171 theses using that wording were losing their subject.
_SUBJECT_EN = re.compile(
    r"(?i)\b(?:degree in|Master of (?:Science|Arts|Engineering|Project Management) in)\s+"
    r"([A-Za-zÁÉÍÓÚÝÞÆÖÐáéíóúýþæöð&,\- ]{3,60})"
)
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
            if m := pattern.search(ln):
                out[key] = _EN_TAIL.sub("", m.group(1)).strip(" ,.")
                break

    # The same pattern finds both; which column it lands in is decided by the
    # word it ends on, so "Verkfraedi- og natturuvisindasvid" is a svid and
    # "Jardvisindadeild" a deild without needing two near-identical patterns.
    for ln in lines:
        for m in _UNIT.finditer(ln):
            name = " ".join(m.group(1).split())
            if len(name) < 9:
                continue
            key = "svid" if re.search(r"(?i)svið[si]?$", name) else "deild"
            out.setdefault(key, name)
        if "deild" in out and "svid" in out:
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


def _split_marker(raw: str) -> tuple[str, int | None, int | None]:
    """Split the cached marker off the stored text.

    The marker carries the document total page count and how many of those
    pages were actually extracted: `%%PAGES\t106\t20`. Caches written
    before the second field existed report `None` for it, which makes them
    stale -- there is no way to tell how much of such a file was kept.
    """
    if raw.startswith(PAGE_MARKER):
        head, _, rest = raw.partition("\n")
        fields = head[len(PAGE_MARKER):].split("\t")
        try:
            total = int(fields[0])
        except (ValueError, IndexError):
            return rest, None, None
        try:
            kept = int(fields[1])
        except (ValueError, IndexError):
            kept = None
        return rest, total, kept
    return raw, None, None


def _is_stale(total: int | None, kept: int | None, pages: int | None) -> bool:
    """Does this cache hold fewer pages than the caller is asking for?"""
    if total is None or kept is None:
        return True
    wanted = total if pages is None else min(pages, total)
    return kept < wanted


class TitlepageError(Exception):
    """A thesis that could not be turned into text, with why and whether to retry."""

    def __init__(self, reason: str, *, permanent: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.permanent = permanent



# A title page that states the degree is a title page we read the right file
# for. Skemman's items often carry several PDFs and the first open one is not
# always the thesis -- a declaration form, a cover sheet, an appendix. When the
# first file says nothing about a degree, the others are worth a look.
#
# The English words alone are not enough: 216 of the cached texts say only
# "meistara" and would be thrown away by an `msc|master` test.
DEGREE_PATTERN = re.compile(
    r"master|magister|meistara|\bm\.?\s?sc\b",
    re.I,
)

_BITSTREAM_SEQ = re.compile(r"/bitstream/\d+/\d+/(\d+)/")


def bitstream_seq(url: str | None) -> int:
    """The sequence number DSpace gives a file: /bitstream/1946/53215/2/name.pdf."""
    m = _BITSTREAM_SEQ.search(url or "")
    return int(m.group(1)) if m else 9999


def states_a_degree(text: str | None) -> bool:
    """Does this text look like a title page rather than a form or an appendix?"""
    return bool(text and DEGREE_PATTERN.search(text))


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
        text, n_pages, kept = _split_marker(
            text_path.read_text(encoding="utf-8", errors="replace")
        )
        # A cache holding fewer pages than asked for is fetched again rather
        # than returned short: raising `titlepage_pages` has to take effect.
        if not _is_stale(n_pages, kept, pages):
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
    kept = n_pages if pages is None else min(pages, n_pages)
    text_path.write_text(
        f"{PAGE_MARKER}{n_pages}\t{kept}\n{text}", encoding="utf-8"
    )

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

        where = ["f.pdf_url is not null"]
        params: list[object] = []
        if ids:
            wanted = [int(x) for x in ids.split(",") if x.strip()]
            where.append(f"m.thesis_id in ({','.join('?' * len(wanted))})")
            params.extend(wanted)
        else:
            # An unknown degree level is not a bachelor's. 841 records carry
            # no level at all, and master's theses are certainly among them --
            # cheaper to read a title page that turns out to be the wrong level
            # than to drop a thesis that belongs in the population. The level
            # the document itself states is what the analysis trusts anyway.
            where.append("(m.degree_level = ? or m.degree_level is null)")
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
            where.append("coalesce(f.open_size, f.any_size, 0) <= ?")
            params.append(max_bytes)
        if not include_closed:
            # A thesis with no indexed files is still worth trying; one whose
            # PDFs are all closed is not. Aggregating access with min() would
            # have excluded any thesis carrying a closed file alongside an open
            # one -- 'Lokadur' sorts before 'Opinn' -- which is most of them.
            where.append("(f.thesis_id is null or f.open_pdfs > 0)")

        sql = rf"""
            select m.thesis_id, f.pdf_url
            from thesis_metadata m
            left join thesis_titlepage p on p.thesis_id = m.thesis_id
            left join (
                -- One row per thesis: the file that is the thesis itself, and
                -- what is known about getting hold of it.
                --
                -- `role` is set where the file table came from xoai, which is
                -- DSpace saying outright which attachment is COMPLETE_TEXT and
                -- which is the DECLARATION form. The description and the size
                -- are only fallbacks, for rows indexed before that existed --
                -- and size is a poor one: a one-page scanned declaration is
                -- often the larger file.
                --
                -- Access is a three-state thing here. 'Opinn' is open.
                -- "Lokadur til 13.06.2026" is an embargo with an end date, and
                -- the record keeps saying so long after it lapsed, so a date
                -- in the past counts as open. Null means nobody has looked:
                -- xoai does not carry the access status at all. Unknown is
                -- treated as worth trying -- the fetch either works or is
                -- recorded as a failure, which is how the status gets known.
                select thesis_id,
                       url as pdf_url,
                       case when is_open then 1 else 0 end as open_pdfs,
                       case when is_open then size_bytes end as open_size,
                       size_bytes as any_size
                from (
                    select thesis_id,
                           url,
                           size_bytes,
                           (
                               access is null
                               or access = 'Opinn'
                               or try_strptime(
                                   regexp_extract(access, '(\d{2}\.\d{2}\.\d{4})', 1),
                                   '%d.%m.%Y'
                               )::date <= current_date
                           ) as is_open,
                           row_number() over (
                               partition by thesis_id
                               order by
                                   case when role = 'primary' then 0 else 1 end,
                                   case when description = 'Heildartexti' then 0 else 1 end,
                                   case when lower(coalesce(description, ''))
                                             in ('yfirlýsing', 'yfirlysing')
                                        then 1 else 0 end,
                                   size_bytes desc nulls last
                           ) as rn
                    from thesis_file
                    where filetype = 'PDF'
                )
                where rn = 1
            ) f on f.thesis_id = m.thesis_id
            where {' and '.join(where)}
            order by coalesce(f.open_size, f.any_size, 9223372036854775807), m.thesis_id
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
