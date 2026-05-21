from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


def _normalise_record(record: Any) -> dict[str, Any]:
    if is_dataclass(record):
        record = asdict(record)
    out = dict(record)
    for key, value in list(out.items()):
        if isinstance(value, (list, dict)):
            out[key] = value
    return out


def _table_name_from_path(path: Path) -> str:
    name = path.stem
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
    if not safe:
        safe = "data"
    if safe[0].isdigit():
        safe = f"t_{safe}"
    return safe


def write_parquet(records: list[Any], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([_normalise_record(r) for r in records])
    write_table(df, path)


def read_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() in {".duckdb", ".db"}:
        table = _table_name_from_path(path)
        with duckdb.connect(str(path), read_only=True) as con:
            return con.execute(f"select * from {table}").df()
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_parquet(path)


def _duckdb_table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return (
            con.execute(
                "select count(*) from information_schema.tables where lower(table_name) = ?",
                [table.lower()],
            ).fetchone()[0]
            > 0
    )


def write_table(df: pd.DataFrame, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".duckdb", ".db"}:
        table = _table_name_from_path(path)
        with duckdb.connect(str(path)) as con:
            con.register("df", df)
            if _duckdb_table_exists(con, table):
                key = None
                if "id" in df.columns:
                    key = "id"
                elif "item_id" in df.columns:
                    key = "item_id"
                elif "handle" in df.columns:
                    key = "handle"
                elif "item_url" in df.columns:
                    key = "item_url"
                if key:
                    con.execute(
                        f"insert into {table} (id, date_accepted, title, authors) "
                        f"select cast(df.id as integer), "
                        f"try_strptime(df.date_accepted, '%d.%m.%Y')::date, "
                        f"cast(df.title as varchar), cast(df.authors as varchar) "
                        f"from df left join {table} t on df.{key} = t.{key} "
                        f"where t.{key} is null"
                    )
                else:
                    con.execute(
                        f"insert into {table} (id, date_accepted, title, authors) "
                        f"select cast(df.id as integer), "
                        f"try_strptime(df.date_accepted, '%d.%m.%Y')::date, "
                        f"cast(df.title as varchar), cast(df.authors as varchar) "
                        f"from df"
                    )
            else:
                con.execute(
                    f"create table {table} as "
                    f"select cast(df.id as integer) as id, "
                    f"try_strptime(df.date_accepted, '%d.%m.%Y')::date as date_accepted, "
                    f"cast(df.title as varchar) as title, "
                    f"cast(df.authors as varchar) as authors "
                    f"from df"
                )
        return
    if path.suffix.lower() == ".csv":
        df.to_csv(path, index=False)
    else:
        df.to_parquet(path, index=False)
