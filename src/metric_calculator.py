from __future__ import annotations

import numpy as np
import pandas as pd


def ensure_metric_columns(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    for col in ["impressions", "clicks", "spend", "orders", "units", "sales", "total_sales", "organic_sales"]:
        if col not in df.columns:
            df[col] = 0.0
    return df


def div(n, d):
    return np.where(pd.Series(d).fillna(0).astype(float) == 0, 0.0, pd.Series(n).fillna(0).astype(float) / pd.Series(d).fillna(0).astype(float))


def add_core_metrics(frame: pd.DataFrame, settings: dict | None = None) -> pd.DataFrame:
    settings = settings or {}
    df = ensure_metric_columns(frame)
    df["ctr"] = div(df["clicks"], df["impressions"])
    df["cpc"] = div(df["spend"], df["clicks"])
    df["cvr"] = div(df["orders"], df["clicks"])
    df["acos"] = div(df["spend"], df["sales"])
    df["roas"] = div(df["sales"], df["spend"])
    df["cpa"] = div(df["spend"], df["orders"])
    df["rpc"] = div(df["sales"], df["clicks"])
    df["tacos"] = div(df["spend"], df["total_sales"])
    df["advertising_order_share"] = div(df["orders"], df.get("total_orders", df["orders"]))
    df["advertising_sales_share"] = div(df["sales"], df["total_sales"])
    price = float(settings.get("product_price", 0) or 0)
    break_even_acos = float(settings.get("break_even_acos", 0) or 0)
    target_acos = float(settings.get("target_acos", 0) or 0)
    df["break_even_acos"] = break_even_acos
    df["break_even_cpc"] = df["cvr"] * price * break_even_acos
    df["target_cpc"] = df["rpc"] * target_acos
    df["profit_after_ads"] = df.get("gross_profit", df["sales"] * break_even_acos) - df["spend"]
    df["estimated_advertising_profit"] = df["sales"] * break_even_acos - df["spend"]
    return df


def aggregate_metrics(frame: pd.DataFrame, dimensions: list[str]) -> pd.DataFrame:
    df = ensure_metric_columns(frame)
    dims = [d for d in dimensions if d in df.columns]
    if not dims:
        dims = []
    agg = df.groupby(dims, dropna=False).agg({
        "impressions": "sum",
        "clicks": "sum",
        "spend": "sum",
        "orders": "sum",
        "units": "sum",
        "sales": "sum",
        "total_sales": "sum",
        "organic_sales": "sum",
        **({"bid": "mean"} if "bid" in df.columns else {}),
        **({"budget": "mean"} if "budget" in df.columns else {}),
    }).reset_index() if dims else pd.DataFrame([df.sum(numeric_only=True)])
    return add_core_metrics(agg)
