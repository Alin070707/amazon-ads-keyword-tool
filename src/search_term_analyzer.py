from __future__ import annotations

import re

import pandas as pd

from .field_mapper import classify_term
from .metric_calculator import add_core_metrics
from .utils import contains_any


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "best", "by", "for", "from", "in", "into",
    "of", "on", "or", "the", "to", "with", "without", "new", "amazon", "prime",
}

ASIN_PATTERN = re.compile(r"\bB0[A-Z0-9]{8}\b", re.IGNORECASE)


def normalize_search_term(term: str) -> str:
    return re.sub(r"\s+", " ", str(term or "").strip().lower())


def stem_token(token: str) -> str:
    token = re.sub(r"[^a-z0-9]", "", token.lower())
    if len(token) <= 3:
        return token
    for suffix in ["ing", "ers", "er", "ies", "ied", "ed", "es", "s"]:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            if suffix in {"ies", "ied"}:
                token = token[:-3] + "y"
            else:
                token = token[: -len(suffix)]
            if len(token) >= 2 and token[-1] == token[-2]:
                token = token[:-1]
            return token
    return token


def term_tokens(term: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+", normalize_search_term(term))
    return [stem_token(t) for t in tokens if stem_token(t) and stem_token(t) not in STOPWORDS]


def term_root(term: str) -> str:
    tokens = term_tokens(term)
    if not tokens:
        return normalize_search_term(term)
    return max(tokens, key=lambda x: (len(x), -tokens.index(x)))


def term_scene(term: str) -> str:
    tokens = term_tokens(term)
    if not tokens:
        return normalize_search_term(term)
    root = term_root(term)
    modifiers = [t for t in tokens if t != root]
    return " ".join([root] + modifiers[:3])


def extract_asin_from_text(value: object) -> str:
    match = ASIN_PATTERN.search(str(value or ""))
    return match.group(0).upper() if match else ""


def add_product_group_columns(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    df = frame.copy()
    if "asin" not in df.columns:
        df["asin"] = ""
    df["asin"] = df["asin"].fillna("").astype(str).str.upper().str.strip()

    if "sku" in df.columns and settings.get("asin_sku_map"):
        sku_map = {str(k).strip(): str(v).strip().upper() for k, v in settings.get("asin_sku_map", {}).items()}
        missing = df["asin"].eq("")
        df.loc[missing, "asin"] = df.loc[missing, "sku"].astype(str).str.strip().map(sku_map).fillna("")

    text_cols = [c for c in ["campaign", "ad_group", "targeting", "search_term"] if c in df.columns]
    for col in text_cols:
        missing = df["asin"].eq("")
        if missing.any():
            df.loc[missing, "asin"] = df.loc[missing, col].map(extract_asin_from_text)

    groups = settings.get("product_asin_groups", {}) or {}
    asin_to_group = {}
    for group_name, asins in groups.items():
        if isinstance(asins, str):
            asins = [x.strip() for x in re.split(r"[,，\s]+", asins) if x.strip()]
        for asin in asins or []:
            asin_to_group[str(asin).strip().upper()] = str(group_name)

    df["product_group"] = df["asin"].map(asin_to_group).fillna("")
    df.loc[df["product_group"].eq("") & df["asin"].ne(""), "product_group"] = "未分组-" + df.loc[df["product_group"].eq("") & df["asin"].ne(""), "asin"]
    df.loc[df["product_group"].eq(""), "product_group"] = "未识别ASIN"
    return df


def categorize_search_terms(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    if "search_term" not in frame.columns or frame.empty:
        return pd.DataFrame()
    df = frame.copy()
    target = settings.get("target_acos", 0.30)
    min_clicks = settings.get("min_clicks", 8)
    min_orders = settings.get("min_orders", 2)
    df["search_term_category"] = "数据不足词"
    df.loc[(df["orders"] >= min_orders) & (df["acos"] <= target), "search_term_category"] = "建议收割词"
    df.loc[(df["clicks"] >= min_clicks) & (df["orders"] == 0), "search_term_category"] = "高花费无订单词"
    df.loc[(df["orders"] > 0) & (df["acos"] > target * 1.3), "search_term_category"] = "高ACOS词"
    df.loc[(df["orders"] > 0) & (df["acos"] <= target * 0.7), "search_term_category"] = "优质词"
    df.loc[(df["clicks"] >= min_clicks) & (df["orders"] > 0) & (df["acos"] <= target), "search_term_category"] = "潜力词"
    return df


def aggregate_search_terms(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    if "search_term" not in frame.columns or frame.empty:
        return pd.DataFrame()
    work = frame.copy()
    for col in ["campaign", "ad_group", "targeting", "match_type", "search_term"]:
        if col not in work.columns:
            work[col] = ""
    grouped = work.groupby("search_term", dropna=False).agg(
        campaign=("campaign", lambda x: ", ".join(sorted({str(v) for v in x if str(v)}))[:300]),
        ad_group=("ad_group", lambda x: ", ".join(sorted({str(v) for v in x if str(v)}))[:300]),
        targeting=("targeting", lambda x: ", ".join(sorted({str(v) for v in x if str(v)}))[:300]),
        match_type=("match_type", lambda x: ", ".join(sorted({str(v) for v in x if str(v)}))[:120]),
        impressions=("impressions", "sum"),
        clicks=("clicks", "sum"),
        spend=("spend", "sum"),
        orders=("orders", "sum"),
        sales=("sales", "sum"),
    ).reset_index()
    grouped = add_core_metrics(grouped, settings)
    grouped["word_root"] = grouped["search_term"].map(term_root)
    grouped["scene_key"] = grouped["search_term"].map(term_scene)
    grouped["term_type"] = grouped["search_term"].map(
        lambda x: classify_term(
            x,
            settings.get("brand_terms", []),
            settings.get("competitor_terms", []),
            settings.get("core_terms", []),
        )
    )
    grouped["is_protected"] = grouped["search_term"].map(
        lambda x: contains_any(x, settings.get("brand_terms", []) + settings.get("core_terms", []))
    )
    grouped["seller_explanation"] = grouped.apply(_seller_explanation, axis=1, args=(settings,))
    return grouped


def product_group_keyword_tables(frame: pd.DataFrame, settings: dict) -> dict[str, pd.DataFrame]:
    empty = pd.DataFrame()
    if frame.empty or "search_term" not in frame.columns:
        return {
            "product_group_terms": empty,
            "product_group_roots": empty,
            "asin_summary": empty,
        }

    work = add_product_group_columns(frame, settings)
    for col in ["campaign", "ad_group", "targeting", "match_type", "asin", "product_group"]:
        if col not in work.columns:
            work[col] = ""

    term_group = work.groupby(["product_group", "search_term"], dropna=False).agg(
        asin_list=("asin", lambda x: ", ".join(sorted({str(v) for v in x if str(v)}))[:300]),
        campaign=("campaign", lambda x: ", ".join(sorted({str(v) for v in x if str(v)}))[:300]),
        ad_group=("ad_group", lambda x: ", ".join(sorted({str(v) for v in x if str(v)}))[:300]),
        impressions=("impressions", "sum"),
        clicks=("clicks", "sum"),
        spend=("spend", "sum"),
        orders=("orders", "sum"),
        sales=("sales", "sum"),
    ).reset_index()
    term_group = add_core_metrics(term_group, settings)
    term_group["word_root"] = term_group["search_term"].map(term_root)
    term_group["scene_key"] = term_group["search_term"].map(term_scene)

    target = float(settings.get("target_acos", 0.30))
    min_clicks = int(settings.get("min_clicks", 8))
    min_orders = int(settings.get("min_orders", 2))
    term_group["keyword_status"] = "观察"
    term_group.loc[(term_group["orders"] >= min_orders) & (term_group["acos"] <= target), "keyword_status"] = "表现好，可扩量"
    term_group.loc[(term_group["clicks"] >= min_clicks) & (term_group["orders"] == 0), "keyword_status"] = "候选否定"
    term_group.loc[(term_group["orders"] > 0) & (term_group["acos"] > target * 1.3), "keyword_status"] = "有转化但ACOS高"
    term_group["suggested_action"] = term_group.apply(_product_term_action, axis=1, args=(target, min_clicks, min_orders))

    root_rows = []
    for (product_group, root), grp in term_group.groupby(["product_group", "word_root"], dropna=False):
        if not root:
            continue
        total_clicks = grp["clicks"].sum()
        if len(grp) < 2 and total_clicks < min_clicks:
            continue
        total_spend = grp["spend"].sum()
        total_sales = grp["sales"].sum()
        total_orders = grp["orders"].sum()
        acos = 0 if total_sales == 0 else total_spend / total_sales
        good_terms = grp[(grp["orders"] >= 1) & (grp["acos"] <= target)]["search_term"].head(10).tolist()
        bad_terms = grp[(grp["clicks"] >= min_clicks) & (grp["orders"] == 0)]["search_term"].head(10).tolist()
        sample_terms = grp.sort_values(["orders", "sales", "clicks"], ascending=False)["search_term"].head(20).tolist()
        action = "同产品组同词根：建议放入同一个广告组统一测试"
        if good_terms:
            action = "同产品组同词根表现好：建议建立精准/词组广告组集中放量"
        if bad_terms and not good_terms:
            action = "同产品组同词根转化弱：建议降价测试，明显不相关再否定"
        root_rows.append({
            "product_group": product_group,
            "word_root": root,
            "suggested_ad_group_name": f"{product_group}_{root}_关键词组",
            "terms_count": len(grp),
            "sample_terms": " | ".join(sample_terms),
            "good_terms": " | ".join(good_terms),
            "negative_candidates": " | ".join(bad_terms),
            "clicks": total_clicks,
            "orders": total_orders,
            "spend": total_spend,
            "sales": total_sales,
            "acos": acos,
            "suggested_action": action,
        })

    asin_summary = work.groupby(["product_group", "asin"], dropna=False).agg(
        search_terms=("search_term", "nunique"),
        impressions=("impressions", "sum"),
        clicks=("clicks", "sum"),
        spend=("spend", "sum"),
        orders=("orders", "sum"),
        sales=("sales", "sum"),
    ).reset_index()
    asin_summary = add_core_metrics(asin_summary, settings)

    product_group_roots = pd.DataFrame(root_rows)
    if not product_group_roots.empty:
        product_group_roots = product_group_roots.sort_values(["product_group", "orders", "sales", "clicks"], ascending=[True, False, False, False])
    if not term_group.empty:
        term_group = term_group.sort_values(["product_group", "orders", "sales", "spend"], ascending=[True, False, False, False])
    return {
        "product_group_terms": term_group,
        "product_group_roots": product_group_roots,
        "asin_summary": asin_summary,
    }


def _product_term_action(row: pd.Series, target: float, min_clicks: int, min_orders: int) -> str:
    if row.get("orders", 0) >= min_orders and row.get("acos", 0) <= target:
        return "该产品组下表现好，建议收割为精准词，并优先用于同组ASIN。"
    if row.get("clicks", 0) >= min_clicks and row.get("orders", 0) == 0:
        return "该产品组下点击达标但无订单，建议人工确认相关性后否定或降价。"
    if row.get("orders", 0) > 0 and row.get("acos", 0) > target * 1.3:
        return "该产品组下有转化但ACOS偏高，建议保留流量并降低竞价。"
    return "数据量不足或表现接近目标，建议继续观察。"


def _empty_term_table() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "search_term", "word_root", "scene_key", "term_type", "campaign", "ad_group",
            "impressions", "clicks", "spend", "orders", "sales", "ctr", "cvr", "acos",
            "recommended_action", "why", "seller_explanation",
        ]
    )


def search_term_insight_tables(frame: pd.DataFrame, settings: dict) -> dict[str, pd.DataFrame]:
    grouped = aggregate_search_terms(frame, settings)
    if grouped.empty:
        empty = _empty_term_table()
        return {
            "good_terms": empty,
            "negative_terms": empty,
            "ad_group_clusters": pd.DataFrame(),
            "root_longtails": pd.DataFrame(),
        }

    target = float(settings.get("target_acos", 0.30))
    min_clicks = int(settings.get("min_clicks", 8))
    min_orders = int(settings.get("min_orders", 2))

    good_terms = grouped[
        (grouped["orders"] >= min_orders)
        & (grouped["acos"] <= target)
        & (grouped["sales"] > 0)
    ].copy()
    if not good_terms.empty:
        good_terms["recommended_action"] = "表现好：建议单独做Exact精准投放，或提高预算/竞价承接更多流量"
        good_terms["why"] = good_terms.apply(
            lambda r: f"该词有{int(r['orders'])}单，ACOS {r['acos']:.1%}不高于目标{target:.1%}，销售额{r['sales']:.2f}。",
            axis=1,
        )
    else:
        good_terms = _empty_term_table()

    negative_terms = grouped[
        (grouped["clicks"] >= min_clicks)
        & (grouped["orders"] == 0)
        & (grouped["spend"] > 0)
        & (~grouped["is_protected"])
    ].copy()
    if not negative_terms.empty:
        negative_terms["recommended_action"] = "建议否定：先人工确认相关性，明显不相关做Negative Exact；词根明显不相关再考虑Negative Phrase"
        negative_terms["why"] = negative_terms.apply(
            lambda r: f"该词点击{int(r['clicks'])}次、花费{r['spend']:.2f}，但没有订单，已达到判断门槛。",
            axis=1,
        )
    else:
        negative_terms = _empty_term_table()

    cluster_rows = []
    for root, grp in grouped.groupby("word_root", dropna=False):
        if not root or len(grp) < 2:
            continue
        total_clicks = grp["clicks"].sum()
        if total_clicks < max(min_clicks, 3):
            continue
        total_orders = grp["orders"].sum()
        total_spend = grp["spend"].sum()
        total_sales = grp["sales"].sum()
        acos = 0 if total_sales == 0 else total_spend / total_sales
        terms = grp.sort_values(["orders", "sales", "clicks"], ascending=False)["search_term"].head(15).tolist()
        best_terms = grp[(grp["orders"] > 0) & (grp["acos"] <= target)]["search_term"].head(8).tolist()
        bad_terms = grp[(grp["clicks"] >= min_clicks) & (grp["orders"] == 0) & (~grp["is_protected"])]["search_term"].head(8).tolist()
        recommendation = "同词根/同场景：建议放进同一个广告组，统一测试和控价"
        if best_terms:
            recommendation = "同词根表现不错：建议建立一个独立精准/词组广告组集中放量"
        if bad_terms and not best_terms:
            recommendation = "同词根整体转化差：建议集中降价测试，明显不相关词再否定"
        cluster_rows.append({
            "word_root": root,
            "suggested_ad_group_name": f"AG_{root}_theme",
            "terms_count": len(grp),
            "sample_terms": " | ".join(terms),
            "good_terms": " | ".join(best_terms),
            "negative_candidates": " | ".join(bad_terms),
            "clicks": total_clicks,
            "orders": total_orders,
            "spend": total_spend,
            "sales": total_sales,
            "acos": acos,
            "recommended_action": recommendation,
            "why": f"这些词都围绕“{root}”这个词根，合计点击{int(total_clicks)}次、订单{int(total_orders)}单、ACOS {acos:.1%}。",
        })

    ad_group_clusters = pd.DataFrame(cluster_rows)
    root_longtails = ad_group_clusters.copy()
    if not ad_group_clusters.empty:
        ad_group_clusters = ad_group_clusters.sort_values(["terms_count", "orders", "sales"], ascending=False)
        root_longtails = root_longtails.sort_values(["orders", "sales", "clicks"], ascending=False)

    cols = list(_empty_term_table().columns)
    if not good_terms.empty:
        good_terms = good_terms.sort_values(["orders", "sales"], ascending=False)[[c for c in cols if c in good_terms.columns]]
    if not negative_terms.empty:
        negative_terms = negative_terms.sort_values(["spend", "clicks"], ascending=False)[[c for c in cols if c in negative_terms.columns]]
    return {
        "good_terms": good_terms,
        "negative_terms": negative_terms,
        "ad_group_clusters": ad_group_clusters,
        "root_longtails": root_longtails,
    }


def _seller_explanation(row: pd.Series, settings: dict) -> str:
    target = float(settings.get("target_acos", 0.30))
    return (
        f"搜索词“{row.get('search_term', '')}”：曝光{int(row.get('impressions', 0) or 0)}，"
        f"点击{int(row.get('clicks', 0) or 0)}，花费{row.get('spend', 0):.2f}，"
        f"订单{int(row.get('orders', 0) or 0)}，销售额{row.get('sales', 0):.2f}，"
        f"ACOS {row.get('acos', 0):.1%}，目标ACOS {target:.1%}。"
    )
