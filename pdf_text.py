from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

import fitz  # PyMuPDF
import pandas as pd
from rich.console import Console
from tqdm import tqdm

from .utils import PoliteSession

console = Console()


def filename_for_pdf(url: str) -> str:
    path_name = Path(urlparse(url).path).name
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    if path_name.lower().endswith(".pdf"):
        return f"{digest}-{path_name}"
    return f"{digest}.pdf"


def extract_pdf_text(path: str | Path, max_pages: int = 8) -> str:
    doc = fitz.open(path)
    chunks: list[str] = []
    for page_index in range(min(max_pages, doc.page_count)):
        text = doc.load_page(page_index).get_text("text")
        if text:
            chunks.append(text)
    return "\n\n".join(chunks)


def add_pdf_text(
        df: pd.DataFrame,
        user_agent: str,
        delay_seconds: float = 2.0,
        max_pages: int = 8,
        download_dir: str | Path = "data/raw/pdfs",
) -> pd.DataFrame:
    session = PoliteSession(
        user_agent=user_agent,
        delay_seconds=delay_seconds,
        timeout_seconds=60,
        cache_dir=None,
    )
    download_dir = Path(download_dir)
    snippets: list[str | None] = []

    for _, row in tqdm(df.iterrows(), total=len(df), desc="PDF text", unit="item"):
        urls = row.get("file_urls")
        if not isinstance(urls, list) or not urls:
            snippets.append(None)
            continue

        pdf_url = urls[0]
        try:
            pdf_path = download_dir / filename_for_pdf(pdf_url)
            if not pdf_path.exists():
                session.download_binary(pdf_url, pdf_path)
            snippets.append(extract_pdf_text(pdf_path, max_pages=max_pages))
        except Exception as exc:  # noqa: BLE001
            console.print(
                f"[yellow]Could not extract PDF for {row.get('item_url')}: {exc}[/yellow]")
            snippets.append(None)

    out = df.copy()
    out["pdf_text_snippet"] = snippets
    return out
