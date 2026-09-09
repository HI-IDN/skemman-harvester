# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.0.0]: https://github.com/HI-IDN/skemman-harvester/releases/tag/v1.0.0
