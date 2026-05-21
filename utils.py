from __future__ import annotations

import hashlib
import time
from pathlib import Path
from urllib.parse import urljoin

import requests


def normalise_url(base_url: str, href: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", href)


def cache_path_for_url(cache_dir: Path, url: str, suffix: str = ".html") -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}{suffix}"


class PoliteSession:
    def __init__(
            self,
            user_agent: str,
            delay_seconds: float = 2.0,
            timeout_seconds: int = 30,
            cache_dir: Path | None = None,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self.cache_dir = cache_dir
        self._last_request = 0.0
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def get_text(self, url: str, use_cache: bool = True) -> str:
        if self.cache_dir and use_cache:
            path = cache_path_for_url(self.cache_dir, url)
            if path.exists():
                return path.read_text(encoding="utf-8", errors="replace")

        elapsed = time.time() - self._last_request
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

        resp = self.session.get(url, timeout=self.timeout_seconds)
        self._last_request = time.time()
        resp.raise_for_status()
        text = resp.text

        if self.cache_dir and use_cache:
            cache_path_for_url(self.cache_dir, url).write_text(text, encoding="utf-8")

        return text

    def download_binary(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)

        elapsed = time.time() - self._last_request
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

        with self.session.get(url, timeout=self.timeout_seconds, stream=True) as resp:
            self._last_request = time.time()
            resp.raise_for_status()
            with dest.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
        return dest
