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




def term_phrase_tokens(term: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+", normalize_search_term(term))
    return [t.lower() for t in tokens if t.lower() not in STOPWORDS]


def term_ngrams(term: str, min_n: int = 2, max_n: int = 5) -> list[str]:
    tokens = term_phrase_tokens(term)
    phrases: list[str] = []
    upper = min(max_n, len(tokens))
    for n in range(upper, min_n - 1, -1):
        for i in range(0, len(tokens) - n + 1):
            phrase = " ".join(tokens[i : i + n])
            if len(phrase) >= 5:
                phrases.append(phrase)
    return phrases


def build_phrase_root_map(grouped: pd.DataFrame, settings: dict) -> dict[str, str]:
    if grouped.empty or "search_term" not in grouped.columns:
        return {}
    target = float(settings.get("target_acos", 0.30))
    min_clicks = int(settings.get("min_clicks", 8))
    phrase_stats: dict[str, dict[str, float | set[str]]] = {}

    for _, row in grouped.iterrows():
        term = str(row.get("search_term", ""))
        orders = float(row.get("orders", 0) or 0)
        sales = float(row.get("sales", 0) or 0)
        clicks = float(row.get("clicks", 0) or 0)
        acos = float(row.get("acos", 0) or 0)
        is_good = orders > 0 and sales > 0 and (acos <= target or clicks >= min_clicks)
        weight = 1.0 + orders * 8 + sales / 20 + clicks / 20
        if is_good:
            weight *= 2.5
        for phrase in term_ngrams(term):
            stats = phrase_stats.setdefault(
                phrase,
                {"terms": set(), "good_terms": set(), "orders": 0.0, "sales": 0.0, "clicks": 0.0, "score": 0.0},
            )
            stats["terms"].add(term)  # type: ignore[index]
            if is_good:
                stats["good_terms"].add(term)  # type: ignore[index]
            stats["orders"] = float(stats["orders"]) + orders
            stats["sales"] = float(stats["sales"]) + sales
            stats["clicks"] = float(stats["clicks"]) + clicks
            stats["score"] = float(stats["score"]) + weight + len(phrase.split()) * 1.5

    candidates: dict[str, float] = {}
    for phrase, stats in phrase_stats.items():
        term_count = len(stats["terms"])  # type: ignore[arg-type]
        good_count = len(stats["good_terms"])  # type: ignore[arg-type]
        if term_count < 2:
            continue
        if good_count == 0 and float(stats["clicks"]) < min_clicks:
            continue
        score = float(stats["score"]) + term_count * 5 + good_count * 12 + len(phrase.split()) * 4
        candidates[phrase] = score

    root_map: dict[str, str] = {}
    for term in grouped["search_term"].fillna("").astype(str):
        term_candidates = [p for p in term_ngrams(term) if p in candidates]
        if term_candidates:
            root_map[term] = max(term_candidates, key=lambda p: (len(p.split()), candidates[p], len(p)))
        else:
            scene = term_scene(term)
            parts = scene.split()
            root_map[term] = " ".join(parts[: min(3, len(parts))]) if len(parts) >= 2 else term_root(term)
    return root_map

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
            asins = [x.strip() for x in re.split(r"[,ï¼Œ\s]+", asins) if x.strip()]
        for asin in asins or []:
            asin_to_group[str(asin).strip().upper()] = str(group_name)

    df["product_group"] = df["asin"].map(asin_to_group).fillna("")
    df.loc[df["product_group"].eq("") & df["asin"].ne(""), "product_group"] = "æœªåˆ†ç»„-" + df.loc[df["product_group"].eq("") & df["asin"].ne(""), "asin"]
    df.loc[df["product_group"].eq(""), "product_group"] = "æœªè¯†åˆ«ASIN"
    return df


def categorize_search_terms(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    if "search_term" not in frame.columns or frame.empty:
        return pd.DataFrame()
    df = frame.copy()
    target = settings.get("target_acos", 0.30)
    min_clicks = settings.get("min_clicks", 8)
    min_orders = settings.get("min_orders", 2)
    df["search_term_category"] = "æ•°æ®ä¸è¶³è¯"
    df.loc[(df["orders"] >= min_orders) & (df["acos"] <= target), "search_term_category"] = "å»ºè®®æ”¶å‰²è¯"
    df.loc[(df["clicks"] >= min_clicks) & (df["orders"] == 0), "search_term_category"] = "é«˜èŠ±è´¹æ— è®¢å•è¯"
    df.loc[(df["orders"] > 0) & (df["acos"] > target * 1.3), "search_term_category"] = "é«˜ACOSè¯"
    df.loc[(df["orders"] > 0) & (df["acos"] <= target * 0.7), "search_term_category"] = "ä¼˜è´¨è¯"
    df.loc[(df["clicks"] >= min_clicks) & (df["orders"] > 0) & (df["acos"] <= target), "search_term_category"] = "æ½œåŠ›è¯"
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
    grouped["single_word_root"] = grouped["search_term"].map(term_root)
    phrase_root_map = build_phrase_root_map(grouped, settings)
    grouped["word_root"] = grouped["search_term"].map(lambda x: phrase_root_map.get(str(x), term_scene(str(x))))
    grouped["phrase_root"] = grouped["word_root"]
    grouped["scene_key"] = grouped["word_root"]
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
    term_group["single_word_root"] = term_group["search_term"].map(term_root)
    phrase_root_map = build_phrase_root_map(term_group, settings)
    term_group["word_root"] = term_group["search_term"].map(lambda x: phrase_root_map.get(str(x), term_scene(str(x))))
    term_group["phrase_root"] = term_group["word_root"]
    term_group["scene_key"] = term_group["word_root"]

    target = float(settings.get("target_acos", 0.30))
    min_clicks = int(settings.get("min_clicks", 8))
    min_orders = int(settings.get("min_orders", 2))
    term_group["keyword_status"] = "è§‚å¯Ÿ"
    term_group.loc[(term_group["orders"] >= min_orders) & (term_group["acos"] <= target), "keyword_status"] = "è¡¨çŽ°å¥½ï¼Œå¯æ‰©é‡"
    term_group.loc[(term_group["clicks"] >= min_clicks) & (term_group["orders"] == 0), "keyword_status"] = "å€™é€‰å¦å®š"
    term_group.loc[(term_group["orders"] > 0) & (term_group["acos"] > target * 1.3), "keyword_status"] = "æœ‰è½¬åŒ–ä½†ACOSé«˜"
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
        action = "åŒäº§å“ç»„åŒè¯æ ¹ï¼šå»ºè®®æ”¾å…¥åŒä¸€ä¸ªå¹¿å‘Šç»„ç»Ÿä¸€æµ‹è¯•"
        if good_terms:
            action = "åŒäº§å“ç»„åŒè¯æ ¹è¡¨çŽ°å¥½ï¼šå»ºè®®å»ºç«‹ç²¾å‡†/è¯ç»„å¹¿å‘Šç»„é›†ä¸­æ”¾é‡"
        if bad_terms and not good_terms:
            action = "åŒäº§å“ç»„åŒè¯æ ¹è½¬åŒ–å¼±ï¼šå»ºè®®é™ä»·æµ‹è¯•ï¼Œæ˜Žæ˜¾ä¸ç›¸å…³å†å¦å®š"
        root_rows.append({
            "product_group": product_group,
            "word_root": root,
            "suggested_ad_group_name": f"{product_group}_{root}_å…³é”®è¯ç»„",
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
        return "è¯¥äº§å“ç»„ä¸‹è¡¨çŽ°å¥½ï¼Œå»ºè®®æ”¶å‰²ä¸ºç²¾å‡†è¯ï¼Œå¹¶ä¼˜å…ˆç”¨äºŽåŒç»„ASINã€‚"
    if row.get("clicks", 0) >= min_clicks and row.get("orders", 0) == 0:
        return "è¯¥äº§å“ç»„ä¸‹ç‚¹å‡»è¾¾æ ‡ä½†æ— è®¢å•ï¼Œå»ºè®®äººå·¥ç¡®è®¤ç›¸å…³æ€§åŽå¦å®šæˆ–é™ä»·ã€‚"
    if row.get("orders", 0) > 0 and row.get("acos", 0) > target * 1.3:
        return "è¯¥äº§å“ç»„ä¸‹æœ‰è½¬åŒ–ä½†ACOSåé«˜ï¼Œå»ºè®®ä¿ç•™æµé‡å¹¶é™ä½Žç«žä»·ã€‚"
    return "æ•°æ®é‡ä¸è¶³æˆ–è¡¨çŽ°æŽ¥è¿‘ç›®æ ‡ï¼Œå»ºè®®ç»§ç»­è§‚å¯Ÿã€‚"


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
        good_terms["recommended_action"] = "è¡¨çŽ°å¥½ï¼šå»ºè®®å•ç‹¬åšExactç²¾å‡†æŠ•æ”¾ï¼Œæˆ–æé«˜é¢„ç®—/ç«žä»·æ‰¿æŽ¥æ›´å¤šæµé‡"
        good_terms["why"] = good_terms.apply(
            lambda r: f"è¯¥è¯æœ‰{int(r['orders'])}å•ï¼ŒACOS {r['acos']:.1%}ä¸é«˜äºŽç›®æ ‡{target:.1%}ï¼Œé”€å”®é¢{r['sales']:.2f}ã€‚",
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
        negative_terms["recommended_action"] = "å»ºè®®å¦å®šï¼šå…ˆäººå·¥ç¡®è®¤ç›¸å…³æ€§ï¼Œæ˜Žæ˜¾ä¸ç›¸å…³åšNegative Exactï¼›è¯æ ¹æ˜Žæ˜¾ä¸ç›¸å…³å†è€ƒè™‘Negative Phrase"
        negative_terms["why"] = negative_terms.apply(
            lambda r: f"è¯¥è¯ç‚¹å‡»{int(r['clicks'])}æ¬¡ã€èŠ±è´¹{r['spend']:.2f}ï¼Œä½†æ²¡æœ‰è®¢å•ï¼Œå·²è¾¾åˆ°åˆ¤æ–­é—¨æ§›ã€‚",
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
        recommendation = "åŒè¯æ ¹/åŒåœºæ™¯ï¼šå»ºè®®æ”¾è¿›åŒä¸€ä¸ªå¹¿å‘Šç»„ï¼Œç»Ÿä¸€æµ‹è¯•å’ŒæŽ§ä»·"
        if best_terms:
            recommendation = "åŒè¯æ ¹è¡¨çŽ°ä¸é”™ï¼šå»ºè®®å»ºç«‹ä¸€ä¸ªç‹¬ç«‹ç²¾å‡†/è¯ç»„å¹¿å‘Šç»„é›†ä¸­æ”¾é‡"
        if bad_terms and not best_terms:
            recommendation = "åŒè¯æ ¹æ•´ä½“è½¬åŒ–å·®ï¼šå»ºè®®é›†ä¸­é™ä»·æµ‹è¯•ï¼Œæ˜Žæ˜¾ä¸ç›¸å…³è¯å†å¦å®š"
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
            "why": f"è¿™äº›è¯éƒ½å›´ç»•â€œ{root}â€è¿™ä¸ªè¯æ ¹ï¼Œåˆè®¡ç‚¹å‡»{int(total_clicks)}æ¬¡ã€è®¢å•{int(total_orders)}å•ã€ACOS {acos:.1%}ã€‚",
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
        f"æœç´¢è¯â€œ{row.get('search_term', '')}â€ï¼šæ›å…‰{int(row.get('impressions', 0) or 0)}ï¼Œ"
        f"ç‚¹å‡»{int(row.get('clicks', 0) or 0)}ï¼ŒèŠ±è´¹{row.get('spend', 0):.2f}ï¼Œ"
        f"è®¢å•{int(row.get('orders', 0) or 0)}ï¼Œé”€å”®é¢{row.get('sales', 0):.2f}ï¼Œ"
        f"ACOS {row.get('acos', 0):.1%}ï¼Œç›®æ ‡ACOS {target:.1%}ã€‚"
    )




