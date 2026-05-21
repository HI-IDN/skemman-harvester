from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from .config import load_config
from .simple_search import harvest_simple_search
from .storage import write_table

app = typer.Typer(help="Skemman thesis scraper and analysis pipeline")
console = Console()


@app.command(name="harvest")
def harvest_cmd(
        config: Path = typer.Option(Path("config/collections.yaml"), "--config", "-c"),
        output: Path = typer.Option(Path("data/processed/items.parquet"), "--output", "-o"),
        limit: int | None = typer.Option(None, "--limit", help="Maximum item pages to harvest."),
) -> None:
    """TODO: implement item-page harvest workflow."""
    raise typer.Exit("TODO: harvest is not implemented yet. Use simple-search for listing capture.")


@app.command(name="extract-pdfs")
def extract_pdfs(
        input: Path = typer.Option(Path("data/processed/items.parquet"), "--input", "-i"),
        output: Path = typer.Option(Path("data/processed/items_with_text.parquet"), "--output",
                                    "-o"),
        config: Path = typer.Option(Path("config/collections.yaml"), "--config", "-c"),
        max_pages: int = typer.Option(8, "--max-pages"),
) -> None:
    """TODO: implement PDF text extraction workflow."""
    raise typer.Exit(
        "TODO: extract-pdfs is not implemented yet. Use simple-search for listing capture.")


@app.command()
def classify(
        input: Path = typer.Option(Path("data/processed/items_with_text.parquet"), "--input", "-i"),
        output: Path = typer.Option(Path("data/processed/classified.parquet"), "--output", "-o"),
        config: Path = typer.Option(Path("config/collections.yaml"), "--config", "-c"),
) -> None:
    """TODO: implement classification workflow."""
    raise typer.Exit(
        "TODO: classify is not implemented yet. Use simple-search for listing capture.")


@app.command()
def export(
        input: Path = typer.Option(Path("data/processed/classified.parquet"), "--input", "-i"),
        csv: Path = typer.Option(Path("outputs/classified.csv"), "--csv"),
) -> None:
    """TODO: implement export workflow."""
    raise typer.Exit("TODO: export is not implemented yet. Use simple-search for listing capture.")


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
    """Scrape simple-search listing rows (date/title/author/url) into DuckDB."""
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
    if not df.empty and "source_url" in df.columns:
        console.print(f"[blue]Source URL: {df['source_url'].iloc[0]}[/blue]")
    console.print(f"[green]Wrote {len(df)} records to {output}[/green]")


if __name__ == "__main__":
    app()
