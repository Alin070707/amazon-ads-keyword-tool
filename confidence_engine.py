from __future__ import annotations

import pandas as pd


def confidence_for_row(row: pd.Series, settings: dict) -> str:
    min_clicks = settings.get("min_clicks", 8)
    min_orders = settings.get("min_orders", 2)
    min_impressions = settings.get("min_impressions", 500)
    clicks = float(row.get("clicks", 0) or 0)
    orders = float(row.get("orders", 0) or 0)
    impressions = float(row.get("impressions", 0) or 0)
    if clicks >= min_clicks * 2 and orders >= min_orders and impressions >= min_impressions:
        return "高"
    if clicks >= min_clicks or orders > 0 or impressions >= min_impressions:
        return "中"
    return "低"


def add_confidence(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    df = frame.copy()
    df["confidence"] = df.apply(lambda row: confidence_for_row(row, settings), axis=1)
    return df
