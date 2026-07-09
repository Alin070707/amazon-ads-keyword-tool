from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

from .metric_calculator import add_core_metrics
from .search_term_analyzer import aggregate_search_terms, build_phrase_root_map, term_scene


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        value = float(value)
        if math.isfinite(value):
            return value
    except Exception:
        pass
    return default


def _money(value: float) -> str:
    return f"${value:.2f}"


def _pct(value: float) -> str:
    return f"{value:.1%}"


def _clean_name(value: str, fallback: str = "关键词") -> str:
    text = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", " ", str(value or "")).strip()
    return text[:48] or fallback


def _recommended_bid(row: pd.Series, target_acos: float, mode: str) -> float:
    cpc = _num(row.get("cpc"))
    rpc = _num(row.get("rpc"))
    cvr = _num(row.get("cvr"))
    price = _num(row.get("avg_order_value")) or _num(row.get("product_price"))
    bid_base = _num(row.get("bid")) or cpc or 0.75
    target_cpc = rpc * target_acos if rpc > 0 else cvr * price * target_acos
    if target_cpc <= 0:
        target_cpc = bid_base * (0.70 if mode == "cut" else 1.10)
    if mode == "cut":
        return round(max(0.2, min(bid_base * 0.70, target_cpc)), 2)
    if mode == "scale":
        return round(min(bid_base * 1.20, max(target_cpc, bid_base * 1.05)), 2)
    if mode == "rank":
        return round(min(bid_base * 1.35, max(target_cpc * 1.15, bid_base * 1.15)), 2)
    return round(bid_base, 2)


def _daily_budget(spend: float, days: int, multiplier: float, floor: float, ceiling: float | None = None) -> float:
    base = spend / max(days, 1)
    budget = max(floor, base * multiplier)
    if ceiling is not None:
        budget = min(budget, ceiling)
    return round(budget, 2)


def _row_current_data(row: pd.Series) -> str:
    return (
        f"曝光{int(_num(row.get('impressions')))}，点击{int(_num(row.get('clicks')))}，"
        f"花费{_money(_num(row.get('spend')))}，订单{int(_num(row.get('orders')))}，"
        f"销售额{_money(_num(row.get('sales')))}，ACOS {_pct(_num(row.get('acos')))}，"
        f"CPC {_money(_num(row.get('cpc')))}，CVR {_pct(_num(row.get('cvr')))}"
    )


def _evidence_level(row: pd.Series, min_clicks: int = 8, min_orders: int = 2) -> str:
    clicks = _num(row.get("clicks"))
    orders = _num(row.get("orders"))
    impressions = _num(row.get("impressions"))
    spend = _num(row.get("spend"))
    if clicks >= max(min_clicks * 3, 24) and orders >= max(min_orders, 2):
        return "高"
    if clicks >= min_clicks and (orders >= 1 or spend > 0 or impressions >= 500):
        return "中"
    return "低"


def _campaign_evidence_level(df: pd.DataFrame, min_clicks: int = 8, min_orders: int = 2) -> str:
    if df.empty:
        return "低"
    clicks = float(df.get("clicks", pd.Series(dtype=float)).sum())
    orders = float(df.get("orders", pd.Series(dtype=float)).sum())
    impressions = float(df.get("impressions", pd.Series(dtype=float)).sum())
    if clicks >= max(min_clicks * 5, 40) and orders >= max(min_orders * 2, 4):
        return "高"
    if clicks >= min_clicks * 2 or orders >= min_orders or impressions >= 1000:
        return "中"
    return "低"


def _trigger_basis(row: pd.Series, scenario: str, target_acos: float) -> str:
    orders = _num(row.get("orders"))
    acos = _num(row.get("acos"))
    if scenario.startswith("1"):
        if orders <= 0:
            return f"点击达到判断门槛但订单为0，先按低价观察/否定候选处理；当前数据：{_row_current_data(row)}"
        return f"有订单但ACOS {_pct(acos)} 高于目标ACOS {_pct(target_acos)}，按目标ACOS反推建议竞价；当前数据：{_row_current_data(row)}"
    if scenario.startswith("2"):
        return f"已有订单且ACOS不高于目标ACOS {_pct(target_acos)}，适合单独Exact收割或Phrase小预算扩展；当前数据：{_row_current_data(row)}"
    if scenario.startswith("3"):
        return f"该词/词根已有点击和订单基础，适合单独预算推进排名；当前数据：{_row_current_data(row)}"
    return _row_current_data(row)


def _execution_guardrail(scenario: str, evidence: str) -> str:
    if evidence == "低":
        return "证据不足，只建议观察或小预算测试，不建议直接暂停、强否定或大幅加预算。"
    if scenario.startswith("1"):
        return "执行前先确认词和产品相关性；品牌词、核心词、新品测试词不要直接Phrase否定。"
    if scenario.startswith("2"):
        return "先小幅加预算，3-7天复查CPC、ACOS和订单，不要把泛词混进收割广告组。"
    if scenario.startswith("3"):
        return "这是排名预算池，允许短期ACOS偏高，但要独立预算并每7天复盘排名和订单变化。"
    return "先人工审核，再执行。"


def build_operator_strategy_summary(frame: pd.DataFrame, settings: dict) -> dict[str, pd.DataFrame]:
    if frame.empty or "search_term" not in frame.columns:
        empty = pd.DataFrame()
        return {"strategy_summary": empty, "reduce_acos": empty, "efficient_budget": empty, "rank_push": empty}

    target_acos = float(settings.get("target_acos", 0.30) or 0.30)
    min_clicks = int(settings.get("min_clicks", 8) or 8)
    min_orders = int(settings.get("min_orders", 2) or 2)
    days = int(settings.get("analysis_days", settings.get("attribution_window_days", 30)) or 30)
    ranking_terms = [str(x).strip().lower() for x in settings.get("ranking_terms", []) if str(x).strip()]

    terms = aggregate_search_terms(frame, settings)
    if terms.empty:
        empty = pd.DataFrame()
        return {"strategy_summary": empty, "reduce_acos": empty, "efficient_budget": empty, "rank_push": empty}

    terms = terms.copy()
    terms["avg_order_value"] = terms.apply(lambda r: _num(r.get("sales")) / _num(r.get("orders"), 1) if _num(r.get("orders")) > 0 else _num(settings.get("product_price", 25)), axis=1)
    terms["product_price"] = float(settings.get("product_price", 25) or 25)
    if "bid" not in terms.columns:
        terms["bid"] = 0.0

    rows: list[dict[str, Any]] = []

    waste = terms[((terms["clicks"] >= min_clicks) & (terms["orders"] == 0)) | ((terms["orders"] > 0) & (terms["acos"] > target_acos * 1.25))].copy()
    for _, row in waste.sort_values(["spend", "clicks"], ascending=False).head(30).iterrows():
        no_order = _num(row.get("orders")) == 0
        action = "降低竞价并观察" if not no_order else "先降竞价；明显不相关再否定精准"
        match = "Negative Exact + Phrase观察" if no_order else "Exact/老投放降价"
        suggested_bid = _recommended_bid(row, target_acos, "cut")
        rows.append({
            "策略场景": "1 降低ACOS",
            "优先级": "P0" if _num(row.get("spend")) >= max(10, _num(row.get("avg_order_value")) * target_acos) else "P1",
            "Campaign": row.get("campaign", ""),
            "建议广告组": f"ACOS控制_{_clean_name(row.get('word_root') or row.get('search_term'))}",
            "关键词/词根": row.get("search_term", ""),
            "建议匹配方式": match,
            "当前CPC": round(_num(row.get("cpc")), 2),
            "建议CPC/竞价": suggested_bid,
            "建议日预算": _daily_budget(_num(row.get("spend")), days, 0.65, 3.0, 20.0),
            "当前数据": _row_current_data(row),
            "建议动作": action,
            "建议原因": "该词已经消耗预算但没有订单，或ACOS明显高于目标；先控价保留少量数据，不建议一刀切暂停核心相关词。" if no_order else "该词有转化但效率低于目标，建议按目标ACOS反推CPC，逐步降价。",
            "风险提示": "如果这是核心大词或新品测试词，先降价观察7天，不要直接否定Phrase。",
        })

    efficient = terms[(terms["orders"] >= min_orders) & (terms["sales"] > 0) & (terms["acos"] <= target_acos)].copy()
    for _, row in efficient.sort_values(["acos", "orders", "sales"], ascending=[True, False, False]).head(30).iterrows():
        root = row.get("word_root") or term_scene(row.get("search_term", ""))
        rows.append({
            "策略场景": "2 低预算高回报",
            "优先级": "P1",
            "Campaign": row.get("campaign", ""),
            "建议广告组": f"ROI_{_clean_name(root)}_ExactPhrase",
            "关键词/词根": row.get("search_term", ""),
            "建议匹配方式": "Exact优先，Phrase扩展；原自动/广泛中暂不急着否定，避免断流",
            "当前CPC": round(_num(row.get("cpc")), 2),
            "建议CPC/竞价": _recommended_bid(row, target_acos, "scale"),
            "建议日预算": _daily_budget(_num(row.get("spend")), days, 1.5, 5.0, 35.0),
            "当前数据": _row_current_data(row),
            "建议动作": "单独建高ROI广告组，预算小幅增加，优先承接确定性订单",
            "建议原因": "该词已经证明能转化且ACOS不高，适合用较小预算集中吃确定性流量。",
            "风险提示": "预算增加不要过猛，先按1.5倍日消耗测试3-7天，避免CPC被系统快速抬高。",
        })

    root_map = build_phrase_root_map(terms, settings)
    terms["rank_root"] = terms["search_term"].map(lambda x: root_map.get(str(x), term_scene(str(x))))
    root_group = terms.groupby("rank_root", dropna=False).agg(
        sample_terms=("search_term", lambda x: " | ".join(list(x)[:8])),
        campaign=("campaign", lambda x: ", ".join(sorted({str(v) for v in x if str(v)}))[:220]),
        impressions=("impressions", "sum"),
        clicks=("clicks", "sum"),
        spend=("spend", "sum"),
        orders=("orders", "sum"),
        sales=("sales", "sum"),
    ).reset_index()
    root_group = add_core_metrics(root_group, settings)
    root_group["rank_score"] = root_group.apply(
        lambda r: _num(r.get("orders")) * 8 + _num(r.get("sales")) / 10 + _num(r.get("impressions")) / 1000 - _num(r.get("acos")) * 3,
        axis=1,
    )
    if ranking_terms:
        root_group["rank_score"] += root_group["rank_root"].map(lambda x: 30 if any(t in str(x).lower() or str(x).lower() in t for t in ranking_terms) else 0)
    candidates = root_group[(root_group["clicks"] >= max(min_clicks, 5)) & (root_group["orders"] > 0)].copy()
    for _, row in candidates.sort_values(["rank_score", "orders", "sales"], ascending=False).head(20).iterrows():
        root = str(row.get("rank_root") or "").strip()
        if not root:
            continue
        rows.append({
            "策略场景": "3 推排名大词",
            "优先级": "P1" if _num(row.get("acos")) <= target_acos * 1.4 else "P2",
            "Campaign": row.get("campaign", ""),
            "建议广告组": f"Rank_{_clean_name(root)}_ExactPhrase",
            "关键词/词根": root,
            "建议匹配方式": "Exact打核心词，Phrase覆盖长尾；不要混入无关泛词",
            "当前CPC": round(_num(row.get("cpc")), 2),
            "建议CPC/竞价": _recommended_bid(row, target_acos, "rank"),
            "建议日预算": _daily_budget(_num(row.get("spend")), days, 2.2, 10.0, 80.0),
            "当前数据": _row_current_data(row),
            "建议动作": "建立排名推进广告组，集中预算打相关大词和同短语长尾词",
            "建议原因": "该短语词根已有转化基础，适合用Exact+Phrase集中权重，逐步把相关大词排名往前推。",
            "风险提示": "推排名打法会牺牲短期ACOS，建议单独预算池控制，不能和保利润广告混在一起。",
        })

    strategy_summary = pd.DataFrame(rows)
    if not strategy_summary.empty:
        order = {"P0": 0, "P1": 1, "P2": 2}
        strategy_summary["_priority_order"] = strategy_summary["优先级"].map(order).fillna(9)
        strategy_summary = strategy_summary.sort_values(["策略场景", "_priority_order", "建议日预算"], ascending=[True, True, False]).drop(columns=["_priority_order"])

    return {
        "strategy_summary": strategy_summary,
        "reduce_acos": strategy_summary[strategy_summary["策略场景"].eq("1 降低ACOS")].copy() if not strategy_summary.empty else pd.DataFrame(),
        "efficient_budget": strategy_summary[strategy_summary["策略场景"].eq("2 低预算高回报")].copy() if not strategy_summary.empty else pd.DataFrame(),
        "rank_push": strategy_summary[strategy_summary["策略场景"].eq("3 推排名大词")].copy() if not strategy_summary.empty else pd.DataFrame(),
    }


def _term_rows_text(df: pd.DataFrame, limit: int = 6) -> str:
    if df.empty:
        return ""
    values = []
    for _, r in df.head(limit).iterrows():
        values.append(f"{r.get('search_term', r.get('rank_root', ''))}｜{r.get('match_type_plan', '')}｜竞价{_money(_num(r.get('planned_bid')))}")
    return "\n".join(values)


def _campaign_row(
    scenario: str,
    campaign_name: str,
    objective: str,
    daily_budget: float,
    ad_group_name: str,
    match_structure: str,
    keywords_df: pd.DataFrame,
    reason: str,
    risk: str,
    priority: str = "P1",
) -> dict[str, Any]:
    return {
        "策略场景": scenario,
        "优先级": priority,
        "建议新建Campaign": campaign_name,
        "Campaign目标": objective,
        "建议日预算": round(daily_budget, 2),
        "建议广告组": ad_group_name,
        "广告组结构": match_structure,
        "放入关键词数量": len(keywords_df),
        "关键词清单摘要": _term_rows_text(keywords_df),
        "预计覆盖搜索词": " | ".join(keywords_df.get("search_term", keywords_df.get("rank_root", pd.Series(dtype=str))).astype(str).head(8).tolist()),
        "建组原因": reason,
        "证据可靠性": _campaign_evidence_level(keywords_df),
        "依据摘要": f"放入{len(keywords_df)}个关键词/词根，合计点击{int(_num(keywords_df.get('clicks', pd.Series(dtype=float)).sum()))}，订单{int(_num(keywords_df.get('orders', pd.Series(dtype=float)).sum()))}，花费{_money(_num(keywords_df.get('spend', pd.Series(dtype=float)).sum()))}。",
        "执行提醒": risk,
    }


def _keyword_plan_rows(
    df: pd.DataFrame,
    campaign_name: str,
    ad_group_name: str,
    scenario: str,
    match_type: str,
    bid_mode: str,
    target_acos: float,
    min_clicks: int = 8,
    min_orders: int = 2,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        bid = _num(row.get("planned_bid")) or _recommended_bid(row, target_acos, bid_mode)
        evidence = _evidence_level(row, min_clicks, min_orders)
        rows.append({
            "策略场景": scenario,
            "建议Campaign": campaign_name,
            "建议广告组": ad_group_name,
            "关键词": row.get("search_term", row.get("rank_root", "")),
            "匹配方式": match_type,
            "建议竞价": bid,
            "当前CPC": round(_num(row.get("cpc")), 2),
            "历史订单": int(_num(row.get("orders"))),
            "历史花费": round(_num(row.get("spend")), 2),
            "历史销售额": round(_num(row.get("sales")), 2),
            "历史ACOS": _pct(_num(row.get("acos"))),
            "证据等级": evidence,
            "触发依据": _trigger_basis(row, scenario, target_acos),
            "执行前检查": _execution_guardrail(scenario, evidence),
            "投放理由": _row_current_data(row),
        })
    return rows

def build_campaign_architecture_plan(frame: pd.DataFrame, settings: dict) -> dict[str, pd.DataFrame]:
    if frame.empty or "search_term" not in frame.columns:
        empty = pd.DataFrame()
        return {"campaign_plan": empty, "keyword_plan": empty}

    target_acos = float(settings.get("target_acos", 0.30) or 0.30)
    min_clicks = int(settings.get("min_clicks", 8) or 8)
    min_orders = int(settings.get("min_orders", 2) or 2)
    days = int(settings.get("analysis_days", settings.get("attribution_window_days", 30)) or 30)
    ranking_terms = [str(x).strip().lower() for x in settings.get("ranking_terms", []) if str(x).strip()]

    terms = aggregate_search_terms(frame, settings)
    if terms.empty:
        empty = pd.DataFrame()
        return {"campaign_plan": empty, "keyword_plan": empty}

    terms = terms.copy()
    terms["avg_order_value"] = terms.apply(lambda r: _num(r.get("sales")) / _num(r.get("orders"), 1) if _num(r.get("orders")) > 0 else _num(settings.get("product_price", 25)), axis=1)
    terms["product_price"] = float(settings.get("product_price", 25) or 25)
    if "bid" not in terms.columns:
        terms["bid"] = 0.0

    campaign_rows: list[dict[str, Any]] = []
    keyword_rows: list[dict[str, Any]] = []

    # 1. 降低 ACOS：把浪费词单独隔离成低价防守/否定审核池，不继续和优质词混在一起消耗预算。
    waste = terms[((terms["clicks"] >= min_clicks) & (terms["orders"] == 0)) | ((terms["orders"] > 0) & (terms["acos"] > target_acos * 1.25))].copy()
    if not waste.empty:
        waste = waste.sort_values(["spend", "clicks"], ascending=False).head(12).copy()
        waste["planned_bid"] = waste.apply(lambda r: _recommended_bid(r, target_acos, "cut"), axis=1)
        waste["match_type_plan"] = waste.apply(lambda r: "Negative Exact候选" if _num(r.get("orders")) == 0 else "Exact低价观察", axis=1)
        campaign_name = "SP-ACOS控制-浪费词隔离"
        ad_group_name = "AG-高花费低转化-低价观察"
        budget = max(5.0, min(25.0, waste["spend"].sum() / max(days, 1) * 0.6))
        campaign_rows.append(_campaign_row(
            "1 降低ACOS",
            campaign_name,
            "把高花费无订单和高ACOS词从主投放中隔离出来，低价观察或人工审核否定。",
            budget,
            ad_group_name,
            "Exact低价观察 + Negative Exact候选清单；不建议直接做Negative Phrase，除非确认整组词根不相关。",
            waste,
            f"这批词合计花费{_money(waste['spend'].sum())}，但订单少或ACOS高于目标，继续混在主广告里会拉高整体ACOS。",
            "先在原广告中下调/否定明确不相关词；核心相关词保留低价观察7天。",
            "P0" if waste["spend"].sum() >= 20 else "P1",
        ))
        keyword_rows.extend(_keyword_plan_rows(waste, campaign_name, ad_group_name, "1 降低ACOS", "Exact低价/否定审核", "cut", target_acos, min_clicks, min_orders))

    # 2. 低预算高回报：把已经出单且 ACOS 健康的词收割到小预算精准 Campaign。
    winners = terms[(terms["orders"] >= min_orders) & (terms["sales"] > 0) & (terms["acos"] <= target_acos)].copy()
    if not winners.empty:
        winners = winners.sort_values(["orders", "sales", "acos"], ascending=[False, False, True]).head(8).copy()
        winners["planned_bid"] = winners.apply(lambda r: _recommended_bid(r, target_acos, "scale"), axis=1)
        winners["match_type_plan"] = "Exact"
        root = _clean_name(winners.iloc[0].get("word_root") or winners.iloc[0].get("search_term"), "核心词")
        campaign_name = f"SP-精准收割-{root}"
        ad_group_name = f"AG-Exact-高ROI-{root}"
        budget = max(8.0, min(45.0, winners["spend"].sum() / max(days, 1) * 1.6))
        campaign_rows.append(_campaign_row(
            "2 低预算高回报",
            campaign_name,
            "用最少预算优先吃已经验证能转化的精准搜索词，稳定拿订单。",
            budget,
            ad_group_name,
            "一个广告组只放高转化Exact词；预算独立，不和测试词混用。",
            winners,
            f"这批词已有{int(winners['orders'].sum())}单，整体ACOS {_pct(winners['spend'].sum() / winners['sales'].sum() if winners['sales'].sum() else 0)}，适合单独收割。",
            "预算按历史日花费约1.6倍起步，观察3-7天；如果CPC快速上涨，回调10%-15%。",
            "P1",
        ))
        keyword_rows.extend(_keyword_plan_rows(winners, campaign_name, ad_group_name, "2 低预算高回报", "Exact", "scale", target_acos, min_clicks, min_orders))

        phrase = winners.copy()
        phrase["search_term"] = phrase["word_root"].fillna(phrase["search_term"])
        phrase = phrase.drop_duplicates("search_term").head(5).copy()
        phrase["planned_bid"] = phrase.apply(lambda r: max(0.2, round(_recommended_bid(r, target_acos, "scale") * 0.85, 2)), axis=1)
        phrase["match_type_plan"] = "Phrase"
        phrase_campaign = f"SP-词组扩展-{root}"
        phrase_ad_group = f"AG-Phrase-相关长尾-{root}"
        campaign_rows.append(_campaign_row(
            "2 低预算高回报",
            phrase_campaign,
            "用共同短语词根低成本覆盖更多长尾词，寻找新的Exact收割对象。",
            max(5.0, round(budget * 0.6, 2)),
            phrase_ad_group,
            "Phrase词组匹配，只放共同短语词根，不放过泛单词。",
            phrase,
            "这些共同短语词根来自已出单搜索词，相关性比普通广泛更高。",
            "Phrase广告要每天看搜索词，连续高点击无单的长尾词再加精准否定。",
            "P1",
        ))
        keyword_rows.extend(_keyword_plan_rows(phrase, phrase_campaign, phrase_ad_group, "2 低预算高回报", "Phrase", "scale", target_acos, min_clicks, min_orders))

    # 3. 推排名大词：围绕目标大词/共同短语建立单独预算池，允许短期 ACOS 高于利润型广告。
    root_map = build_phrase_root_map(terms, settings)
    terms["rank_root"] = terms["search_term"].map(lambda x: root_map.get(str(x), term_scene(str(x))))
    root_group = terms.groupby("rank_root", dropna=False).agg(
        search_term=("rank_root", "first"),
        sample_terms=("search_term", lambda x: " | ".join(list(x)[:8])),
        campaign=("campaign", lambda x: ", ".join(sorted({str(v) for v in x if str(v)}))[:220]),
        impressions=("impressions", "sum"),
        clicks=("clicks", "sum"),
        spend=("spend", "sum"),
        orders=("orders", "sum"),
        sales=("sales", "sum"),
    ).reset_index(drop=True)
    root_group = add_core_metrics(root_group, settings)
    root_group["avg_order_value"] = root_group.apply(lambda r: _num(r.get("sales")) / _num(r.get("orders"), 1) if _num(r.get("orders")) > 0 else _num(settings.get("product_price", 25)), axis=1)
    root_group["product_price"] = float(settings.get("product_price", 25) or 25)
    root_group["rank_score"] = root_group.apply(lambda r: _num(r.get("orders")) * 10 + _num(r.get("sales")) / 8 + _num(r.get("impressions")) / 1000 - _num(r.get("acos")) * 2, axis=1)
    if ranking_terms:
        root_group["rank_score"] += root_group["search_term"].map(lambda x: 40 if any(t in str(x).lower() or str(x).lower() in t for t in ranking_terms) else 0)
    rank_roots = root_group[(root_group["clicks"] >= max(5, min_clicks)) & (root_group["orders"] > 0)].sort_values(["rank_score", "orders", "sales"], ascending=False).head(5).copy()
    if not rank_roots.empty:
        rank_roots["planned_bid"] = rank_roots.apply(lambda r: _recommended_bid(r, target_acos, "rank"), axis=1)
        rank_roots["match_type_plan"] = "Exact + Phrase"
        root = _clean_name(rank_roots.iloc[0].get("search_term"), "排名词")
        campaign_name = f"SP-排名推进-{root}"
        exact_ad_group = f"AG-Exact-排名核心-{root}"
        phrase_ad_group = f"AG-Phrase-排名长尾-{root}"
        budget = max(15.0, min(90.0, rank_roots["spend"].sum() / max(days, 1) * 2.5))
        campaign_rows.append(_campaign_row(
            "3 推排名大词",
            campaign_name,
            "集中预算推进相关大词排名，提高核心词曝光和自然排名机会。",
            budget,
            exact_ad_group,
            "Exact放核心大词；Phrase放同短语长尾。两组共用一个排名推进Campaign。",
            rank_roots,
            f"这些共同短语已有{int(rank_roots['orders'].sum())}单基础，适合单独做排名推进，而不是放在低预算保利润广告里。",
            "排名推进允许短期ACOS高于目标，但必须设置独立预算上限，避免吞掉利润广告预算。",
            "P1",
        ))
        exact_rows = rank_roots.head(3).copy()
        keyword_rows.extend(_keyword_plan_rows(exact_rows, campaign_name, exact_ad_group, "3 推排名大词", "Exact", "rank", target_acos, min_clicks, min_orders))
        phrase_rows = rank_roots.copy()
        phrase_rows["planned_bid"] = phrase_rows["planned_bid"].map(lambda x: max(0.2, round(_num(x) * 0.85, 2)))
        keyword_rows.extend(_keyword_plan_rows(phrase_rows, campaign_name, phrase_ad_group, "3 推排名大词", "Phrase", "rank", target_acos, min_clicks, min_orders))

    campaign_plan = pd.DataFrame(campaign_rows)
    keyword_plan = pd.DataFrame(keyword_rows)
    if not campaign_plan.empty:
        priority = {"P0": 0, "P1": 1, "P2": 2}
        campaign_plan["_order"] = campaign_plan["优先级"].map(priority).fillna(9)
        campaign_plan = campaign_plan.sort_values(["策略场景", "_order", "建议日预算"], ascending=[True, True, False]).drop(columns=["_order"])
    if not keyword_plan.empty:
        keyword_plan = keyword_plan.sort_values(["策略场景", "建议Campaign", "建议广告组", "历史订单", "历史销售额"], ascending=[True, True, True, False, False])
    return {"campaign_plan": campaign_plan, "keyword_plan": keyword_plan}



def _format_keywords_for_strategy(keyword_plan: pd.DataFrame, scenarios: list[str] | None = None, limit: int = 10) -> str:
    if keyword_plan.empty:
        return "暂无关键词清单"
    df = keyword_plan.copy()
    if scenarios:
        df = df[df["策略场景"].isin(scenarios)]
    if df.empty:
        return "暂无关键词清单"
    lines = []
    for _, kw in df.head(limit).iterrows():
        lines.append(f"{kw.get('关键词')}（{kw.get('匹配方式')}，建议竞价${_num(kw.get('建议竞价')):.2f}）")
    return "\n".join(lines)


def _keyword_placement_text(
    keyword_plan: pd.DataFrame,
    campaign_plan: pd.DataFrame,
    scenarios: list[str] | None = None,
    limit_per_group: int = 8,
) -> str:
    if keyword_plan.empty:
        return "暂无关键词投放清单"

    df = keyword_plan.copy()
    cp = campaign_plan.copy() if not campaign_plan.empty else pd.DataFrame()
    if scenarios:
        df = df[df["策略场景"].isin(scenarios)]
        if not cp.empty:
            cp = cp[cp["策略场景"].isin(scenarios)]
    if df.empty:
        return "暂无关键词投放清单"

    budget_map = {}
    objective_map = {}
    if not cp.empty:
        budget_map = cp.set_index("建议新建Campaign")["建议日预算"].to_dict()
        objective_map = cp.set_index("建议新建Campaign")["Campaign目标"].to_dict()

    lines: list[str] = []
    sorted_df = df.sort_values(
        ["建议Campaign", "建议广告组", "匹配方式", "历史订单", "历史销售额"],
        ascending=[True, True, True, False, False],
    )

    for campaign, campaign_group in sorted_df.groupby("建议Campaign", dropna=False):
        budget = _num(budget_map.get(campaign))
        objective = objective_map.get(campaign, "")
        lines.append(f"【单独给预算】Campaign：{campaign}")
        lines.append(f"建议日预算：${budget:.2f}")
        if objective:
            lines.append(f"这个Campaign的目的：{objective}")

        for (ad_group, match_type), group in campaign_group.groupby(["建议广告组", "匹配方式"], dropna=False):
            lines.append(f"  广告组：{ad_group}")
            lines.append(f"  匹配方式：{match_type}")
            lines.append("  放在这个广告组里的词：")
            for _, kw in group.head(limit_per_group).iterrows():
                lines.append(
                    f"  - {kw.get('关键词')}｜建议竞价 ${_num(kw.get('建议竞价')):.2f}｜历史订单 {int(_num(kw.get('历史订单')))}｜历史ACOS {kw.get('历史ACOS', '-')}"
                )
            rest = len(group) - limit_per_group
            if rest > 0:
                lines.append(f"  - 另外还有 {rest} 个同打法关键词，完整看'关键词投放清单'页。")
        lines.append("")

    lines.append("规则：同一个广告组只放同一目标、同一匹配方式的词；需要单独推排名的词放独立Campaign并单独给预算；低预算收割词不要和推排名词混在一个广告组。")
    return "\n".join(lines).strip()

def _campaign_names(campaign_plan: pd.DataFrame, scenarios: list[str] | None = None) -> str:
    if campaign_plan.empty:
        return "暂无建议Campaign"
    df = campaign_plan.copy()
    if scenarios:
        df = df[df["策略场景"].isin(scenarios)]
    if df.empty:
        return "暂无建议Campaign"
    lines = []
    for _, row in df.iterrows():
        lines.append(f"{row.get('建议新建Campaign')}：日预算${_num(row.get('建议日预算')):.2f}，广告组：{row.get('建议广告组')}")
    return "\n".join(lines)


def _budget_total(campaign_plan: pd.DataFrame, scenarios: list[str] | None = None) -> float:
    if campaign_plan.empty:
        return 0.0
    df = campaign_plan.copy()
    if scenarios:
        df = df[df["策略场景"].isin(scenarios)]
    return round(float(df["建议日预算"].sum()) if "建议日预算" in df.columns else 0.0, 2)


def build_readable_strategy_brief(campaign_plan: pd.DataFrame, keyword_plan: pd.DataFrame) -> pd.DataFrame:
    columns = ["策略类型", "适合什么时候用", "核心思路", "建议广告结构", "关键词怎么放", "预算建议", "风险与观察重点"]
    if campaign_plan.empty:
        return pd.DataFrame(columns=columns)

    all_scenarios = campaign_plan["策略场景"].astype(str).tolist()
    reduce_scenarios = [s for s in all_scenarios if s.startswith("1")]
    roi_scenarios = [s for s in all_scenarios if s.startswith("2")]
    rank_scenarios = [s for s in all_scenarios if s.startswith("3")]

    rows: list[dict[str, Any]] = []

    rows.append({
        "策略类型": "1 最优解：先控浪费，再收割，再小范围推排名",
        "适合什么时候用": "你不想只追求某一个单点目标，而是想让账户更健康：先止损，再把确定能赚钱的词单独吃住，最后用独立预算推核心词。",
        "核心思路": "这是我看完整盘数据后更推荐的综合打法：保留三类预算池，但不要混在同一个Campaign里。浪费词低价隔离，高ROI词精准收割，核心大词单独推排名。",
        "建议广告结构": _campaign_names(campaign_plan, None),
        "关键词怎么放": _keyword_placement_text(keyword_plan, campaign_plan, None, 8),
        "预算建议": f"四类Campaign合计建议日预算约${_budget_total(campaign_plan):.2f}。如果你想稳一点，可以先只执行低预算高回报和ACOS控制；如果要抢排名，再打开推排名Campaign。",
        "风险与观察重点": "这个方案最均衡，但需要你把不同目标的Campaign分开看数据，不要用同一个ACOS标准评价推排名广告和利润型广告。",
    })

    rows.append({
        "策略类型": "2 降低ACOS：先止损，控制无效花费",
        "适合什么时候用": "当你觉得广告花费太快、ACOS太高、预算被很多无订单词吃掉时，优先用这个方案。",
        "核心思路": "不先扩量，先把高点击无订单、高ACOS词从主广告里隔离出来，降竞价或加入否定审核，减少浪费。",
        "建议广告结构": _campaign_names(campaign_plan, reduce_scenarios),
        "关键词怎么放": _keyword_placement_text(keyword_plan, campaign_plan, reduce_scenarios, 8),
        "预算建议": f"建议日预算约${_budget_total(campaign_plan, reduce_scenarios):.2f}，只保留低预算观察，不要继续给这类词大预算。",
        "风险与观察重点": "不要把所有无订单词直接Phrase否定。核心相关词先低价观察7天；明显不相关、有点击无订单的词再做Negative Exact。",
    })

    rows.append({
        "策略类型": "3 低预算高回报：用小钱稳定拿订单",
        "适合什么时候用": "当你预算有限，想用尽量少的钱拿到确定性订单，而不是盲目扩大曝光时，用这个方案。",
        "核心思路": "只把已经出单、ACOS健康、相关性强的词拿出来，单独建Exact收割Campaign；再用Phrase小预算扩展同短语长尾。",
        "建议广告结构": _campaign_names(campaign_plan, roi_scenarios),
        "关键词怎么放": _keyword_placement_text(keyword_plan, campaign_plan, roi_scenarios, 8),
        "预算建议": f"建议日预算约${_budget_total(campaign_plan, roi_scenarios):.2f}。先用小预算跑3-7天，订单稳定且ACOS低于目标后，再加预算20%-30%。",
        "风险与观察重点": "不要把测试词、泛词混进这个Campaign，否则会污染数据。这个方案的广告组应该干净，只放已经证明能转化的词。",
    })

    rows.append({
        "策略类型": "4 推核心关键词：集中预算推排名",
        "适合什么时候用": "当你的目标不是短期最低ACOS，而是想把某些主要关键词排名推到更前面时，用这个方案。",
        "核心思路": "围绕核心词和共同短语词根单独建排名推进Campaign，Exact打核心词，Phrase覆盖相关长尾，预算和利润型广告分开。",
        "建议广告结构": _campaign_names(campaign_plan, rank_scenarios),
        "关键词怎么放": _keyword_placement_text(keyword_plan, campaign_plan, rank_scenarios, 8),
        "预算建议": f"建议日预算约${_budget_total(campaign_plan, rank_scenarios):.2f}。这是排名预算池，不能拿它和保利润Campaign用同一个ACOS标准比较。",
        "风险与观察重点": "推排名会牺牲短期ACOS。重点看曝光、点击、订单和核心词排名是否上升；如果只涨花费不涨订单，7-14天内要收缩。",
    })

    return pd.DataFrame(rows, columns=columns)






def build_recommendation_audit(frame: pd.DataFrame, campaign_plan: pd.DataFrame, keyword_plan: pd.DataFrame, settings: dict) -> pd.DataFrame:
    target_acos = float(settings.get("target_acos", 0.30) or 0.30)
    min_clicks = int(settings.get("min_clicks", 8) or 8)
    rows = int(len(frame)) if frame is not None else 0
    terms = int(frame["search_term"].nunique()) if frame is not None and not frame.empty and "search_term" in frame.columns else 0
    clicks = float(frame.get("clicks", pd.Series(dtype=float)).sum()) if frame is not None and not frame.empty else 0.0
    orders = float(frame.get("orders", pd.Series(dtype=float)).sum()) if frame is not None and not frame.empty else 0.0
    spend = float(frame.get("spend", pd.Series(dtype=float)).sum()) if frame is not None and not frame.empty else 0.0
    sales = float(frame.get("sales", pd.Series(dtype=float)).sum()) if frame is not None and not frame.empty else 0.0
    acos = spend / sales if sales else 0.0

    high = medium = low = 0
    if keyword_plan is not None and not keyword_plan.empty and "证据等级" in keyword_plan.columns:
        counts = keyword_plan["证据等级"].value_counts().to_dict()
        high = int(counts.get("高", 0))
        medium = int(counts.get("中", 0))
        low = int(counts.get("低", 0))

    safety = "可执行，但建议人工审核后分批操作"
    if clicks < min_clicks * 3 or terms < 5:
        safety = "数据量偏少，只建议观察或小预算测试"
    elif low > high + medium:
        safety = "低证据建议较多，不建议一次性大幅调整"

    return pd.DataFrame([
        {"检查项": "本次数据规模", "结果": f"{rows}行，{terms}个搜索词，点击{int(clicks)}，订单{int(orders)}", "说明": "建议不会脱离报表生成；点击和订单越多，判断越可靠。"},
        {"检查项": "整体表现", "结果": f"花费{_money(spend)}，销售额{_money(sales)}，ACOS {_pct(acos)}", "说明": f"当前目标ACOS为{_pct(target_acos)}，系统会优先按目标ACOS和转化数据约束竞价。"},
        {"检查项": "建议证据分布", "结果": f"高证据{high}条，中证据{medium}条，低证据{low}条", "说明": "低证据建议不会建议直接暂停或大幅加预算，应先观察。"},
        {"检查项": "安全结论", "结果": safety, "说明": "第一版只输出建议，不会自动修改亚马逊后台。否定词、调预算、调竞价都需要人工确认。"},
    ])


