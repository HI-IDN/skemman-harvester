# skemman-harvester

[![CI](https://github.com/HI-IDN/skemman-harvester/actions/workflows/ci.yml/badge.svg)](https://github.com/HI-IDN/skemman-harvester/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)

Harvest Icelandic thesis records from [Skemman](https://skemman.is) into a DuckDB
database — **any collection, any year, any degree level**.

Skemman is the shared repository of the Icelandic universities. This reads what it already
publishes: the search listings, the item pages, and the title page of each open PDF. Point
it at a collection handle and it builds a queryable database of what is there.

```bash
skemman oai-pmh --location 1946/2064 --year-start 2010 --year-end 2026
skemman metadata-load
skemman files-index
skemman titlepage-load --degree-level master
```

## What it deliberately does not do

- **No logins, no credentials.** Only what Skemman serves publicly.
- **No bypassing access rules.** A thesis the item page marks `Lokaður` is recorded as
  closed and never requested. Embargo dates are respected because they are simply obeyed.
- **No hammering.** One request at a time, with a delay of 30 seconds by default —
  what [skemman.is/robots.txt](https://skemman.is/robots.txt) asks for. Everything is
  cached, so a re-run fetches only what is missing.
- **No hoarding.** PDFs are deleted after their first pages are read to text, unless you
  ask to keep them. A full master's harvest costs about 20 MB on disk rather than 12 GB.

> [!WARNING]
> `oai-pmh` lists records through `/oai/request`, the standard repository-harvesting
> interface. The legacy `simple-search` command remains for comparison, but it builds URLs
> under `/simple-search`, which [robots.txt](https://skemman.is/robots.txt) disallows. See
> [Crawling etiquette](https://hi-idn.github.io/skemman-harvester/etiquette.html).

## Install

```bash
pip install git+https://github.com/HI-IDN/skemman-harvester.git
```

For development:

```bash
git clone git@github.com:HI-IDN/skemman-harvester.git
cd skemman-harvester
pip install -e ".[dev]"
pytest
```

## The four steps

Each is resumable and each caches what it fetches.

### 1. `oai-pmh` — what exists

Reads a collection's OAI-PMH set into the `thesis` table.

```bash
skemman oai-pmh --location 1946/2064 --year-start 2010 --year-end 2026
```

Collection handles map to Skemman's OAI-PMH community sets, so `1946/2064` becomes
`com_1946_2064`. Use `--set` directly to harvest a specific OAI-PMH set.

### 2. `metadata-load` — the item pages

Fetches each item page and parses it into normalized tables: titles, abstracts, degree
level, keywords, authors and advisors. Raw HTML is cached under `data/raw/items/`, so a
re-run reuses it.

```bash
skemman metadata-load
```

### 3. `files-index` — what is attached, and whether it is open

Reads the file table on each cached item page: filename, size, access status and type.
No network at all — it works off the HTML step 2 already saved.

This is what makes step 4 cheap. It knows a file's size and whether it is open before
deciding to request it.

```bash
skemman files-index
```

### 4. `titlepage-load` — what the document itself says

Repository keywords are a suggestion. The title page states the faculty, the credits and
the degree outright, and carries fields the metadata does not expose at all — ECTS, and
the length of the thesis.

```bash
skemman titlepage-load --degree-level master
```

Closed files are skipped, small files go first, and only the first pages are kept, as
plain text under `data/raw/pdf_text/`. Anything that yields nothing is recorded in
`thesis_titlepage_failure` and in `logs/titlepage.log` with its handle URL and the reason,
separating the permanent cases from the ones worth retrying.

Because the text is cached, improving the parser costs no downloads:

```bash
duckdb thesis.db -c "drop table thesis_titlepage"
skemman titlepage-load
```

## Configuration

`config/collections.yaml`:

```yaml
base_url: "https://skemman.is"
user_agent: "skemman-scraper/0.1 research crawler; contact: you@example.is"
request_delay_seconds: 2.0
timeout_seconds: 30
handles:
  HI: "1946/2064"
  HR: "1946/6870"
```

Put a real contact address in `user_agent`. It is the courtesy that makes a crawler
identifiable to the people running the server.

## Used by

- [skemman-msc](https://github.com/HI-IDN/skemman-msc) — a study of master's theses in
  engineering and technology at HÍ and HR since 2010.

## License

MIT. See [LICENSE](LICENSE).
