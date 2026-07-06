from __future__ import annotations

import pandas as pd


def campaign_budget_recommendations(campaign_df: pd.DataFrame, settings: dict) -> pd.DataFrame:
    df = campaign_df.copy()
    if df.empty:
        return df
    target = float(settings.get("target_acos", 0.30))
    df["current_budget"] = df.get("budget", 0)
    df["budget_action"] = "Observe"
    df["recommended_budget"] = df["current_budget"]
    good = (df["orders"] >= settings.get("min_orders", 2)) & (df["acos"] > 0) & (df["acos"] <= target)
    bad = (df["spend"] > 0) & ((df["orders"] == 0) | (df["acos"] > target * 1.3))
    df.loc[good, "budget_action"] = "Increase Budget"
    df.loc[good, "recommended_budget"] = (df.loc[good, "current_budget"].replace(0, df.loc[good, "spend"] / 7) * 1.2).round(2)
    df.loc[bad, "budget_action"] = "Decrease Budget"
    df.loc[bad, "recommended_budget"] = (df.loc[bad, "current_budget"] * 0.85).round(2)
    df["budget_reason"] = df.apply(lambda r: "表现好且ACOS不高于目标，建议增加预算承接更多有效流量。" if r["budget_action"] == "Increase Budget" else ("花费效率偏低，建议把预算转移到更高转化Campaign。" if r["budget_action"] == "Decrease Budget" else "数据暂不足或表现接近目标，建议继续观察。"), axis=1)
    return df
