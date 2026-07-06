from __future__ import annotations

import re

import numpy as np
import pandas as pd


NUMERIC_FIELDS = [
    "impressions", "clicks", "spend", "sales", "orders", "units", "bid", "budget",
    "ntb_orders", "ntb_sales", "total_sales", "organic_sales",
]


def parse_number(value):
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float, np.number)):
        return float(value)
    text = str(value).strip()
    if text == "":
        return 0.0
    is_percent = text.endswith("%")
    text = text.replace(",", "")
    text = re.sub(r"[$€£¥￥]", "", text)
    text = text.replace("%", "")
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in ["", ".", "-"]:
        return 0.0
    number = float(text)
    return number / 100 if is_percent else number


def clean_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int | list[str]]]:
    original_rows = len(frame)
    cleaned = frame.copy()
    cleaned.columns = [str(c).strip() for c in cleaned.columns]
    cleaned = cleaned.dropna(how="all")
    blank_rows_removed = original_rows - len(cleaned)
    for col in cleaned.select_dtypes(include=["object"]).columns:
        cleaned[col] = cleaned[col].map(lambda x: x.strip() if isinstance(x, str) else x)
    for field in NUMERIC_FIELDS:
        if field in cleaned.columns:
            cleaned[field] = cleaned[field].map(parse_number)
    if "date" in cleaned.columns:
        cleaned["date"] = pd.to_datetime(cleaned["date"], errors="coerce")
    before_dedup = len(cleaned)
    cleaned = cleaned.drop_duplicates()
    duplicate_rows_removed = before_dedup - len(cleaned)
    critical = [f for f in ["campaign", "clicks", "spend"] if f not in cleaned.columns]
    quality = {
        "original_rows": original_rows,
        "cleaned_rows": len(cleaned),
        "blank_rows_removed": blank_rows_removed,
        "duplicate_rows_removed": duplicate_rows_removed,
        "missing_critical_fields": critical,
    }
    return cleaned, quality


def combine_cleaned(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False).drop_duplicates()
