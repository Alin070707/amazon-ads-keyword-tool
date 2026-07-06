from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .bid_optimizer import recommended_bid
from .confidence_engine import confidence_for_row
from .field_mapper import classify_term
from .utils import ROOT, contains_any, load_yaml


OUTPUT_COLUMNS = [
    "priority", "severity", "rule", "action_type", "ad_type", "campaign", "ad_group",
    "targeting", "search_term", "match_type", "current_bid", "recommended_bid",
    "bid_change", "bid_change_pct", "current_budget", "recommended_budget",
    "current_placement_adjustment", "recommended_placement_adjustment", "current_data",
    "diagnosis", "suggested_action", "reason", "confidence", "executable",
    "expected_impact", "risk_note", "source_file", "report_type",
]


class RuleEngine:
    def __init__(self, rules_path: str | Path | None = None):
        self.rules = load_yaml(rules_path or ROOT / "config" / "optimization_rules.yaml").get("rules", {})

    def evaluate(self, frame: pd.DataFrame, settings: dict[str, Any]) -> pd.DataFrame:
        suggestions: list[dict[str, Any]] = []
        if frame.empty:
            return pd.DataFrame(columns=OUTPUT_COLUMNS)
        avg_ctr = frame["ctr"].mean() if "ctr" in frame else 0
        avg_cvr = frame["cvr"].mean() if "cvr" in frame else 0
        for _, row in frame.iterrows():
            suggestions.extend(self._evaluate_row(row, settings, avg_ctr, avg_cvr))
        suggestions.extend(self._duplicates(frame, settings))
        out = pd.DataFrame(suggestions)
        if out.empty:
            return pd.DataFrame(columns=OUTPUT_COLUMNS)
        for col in OUTPUT_COLUMNS:
            if col not in out.columns:
                out[col] = ""
        return out[OUTPUT_COLUMNS]

    def _base(self, row: pd.Series, settings: dict, rule_key: str, action: str | None = None) -> dict[str, Any]:
        rule = self.rules.get(rule_key, {})
        bid = recommended_bid(row, settings)
        current_data = (
            f"曝光{int(row.get('impressions', 0) or 0)}，点击{int(row.get('clicks', 0) or 0)}，"
            f"花费{row.get('spend', 0):.2f}，订单{int(row.get('orders', 0) or 0)}，"
            f"销售额{row.get('sales', 0):.2f}，ACOS {row.get('acos', 0):.1%}，CVR {row.get('cvr', 0):.1%}。"
        )
        return {
            "priority": rule.get("priority", "P2"),
            "severity": rule.get("severity", "中"),
            "rule": rule_key,
            "action_type": action or rule.get("action", "Observe"),
            "ad_type": row.get("ad_type", ""),
            "campaign": row.get("campaign", ""),
            "ad_group": row.get("ad_group", ""),
            "targeting": row.get("targeting", ""),
            "search_term": row.get("search_term", ""),
            "match_type": row.get("match_type", ""),
            "current_bid": bid["current_bid"],
            "recommended_bid": bid["recommended_bid"],
            "bid_change": bid["bid_change"],
            "bid_change_pct": bid["bid_change_pct"],
            "current_budget": row.get("budget", 0),
            "recommended_budget": "",
            "current_placement_adjustment": "",
            "recommended_placement_adjustment": "",
            "current_data": current_data,
            "confidence": confidence_for_row(row, settings),
            "executable": "否，第一版仅供人工审核",
            "source_file": row.get("source_file", ""),
            "report_type": row.get("report_type", ""),
        }

    def _evaluate_row(self, row: pd.Series, settings: dict, avg_ctr: float, avg_cvr: float) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        target = float(settings.get("target_acos", 0.30))
        min_clicks = int(settings.get("min_clicks", 8))
        min_orders = int(settings.get("min_orders", 2))
        min_impressions = int(settings.get("min_impressions", 500))
        clicks = float(row.get("clicks", 0) or 0)
        orders = float(row.get("orders", 0) or 0)
        spend = float(row.get("spend", 0) or 0)
        sales = float(row.get("sales", 0) or 0)
        acos = float(row.get("acos", 0) or 0)
        ctr = float(row.get("ctr", 0) or 0)
        cvr = float(row.get("cvr", 0) or 0)
        impressions = float(row.get("impressions", 0) or 0)
        search_term = str(row.get("search_term", "") or "")
        target_text = str(row.get("targeting", "") or search_term)
        protected = contains_any(search_term + " " + target_text, settings.get("brand_terms", []) + settings.get("core_terms", []))

        if self.rules.get("high_spend_no_orders", {}).get("enabled", True) and clicks >= min_clicks and orders == 0 and spend > 0:
            item = self._base(row, settings, "high_spend_no_orders")
            if protected or item["confidence"] == "低":
                item["priority"] = "P2"
                item["action_type"] = "Observe"
                item["suggested_action"] = "继续观察，不建议立即暂停或否定。"
                item["risk_note"] = "该词可能属于品牌词、核心词或数据可信度不足，直接否定可能损失后续转化。"
            elif search_term:
                item["action_type"] = "Add Negative Exact"
                item["suggested_action"] = f"建议先降低竞价至{item['recommended_bid']:.2f}；若未来7天继续无订单，再将“{search_term}”加入精准否定。"
                item["risk_note"] = "请人工确认搜索词与产品相关性，避免误否定有辅助转化价值的词。"
            else:
                item["suggested_action"] = f"建议将竞价从{item['current_bid']:.2f}下调至{item['recommended_bid']:.2f}，暂不直接暂停。"
                item["risk_note"] = "无搜索词字段，无法判断是否应该否定，只建议调整投放竞价。"
            item["diagnosis"] = "高花费无订单"
            item["reason"] = f"该对象获得{int(clicks)}次点击、花费{spend:.2f}，但没有产生订单，已达到判断门槛。"
            item["expected_impact"] = f"预计可减少低效测试花费，参考当前花费约{spend:.2f}。"
            out.append(item)

        if orders > 0 and acos > target * self.rules.get("acos_far_above_target", {}).get("multiplier", 1.3):
            item = self._base(row, settings, "acos_far_above_target")
            item["diagnosis"] = "ACOS明显高于目标"
            item["suggested_action"] = f"建议将竞价从{item['current_bid']:.2f}下调至{item['recommended_bid']:.2f}，单次最大降幅已受配置限制。"
            item["reason"] = f"实际ACOS为{acos:.1%}，高于目标ACOS {target:.1%} 的1.3倍。"
            item["expected_impact"] = "降低单次点击成本，使该投放更接近目标ACOS。"
            item["risk_note"] = "仍有订单，不建议直接暂停；先降价并观察转化稳定性。"
            out.append(item)
        elif orders > 0 and target < acos <= target * self.rules.get("acos_slightly_above_target", {}).get("multiplier", 1.3):
            item = self._base(row, settings, "acos_slightly_above_target")
            item["diagnosis"] = "ACOS略高于目标"
            item["suggested_action"] = f"建议小幅降价，竞价调整至{item['recommended_bid']:.2f}，继续观察。"
            item["reason"] = f"实际ACOS为{acos:.1%}，略高于目标ACOS {target:.1%}。"
            item["expected_impact"] = "温和降低花费，同时保留已有转化流量。"
            item["risk_note"] = "不建议立即暂停，以免损失可优化订单。"
            out.append(item)

        if orders >= min_orders and 0 < acos < target * self.rules.get("low_acos_stable_orders", {}).get("multiplier", 0.7):
            item = self._base(row, settings, "low_acos_stable_orders", "Increase Bid")
            item["diagnosis"] = "低ACOS且订单稳定"
            item["suggested_action"] = f"建议提高竞价至{item['recommended_bid']:.2f}，并考虑增加预算或建立独立精准Campaign。"
            item["reason"] = f"订单{int(orders)}单，ACOS {acos:.1%}明显低于目标，具备扩量空间。"
            item["expected_impact"] = "获取更多高效率流量，增加有效广告销售额。"
            item["risk_note"] = "上调后需观察CPC和转化率变化，避免扩量后ACOS快速上升。"
            out.append(item)

        if impressions >= min_impressions and avg_ctr > 0 and ctr < avg_ctr * self.rules.get("low_ctr", {}).get("relative_to_average", 0.55):
            item = self._base(row, settings, "low_ctr", "Check Listing")
            item["diagnosis"] = "点击率明显偏低"
            item["suggested_action"] = "建议检查搜索词相关性、主图、标题、价格、优惠券和广告位；若相关性低，再降低竞价或否定。"
            item["reason"] = f"曝光{int(impressions)}次但CTR仅{ctr:.2%}，明显低于同类平均{avg_ctr:.2%}。"
            item["expected_impact"] = "提升点击质量，减少无效曝光。"
            item["risk_note"] = "CTR问题可能来自广告、Listing或市场竞争，不能只用ACOS判断。"
            out.append(item)

        if avg_ctr > 0 and avg_cvr > 0 and ctr > avg_ctr * 1.2 and cvr < avg_cvr * 0.55 and clicks >= min_clicks:
            item = self._base(row, settings, "high_ctr_low_cvr", "Check Listing")
            item["diagnosis"] = "点击率高但转化率低"
            item["suggested_action"] = "建议检查Listing说服力、价格、评价、优惠和搜索意图；高度相关词不要直接否定。"
            item["reason"] = f"CTR {ctr:.2%}高于平均，但CVR {cvr:.2%}明显偏低。"
            item["expected_impact"] = "提升点击后的成交效率，降低浪费点击。"
            item["risk_note"] = "如果搜索词高度相关，优先优化页面和价格，而不是直接否定。"
            out.append(item)

        if cvr > avg_cvr and 0 < acos <= target and impressions < min_impressions and orders > 0:
            item = self._base(row, settings, "low_impression_good_conversion", "Increase Bid")
            item["diagnosis"] = "曝光低但转化好"
            item["suggested_action"] = f"建议提高竞价至{item['recommended_bid']:.2f}，扩展词组/广泛匹配，并考虑单独建精准Campaign。"
            item["reason"] = f"CVR {cvr:.2%}高于平均且ACOS低于目标，但曝光只有{int(impressions)}。"
            item["expected_impact"] = "扩大优质流量覆盖，提高广告销售额。"
            item["risk_note"] = "曝光少导致样本有限，建议渐进式扩量。"
            out.append(item)

        if search_term and orders >= min_orders and acos <= target:
            item = self._base(row, settings, "search_term_harvest", "Harvest Search Term")
            item["diagnosis"] = "搜索词收割机会"
            item["suggested_action"] = f"建议将“{search_term}”新增为Exact关键词，单独设置竞价；只有新精准投放创建后，再考虑在原自动/广泛广告中精准否定。"
            item["reason"] = f"该搜索词订单{int(orders)}单，ACOS {acos:.1%}不高于目标。"
            item["expected_impact"] = "提升高转化搜索词的流量控制和预算效率。"
            item["risk_note"] = "不要在新投放建立前先否定原流量入口。"
            out.append(item)

        if search_term and clicks >= min_clicks and orders == 0 and not protected:
            term_type = classify_term(search_term, settings.get("brand_terms", []), settings.get("competitor_terms", []), settings.get("core_terms", []))
            item = self._base(row, settings, "search_term_negative", "Add Negative Exact")
            item["diagnosis"] = "搜索词否定候选"
            item["suggested_action"] = f"建议人工确认相关性后，对“{search_term}”添加Negative Exact；若明显不相关且包含泛词根，可考虑Negative Phrase。"
            item["reason"] = f"该{term_type}获得{int(clicks)}次点击、花费{spend:.2f}但无订单。"
            item["expected_impact"] = f"减少该搜索词继续消耗，当前可参考浪费金额{spend:.2f}。"
            item["risk_note"] = "品牌核心词、新品测试词和辅助转化词不应自动否定。"
            out.append(item)

        if float(row.get("break_even_cpc", 0) or 0) > 0 and float(row.get("cpc", 0) or 0) > float(row.get("break_even_cpc", 0)) * 1.2:
            item = self._base(row, settings, "abnormal_cpc", "Decrease Bid")
            item["diagnosis"] = "CPC接近或高于可承受水平"
            item["suggested_action"] = f"建议将竞价下调至{item['recommended_bid']:.2f}，使实际CPC更接近盈亏平衡CPC。"
            item["reason"] = f"当前CPC {row.get('cpc', 0):.2f} 高于盈亏平衡CPC {row.get('break_even_cpc', 0):.2f}。"
            item["expected_impact"] = "降低点击成本，减少亏损订单风险。"
            item["risk_note"] = "需结合转化率和排名变化观察，不要一次过度降价。"
            out.append(item)

        return out

    def _duplicates(self, frame: pd.DataFrame, settings: dict) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        key = "targeting" if "targeting" in frame.columns else "search_term"
        if key not in frame.columns:
            return out
        group_cols = [key]
        dup = frame.groupby(group_cols).filter(lambda x: len(x[["campaign", "ad_group"]].drop_duplicates()) > 1 if {"campaign", "ad_group"}.issubset(x.columns) else len(x) > 1)
        for value, grp in dup.groupby(key):
            best = grp.sort_values(["orders", "acos"], ascending=[False, True]).iloc[0]
            item = self._base(best, settings, "duplicate_targeting", "Observe")
            item["diagnosis"] = "关键词重复和内部竞争"
            item["suggested_action"] = f"同一投放“{value}”出现在{grp['campaign'].nunique() if 'campaign' in grp else len(grp)}个Campaign/广告组中，建议保留转化更好的一组，其他组降低竞价或做流量隔离。"
            item["reason"] = "重复投放可能造成内部竞争和数据分散。"
            item["expected_impact"] = "集中预算和数据，减少自我竞争。"
            item["risk_note"] = "请先确认不同Campaign是否承担品牌词、泛词、竞品词等不同目的。"
            out.append(item)
        return out


def executive_summary(frame: pd.DataFrame, suggestions: pd.DataFrame, settings: dict) -> str:
    if frame.empty:
        return "本次没有可分析的数据。"
    campaigns = frame["campaign"].nunique() if "campaign" in frame else 0
    targets = frame["targeting"].nunique() if "targeting" in frame else 0
    terms = frame["search_term"].nunique() if "search_term" in frame else 0
    spend = frame["spend"].sum()
    sales = frame["sales"].sum()
    orders = frame["orders"].sum()
    acos = 0 if sales == 0 else spend / sales
    target = settings.get("target_acos", 0.30)
    p0 = suggestions[suggestions["priority"] == "P0"] if not suggestions.empty else pd.DataFrame()
    waste = suggestions[suggestions["rule"].isin(["high_spend_no_orders", "search_term_negative"])]["current_data"].count() if not suggestions.empty else 0
    harvest = suggestions[suggestions["action_type"] == "Harvest Search Term"] if not suggestions.empty else pd.DataFrame()
    wasted_spend = frame[(frame["clicks"] >= settings.get("min_clicks", 8)) & (frame["orders"] == 0)]["spend"].sum()
    harvest_sales = harvest.shape[0]
    lines = [
        f"本次共分析{campaigns}个广告活动、{targets}个投放和{terms}个搜索词。",
        f"总广告花费为{spend:.2f}，广告销售额为{sales:.2f}，订单{orders:.0f}单，整体ACOS为{acos:.1%}，目标ACOS为{target:.1%}。",
        "主要问题：",
        f"1. {waste}个对象触发高花费无订单或否定候选，相关花费约{wasted_spend:.2f}。",
        f"2. {len(suggestions[suggestions['rule'].str.contains('acos', na=False)]) if not suggestions.empty else 0}条建议与ACOS偏离目标有关。",
        f"3. {len(p0)}条P0建议需要优先审核。",
        "建议优先执行：",
        f"1. 先审核P0建议，预计可减少无效花费约{wasted_spend:.2f}。",
        f"2. 将{harvest_sales}个高转化搜索词转为精准投放。",
        "3. 将预算从高ACOS或无订单Campaign转移到低ACOS且订单稳定的Campaign。",
        "数据限制和风险提醒：本系统只基于上传报表生成建议，不会登录或修改亚马逊后台；字段缺失、归因周期差异和样本过少会降低建议可信度。",
    ]
    return "\n".join(lines)
