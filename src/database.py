from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from .utils import ROOT


DB_PATH = ROOT / "data" / "processed" / "history.sqlite"


def get_connection(path: str | Path = DB_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def init_db(path: str | Path = DB_PATH) -> None:
    with get_connection(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                run_name TEXT,
                total_spend REAL,
                total_sales REAL,
                total_orders REAL,
                acos REAL
            )
            """
        )


def save_run(summary: dict, path: str | Path = DB_PATH) -> int:
    init_db(path)
    with get_connection(path) as conn:
        cur = conn.execute(
            "INSERT INTO analysis_runs(run_name,total_spend,total_sales,total_orders,acos) VALUES(?,?,?,?,?)",
            (
                summary.get("run_name", "analysis"),
                summary.get("total_spend", 0),
                summary.get("total_sales", 0),
                summary.get("total_orders", 0),
                summary.get("acos", 0),
            ),
        )
        return int(cur.lastrowid)


def load_runs(path: str | Path = DB_PATH) -> pd.DataFrame:
    init_db(path)
    with get_connection(path) as conn:
        return pd.read_sql_query("SELECT * FROM analysis_runs ORDER BY id DESC", conn)
