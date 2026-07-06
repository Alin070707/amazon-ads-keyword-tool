from __future__ import annotations

import pandas as pd


def _series_or_zero(df: pd.DataFrame, column: str) -> pd.Series:
    if column in df.columns:
        return pd.to_numeric(df[column], errors="coerce").fillna(0)
    return pd.Series(0.0, index=df.index)


def unit_profit_before_ads(settings: dict) -> float:
    price = float(settings.get("product_price", 0) or 0)
    return price - (
        float(settings.get("product_cost", 0) or 0)
        + price * float(settings.get("amazon_commission_rate", 0) or 0)
        + float(settings.get("fba_fee", 0) or 0)
        + float(settings.get("inbound_shipping", 0) or 0)
        + float(settings.get("storage_fee", 0) or 0)
        + price * float(settings.get("refund_rate", 0) or 0)
        + price * float(settings.get("vat_rate", 0) or 0)
        + float(settings.get("coupon_cost", 0) or 0)
        + float(settings.get("other_unit_cost", 0) or 0)
    )


def add_profit_metrics(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    df = frame.copy()
    unit_profit = unit_profit_before_ads(settings)
    price = float(settings.get("product_price", 0) or 0)
    units = _series_or_zero(df, "units") if "units" in df.columns else _series_or_zero(df, "orders")
    spend = _series_or_zero(df, "spend")
    orders = _series_or_zero(df, "orders")
    df["gross_profit_before_ads"] = units * unit_profit
    df["profit_after_ads"] = df["gross_profit_before_ads"] - spend
    df["profit_per_ad_order"] = df["profit_after_ads"] / orders.replace(0, pd.NA)
    df["profit_per_ad_order"] = df["profit_per_ad_order"].fillna(0)
    df["computed_break_even_acos"] = 0 if price <= 0 else unit_profit / price
    return df
