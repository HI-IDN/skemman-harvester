from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import typer
from rich.console import Console

from .config import load_config
from .files_index import load_file_index
from .metadata_load import clean_people_table, load_metadata
from .oai_pmh import harvest_oai_pmh, set_spec_from_location
from .titlepage_load import load_titlepages

app = typer.Typer(help="Skemman thesis metadata loader")
console = Console()


def write_thesis_rows(df: pd.DataFrame, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            """
            create table if not exists thesis (
                id integer,
                date_accepted date,
                title varchar,
                authors varchar
            )
            """
        )
        con.register("df", df)
        con.execute(
            """
            insert into thesis (id, date_accepted, title, authors)
            select
                cast(df.id as integer),
                coalesce(
                    try_strptime(df.date_accepted, '%d.%m.%Y')::date,
                    try_strptime(df.date_accepted, '%Y-%m-%d')::date,
                    try_strptime(df.date_accepted, '%Y-%m')::date,
                    try_strptime(df.date_accepted, '%Y')::date
                ),
                cast(df.title as varchar),
                cast(df.authors as varchar)
            from df
            left join thesis t on cast(df.id as integer) = t.id
            where t.id is null
            """
        )


@app.command(name="oai-pmh")
def oai_pmh_cmd(
        location: str | None = typer.Option(None, "--location", "-l"),
        set_spec: str | None = typer.Option(None, "--set"),
        year_start: int | None = typer.Option(None, "--year-start"),
        year_end: int | None = typer.Option(None, "--year-end"),
        limit: int | None = typer.Option(None, "--limit"),
        metadata_prefix: str = typer.Option("oai_dc", "--metadata-prefix"),
        paginate: bool = typer.Option(True, "--paginate/--no-paginate"),
        cache_dir: Path | None = typer.Option(Path("data/raw/oai"), "--cache-dir"),
        output: Path = typer.Option(Path("data/processed/thesis.db"), "--output", "-o"),
        config: Path = typer.Option(Path("config/collections.yaml"), "--config", "-c"),
) -> None:
    """Harvest Skemman listing records through OAI-PMH into DuckDB."""
    cfg = load_config(config)
    year_start = year_start if year_start is not None else cfg.get("year_start")
    year_end = year_end if year_end is not None else cfg.get("year_end")
    limit = limit if limit is not None else cfg.get("record_limit")

    targets = [(location, set_spec)]
    if not location and not set_spec:
        handles = cfg.get("handles") or {}
        targets = [(handle, None) for handle in handles.values()]
    if not targets:
        raise typer.BadParameter("Provide --location or --set, or configure handles.")

    total = 0
    for target_location, target_set_arg in targets:
        target_set = target_set_arg or set_spec_from_location(target_location or "")
        written = 0

        def write_page(page_rows: list[dict[str, Any]]) -> None:
            nonlocal written
            if not page_rows:
                return
            write_thesis_rows(pd.DataFrame(page_rows), output)
            written += len(page_rows)

        df = harvest_oai_pmh(
            cfg,
            location=target_location,
            set_spec=target_set_arg,
            year_start=year_start,
            year_end=year_end,
            metadata_prefix=metadata_prefix,
            paginate=paginate,
            cache_dir=cache_dir,
            limit=limit,
            on_page=write_page,
            checkpoint_context={"output": str(output.resolve())},
        )
        if not written and df.empty:
            console.print(f"[yellow]No data found for OAI-PMH set {target_set}.[/yellow]")
            continue
        total += written
        console.print(f"[blue]OAI-PMH set: {target_set}[/blue]")
        console.print(f"[green]Wrote {written} records to {output}[/green]")
    if not total:
        console.print("[yellow]No data found for the provided filters.[/yellow]")


@app.command(name="metadata-load")
def metadata_load_cmd(
        db: Path = typer.Option(Path("data/processed/thesis.db"), "--db"),
        ids: str | None = typer.Option(None, "--ids"),
        urls: str | None = typer.Option(None, "--urls"),
        out_html: Path = typer.Option(Path("data/raw/items"), "--out-html"),
        user_agent: str = typer.Option("skemman-metadata-loader", "--user-agent"),
        delay: float = typer.Option(2.0, "--delay"),
) -> None:
    """Fetch or reuse Skemman item HTML and load normalized metadata into DuckDB."""
    loaded = load_metadata(
        db=db,
        ids=ids,
        urls=urls,
        out_html=out_html,
        user_agent=user_agent,
        delay=delay,
    )
    console.print(f"[green]Loaded metadata for {loaded} records.[/green]")


@app.command(name="files-index")
def files_index_cmd(
        db: Path = typer.Option(Path("data/processed/thesis.db"), "--db"),
        items_dir: Path = typer.Option(Path("data/raw/items"), "--items-dir"),
) -> None:
    """Index each item's file table: name, size, access and type.

    Reads the cached item HTML only, so it needs no network. Populates
    thesis_file, which titlepage-load uses to skip closed files and to take the
    small ones first.
    """
    theses, rows = load_file_index(db=db, items_dir=items_dir)
    console.print(f"[green]Indexed {rows} files across {theses} theses.[/green]")


@app.command(name="titlepage-load")
def titlepage_load_cmd(
        db: Path = typer.Option(Path("data/processed/thesis.db"), "--db"),
        limit: int | None = typer.Option(None, "--limit", help="Stop after N theses."),
        ids: str | None = typer.Option(None, "--ids", help="Comma-separated thesis ids."),
        degree_level: str = typer.Option("master", "--degree-level"),
        pages: int | None = typer.Option(None, "--pages", help="PDF pages to keep as text."),
        text_dir: Path = typer.Option(Path("data/raw/pdf_text"), "--text-dir"),
        pdf_dir: Path = typer.Option(Path("data/raw/pdfs"), "--pdf-dir"),
        config: Path = typer.Option(Path("config/collections.yaml"), "--config", "-c"),
        keep_pdf: bool = typer.Option(False, "--keep-pdf/--no-keep-pdf"),
        log_path: Path = typer.Option(Path("logs/titlepage.log"), "--log"),
        retry_failed: bool = typer.Option(
            False, "--retry-failed", help="Also retry theses recorded as permanently closed."
        ),
        max_mb: float | None = typer.Option(
            None, "--max-mb", help="Skip PDFs larger than this, per the item page."
        ),
        include_closed: bool = typer.Option(
            False, "--include-closed", help="Also try files the item page marks closed."
        ),
) -> None:
    """Read faculty, credits and degree off thesis title pages into DuckDB.

    Subject keywords are a guess; the title page is the authority. Extracted text
    is cached per thesis, so a second run re-parses without refetching. Theses
    that yield nothing are recorded in thesis_titlepage_failure and in the log.
    """
    processed, with_faculty, failed = load_titlepages(
        db=db,
        limit=limit,
        ids=ids,
        text_dir=text_dir,
        pdf_dir=pdf_dir,
        config=config,
        keep_pdf=keep_pdf,
        degree_level=degree_level,
        log_path=log_path,
        retry_failed=retry_failed,
        max_bytes=int(max_mb * 1024 * 1024) if max_mb else None,
        include_closed=include_closed,
        pages=pages,
    )
    if processed:
        pct = 100.0 * with_faculty / processed
        console.print(
            f"[green]Parsed {processed} title pages, "
            f"{with_faculty} with a stated faculty or deild ({pct:.0f}%).[/green]"
        )
    if failed:
        console.print(
            f"[yellow]{failed} produced nothing -- see {log_path} "
            f"and thesis_titlepage_failure.[/yellow]"
        )
    if not processed and not failed:
        console.print("[yellow]Nothing to do.[/yellow]")


@app.command(name="clean-people")
def clean_people_cmd(
        db: Path = typer.Option(Path("data/processed/thesis.db"), "--db"),
) -> None:
    """Normalize people names and embedded birth/death years in DuckDB."""
    changed = clean_people_table(db)
    console.print(f"[green]Cleaned {changed} people records.[/green]")


if __name__ == "__main__":
    app()
