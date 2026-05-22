from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from .config import load_config
from .metadata_load import load_metadata
from .simple_search import harvest_simple_search
from .storage import write_table

app = typer.Typer(help="Skemman thesis metadata loader")
console = Console()


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
    write_table(df, output)
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


if __name__ == "__main__":
    app()
