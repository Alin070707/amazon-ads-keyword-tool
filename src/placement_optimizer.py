from __future__ import annotations

import pandas as pd


def placement_analysis(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    if "placement" not in frame.columns or frame.empty:
        return pd.DataFrame()
    df = frame.groupby("placement", dropna=False).agg(
        impressions=("impressions", "sum"),
        clicks=("clicks", "sum"),
        spend=("spend", "sum"),
        orders=("orders", "sum"),
        sales=("sales", "sum"),
    ).reset_index()
    from .metric_calculator import add_core_metrics
    df = add_core_metrics(df, settings)
    target = settings.get("target_acos", 0.30)
    df["placement_action"] = "Observe"
    df.loc[(df["orders"] >= settings.get("min_orders", 2)) & (df["acos"] <= target), "placement_action"] = "Increase Placement"
    df.loc[(df["spend"] > 0) & ((df["orders"] == 0) | (df["acos"] > target * 1.3)), "placement_action"] = "Decrease Placement"
    df["suggested_placement_adjustment"] = df["placement_action"].map({"Increase Placement": "+10%", "Decrease Placement": "-15%"}).fillna("0%")
    return df
