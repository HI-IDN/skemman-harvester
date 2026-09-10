# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] — 2026-09-10

### Removed

- **`simple-search` is gone.** It built URLs under `/simple-search`, which
  [robots.txt](https://skemman.is/robots.txt) disallows.
- The item-page scraper, `load_metadata()` and its `python -m` entry point. OAI-PMH
  replaced it for the record and xoai for the files, and no `skemman` command called it.

### Added

- **`oai-pmh`** harvests a collection over OAI-PMH at `/oai/request`, the interface a
  DSpace repository publishes for reading records out of it, and not disallowed. Listing
  pages are cached by metadata prefix, set and offset, and an interrupted harvest resumes
  from its checkpoint. Subjects and abstracts are loaded from the records themselves.
  Handles, years and the record limit are read from the config.
- **`files-load`** fills `thesis_file` from the `xoai` metadata format. xoai is DSpace's
  own format and it lists every attached file with its name, size, MIME type, download URL
  and the bundle description DSpace filed it under, `COMPLETE_TEXT` or `DECLARATION` —
  the repository itself saying which attachment is the thesis and which is the form the
  student signs. Harvest the pages with `oai-pmh --metadata-prefix xoai` and this replays
  them offline. About 66 requests for a whole collection, where `files-index` reads the
  same thing off item pages at one request each — 6291 of them on Skemman's engineering
  collections.
- `role` on `thesis_file`: `primary` for the file that is the thesis, `secondary` for
  everything else. A database created before the column existed gains it on the next run.

### Changed

- **`titlepage-load` takes the PDF URL from `thesis_file`, not `thesis_metadata.pdf_url`.**
  `pdf_url` was only ever filled by the item-page scraper, which nothing has called since
  OAI-PMH replaced it, so on a database built from scratch the column was empty and the
  loader selected nothing at all.
- The download queue picks one file per thesis by `role`, falling back to the description
  and only then to size. Size alone is wrong on 469 of 2410 multi-file items, where the
  scanned declaration form is larger than the thesis.
- An unknown access status counts as worth trying. xoai does not carry access at all, and
  a fetch that fails is recorded like any other; `Opinn` and a lapsed embargo still count
  as open, and a live embargo still does not.
- `titlepage-load --degree-level master` now also takes records with no degree level.
  841 of Skemman's records state none, and master's theses are among them.

## [1.1.0] — 2026-09-09

### Changed

- **Default request delay is now 30 seconds, up from 2.** That is what
  [skemman.is/robots.txt](https://skemman.is/robots.txt) asks for with `Crawl-delay: 30`.
  The previous default ignored it.

### Documented

- `simple-search` builds URLs under `/simple-search`, which robots.txt disallows. This is
  now stated in the README, in the documentation and in a comment at the code that does
  it. Skemman exposes OAI-PMH at `/oai/request`, which is not disallowed and is the
  standard interface for this; replacing `simple-search` with it is tracked as an issue.
- The etiquette page claimed the tool was well-behaved without having checked robots.txt.
  It now quotes it and says plainly where the tool departs from it.

## [1.0.0] — 2026-09-09

First release as a standalone package. Extracted from
[skemman-msc](https://github.com/HI-IDN/skemman-msc), where it grew as
`skemman-scraper`, and renamed: Skemman is a DSpace repository, and *harvest* is that
world's word for reading records out of one.

### Added

- `files-index` — reads each item page's file table into `thesis_file`: filename, size,
  access status and type. No network; it works off the HTML `metadata-load` already
  cached.
- `titlepage-load` — fetches open thesis PDFs, keeps the first pages as plain text, and
  reads the faculty, credits, degree, committee and page count into `thesis_titlepage`.
  Repository keywords are a suggestion; the title page states these outright, and carries
  ECTS and thesis length, which Skemman's metadata does not expose at all.
- `thesis_titlepage_failure` and `logs/titlepage.log` record why a thesis produced
  nothing, with its handle URL, separating permanent failures from ones worth retrying.
- Download queue planning: closed files are never requested, and small files go first.
- `--max-mb`, `--include-closed`, `--retry-failed`, `--keep-pdf`, `--limit`, `--ids`.
- Tests covering the title page parser, the file table parser and size labels. No network,
  no database.
- Documentation site at <https://hi-idn.github.io/skemman-harvester/>.

### Fixed

- Icelandic unit names containing `ð` never matched. The character classes listed the
  accented vowels but not eth, so *Umhverfis- og byggingarverkfræðideild* — a real
  department — was silently losing its deild.
- A PDF that could not be read was cached as an empty file, so the thesis was skipped
  silently on every later run and never got a row. Failed reads are no longer cached.
- AES-encrypted PDFs failed to open. `cryptography` is now a declared dependency.
- Truncated downloads are detected against `Content-Length` where the header is present,
  and retried once.
- `pypdf` font and cross-reference warnings no longer scroll over the progress bar.

### Changed

- Renamed from `skemman-scraper` to `skemman-harvester`. The CLI command stays `skemman`.
- Progress uses `tqdm` throughout, matching what `simple-search` already did.

[1.2.0]: https://github.com/HI-IDN/skemman-harvester/releases/tag/v1.2.0
[1.1.0]: https://github.com/HI-IDN/skemman-harvester/releases/tag/v1.1.0
[1.0.0]: https://github.com/HI-IDN/skemman-harvester/releases/tag/v1.0.0
