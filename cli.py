from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import typer
from rich.console import Console

from .config import load_config
from .metadata_load import clean_people_table, load_metadata
from .simple_search import harvest_simple_search
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
                try_strptime(df.date_accepted, '%d.%m.%Y')::date,
                cast(df.title as varchar),
                cast(df.authors as varchar)
            from df
            left join thesis t on cast(df.id as integer) = t.id
            where t.id is null
            """
        )


@app.command(name="simple-search")
def simple_search_cmd(
        url: str | None = typer.Option(None, "--url"),
        location: str | None = typer.Option(None, "--location", "-l"),
        year: int | None = typer.Option(None, "--year", "-y"),
        rpp: int = typer.Option(25, "--rpp"),
        paginate: bool = typer.Option(True, "--paginate/--no-paginate"),
        output: Path = typer.Option(Path("data/processed/thesis.db"), "--output", "-o"),
        config: Path = typer.Option(Path("config/collections.yaml"), "--config", "-c"),
) -> None:
    """Scrape Skemman simple-search listing rows into DuckDB."""
    cfg = load_config(config)
    if not url and not location:
        raise typer.BadParameter("Provide either --url or --location.")
    df = harvest_simple_search(
        cfg,
        url=url,
        location=location,
        year=year,
        rpp=rpp,
        paginate=paginate,
    )
    if df.empty:
        console.print("[yellow]No data found for the provided filters.[/yellow]")
        return
    write_thesis_rows(df, output)
    if "source_url" in df.columns:
        console.print(f"[blue]Source URL: {df['source_url'].iloc[0]}[/blue]")
    console.print(f"[green]Wrote {len(df)} records to {output}[/green]")


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


@app.command(name="titlepage-load")
def titlepage_load_cmd(
        db: Path = typer.Option(Path("data/processed/thesis.db"), "--db"),
        limit: int | None = typer.Option(None, "--limit", help="Stop after N theses."),
        ids: str | None = typer.Option(None, "--ids", help="Comma-separated thesis ids."),
        degree_level: str = typer.Option("master", "--degree-level"),
        text_dir: Path = typer.Option(Path("data/raw/pdf_text"), "--text-dir"),
        pdf_dir: Path = typer.Option(Path("data/raw/pdfs"), "--pdf-dir"),
        config: Path = typer.Option(Path("config/collections.yaml"), "--config", "-c"),
        keep_pdf: bool = typer.Option(False, "--keep-pdf/--no-keep-pdf"),
) -> None:
    """Read faculty, credits and degree off thesis title pages into DuckDB.

    Subject keywords are a guess; the title page is the authority. Extracted text
    is cached per thesis, so a second run re-parses without refetching.
    """
    processed, with_faculty = load_titlepages(
        db=db,
        limit=limit,
        ids=ids,
        text_dir=text_dir,
        pdf_dir=pdf_dir,
        config=config,
        keep_pdf=keep_pdf,
        degree_level=degree_level,
    )
    if processed:
        pct = 100.0 * with_faculty / processed
        console.print(
            f"[green]Parsed {processed} title pages, "
            f"{with_faculty} with a stated faculty or deild ({pct:.0f}%).[/green]"
        )
    else:
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
