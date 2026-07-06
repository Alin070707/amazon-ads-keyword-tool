from __future__ import annotations

import pandas as pd


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def recommended_bid(row: pd.Series, settings: dict) -> dict:
    current_bid = float(row.get("bid", 0) or 0)
    if current_bid <= 0:
        current_bid = float(row.get("cpc", 0) or settings.get("min_bid", 0.02))
    target_acos = float(settings.get("target_acos", 0.30))
    actual_acos = float(row.get("acos", 0) or 0)
    target_cpc = float(row.get("target_cpc", 0) or 0)
    cpc = float(row.get("cpc", 0) or 0)
    max_up = float(settings.get("max_bid_up_pct", 0.20))
    max_down = float(settings.get("max_bid_down_pct", 0.30))
    if actual_acos > 0 and target_acos > 0:
        raw = current_bid * target_acos / actual_acos
    elif target_cpc > 0 and cpc > 0:
        raw = current_bid * target_cpc / cpc
    else:
        raw = current_bid
    raw = clamp(raw, current_bid * (1 - max_down), current_bid * (1 + max_up))
    rec = clamp(raw, float(settings.get("min_bid", 0.02)), float(settings.get("max_bid", 8.0)))
    return {
        "current_bid": round(current_bid, 2),
        "recommended_bid": round(rec, 2),
        "bid_change": round(rec - current_bid, 2),
        "bid_change_pct": 0 if current_bid == 0 else round((rec - current_bid) / current_bid, 4),
    }


def add_bid_recommendations(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    df = frame.copy()
    recs = df.apply(lambda row: recommended_bid(row, settings), axis=1, result_type="expand")
    for col in recs.columns:
        df[col] = recs[col]
    return df
