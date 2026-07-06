from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st
import yaml

from bulk_operations.bulk_exporter import export_bulk_draft
from src.bid_optimizer import add_bid_recommendations
from src.budget_optimizer import campaign_budget_recommendations
from src.confidence_engine import add_confidence
from src.data_cleaner import clean_frame, combine_cleaned
from src.database import load_runs, save_run
from src.field_mapper import FieldMapper
from src.file_loader import read_uploaded_file
from src.metric_calculator import add_core_metrics, aggregate_metrics
from src.placement_optimizer import placement_analysis
from src.profit_analyzer import add_profit_metrics
from src.report_detector import ReportDetector
from src.report_generator import generate_excel_report
from src.rule_engine import RuleEngine, executive_summary
from src.search_term_analyzer import categorize_search_terms, product_group_keyword_tables, search_term_insight_tables
from src.utils import ROOT, load_yaml, logger


st.set_page_config(page_title="亚马逊广告自动诊断与优化建议系统", layout="wide")

DEFAULT_SETTINGS_PATH = ROOT / "config" / "default_settings.yaml"


def load_settings() -> dict:
    settings = load_yaml(DEFAULT_SETTINGS_PATH)
    if "settings" not in st.session_state:
        st.session_state.settings = settings
        st.session_state.used_defaults = True
    return st.session_state.settings


def save_settings(settings: dict) -> None:
    DEFAULT_SETTINGS_PATH.write_text(yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8")
    st.session_state.settings = settings
    st.session_state.used_defaults = False


def analyze_uploads(files, manual_mapping: dict | None = None):
    mapper = FieldMapper()
    detector = ReportDetector()
    cleaned_frames = []
    metadata_rows = []
    quality_rows = []
    mappings = []
    for file in files:
        for table in read_uploaded_file(file, filename=file.name):
            if table.frame.empty:
                quality_rows.append({"source_file": table.source_file, "error": "; ".join(table.errors)})
                continue
            mapped, mapping, unmapped = mapper.apply_mapping(table.frame, manual_mapping)
            detected = detector.detect(mapped)
            cleaned, quality = clean_frame(mapped)
            cleaned["source_file"] = table.source_file
            cleaned["source_sheet"] = table.sheet_name
            cleaned["report_type"] = detected["report_name"]
            cleaned["ad_type"] = cleaned.get("ad_type", detected["ad_type"])
            start, end = detector.date_range(cleaned)
            cleaned_frames.append(cleaned)
            mappings.append({"source_file": table.source_file, "sheet": table.sheet_name, "mapping": mapping, "unmapped": unmapped})
            metadata_rows.append({
                "文件": table.source_file,
                "工作表": table.sheet_name,
                "识别报表": detected["report_name"],
                "广告类型": detected["ad_type"],
                "日期开始": start,
                "日期结束": end,
                "读取行数": len(table.frame),
                "清洗后行数": len(cleaned),
                "未识别字段": ", ".join(unmapped),
            })
            quality["source_file"] = table.source_file
            quality["report_type"] = detected["report_name"]
            quality_rows.append(quality)
    combined = combine_cleaned(cleaned_frames)
    settings = load_settings()
    if not combined.empty:
        combined = add_profit_metrics(combined, settings)
        combined = add_core_metrics(combined, settings)
        combined = add_confidence(combined, settings)
        combined = add_bid_recommendations(combined, settings)
    suggestions = RuleEngine().evaluate(combined, settings) if not combined.empty else pd.DataFrame()
    return combined, suggestions, pd.DataFrame(metadata_rows), pd.DataFrame(quality_rows), mappings


def ensure_state():
    st.session_state.setdefault("cleaned", pd.DataFrame())
    st.session_state.setdefault("suggestions", pd.DataFrame())
    st.session_state.setdefault("metadata", pd.DataFrame())
    st.session_state.setdefault("quality", pd.DataFrame())
    st.session_state.setdefault("mappings", [])


def render_settings_page():
    settings = load_settings().copy()
    st.subheader("业务参数")
    col1, col2, col3 = st.columns(3)
    with col1:
        settings["target_acos"] = st.number_input("目标ACOS", 0.0, 2.0, float(settings.get("target_acos", 0.30)), 0.01, format="%.2f")
        settings["break_even_acos"] = st.number_input("盈亏平衡ACOS", 0.0, 2.0, float(settings.get("break_even_acos", 0.35)), 0.01, format="%.2f")
        settings["product_price"] = st.number_input("产品售价", 0.0, 100000.0, float(settings.get("product_price", 25.0)), 0.5)
        settings["product_cost"] = st.number_input("产品采购成本", 0.0, 100000.0, float(settings.get("product_cost", 8.0)), 0.5)
        settings["amazon_commission_rate"] = st.number_input("亚马逊佣金比例", 0.0, 1.0, float(settings.get("amazon_commission_rate", 0.15)), 0.01)
    with col2:
        settings["fba_fee"] = st.number_input("FBA配送费", 0.0, 10000.0, float(settings.get("fba_fee", 4.0)), 0.1)
        settings["inbound_shipping"] = st.number_input("头程费用", 0.0, 10000.0, float(settings.get("inbound_shipping", 1.0)), 0.1)
        settings["storage_fee"] = st.number_input("仓储费用", 0.0, 10000.0, float(settings.get("storage_fee", 0.3)), 0.1)
        settings["refund_rate"] = st.number_input("退款率", 0.0, 1.0, float(settings.get("refund_rate", 0.03)), 0.01)
        settings["vat_rate"] = st.number_input("VAT或销售税", 0.0, 1.0, float(settings.get("vat_rate", 0.0)), 0.01)
    with col3:
        settings["coupon_cost"] = st.number_input("优惠券成本", 0.0, 10000.0, float(settings.get("coupon_cost", 0.0)), 0.1)
        settings["other_unit_cost"] = st.number_input("其他单件成本", 0.0, 10000.0, float(settings.get("other_unit_cost", 0.0)), 0.1)
        settings["attribution_window_days"] = st.number_input("广告归因周期", 1, 30, int(settings.get("attribution_window_days", 7)), 1)
        settings["min_clicks"] = st.number_input("最低点击判断门槛", 1, 1000, int(settings.get("min_clicks", 8)), 1)
        settings["min_impressions"] = st.number_input("最低曝光判断门槛", 1, 1000000, int(settings.get("min_impressions", 500)), 10)
    col4, col5, col6 = st.columns(3)
    with col4:
        settings["min_orders"] = st.number_input("最低订单判断门槛", 1, 1000, int(settings.get("min_orders", 2)), 1)
        settings["bid_down_pct"] = st.number_input("默认竞价下调幅度", 0.0, 1.0, float(settings.get("bid_down_pct", 0.15)), 0.01)
        settings["bid_up_pct"] = st.number_input("默认竞价上调幅度", 0.0, 1.0, float(settings.get("bid_up_pct", 0.10)), 0.01)
    with col5:
        settings["max_bid_down_pct"] = st.number_input("单次最大降幅", 0.0, 1.0, float(settings.get("max_bid_down_pct", 0.30)), 0.01)
        settings["max_bid_up_pct"] = st.number_input("单次最大涨幅", 0.0, 1.0, float(settings.get("max_bid_up_pct", 0.20)), 0.01)
        settings["min_bid"] = st.number_input("最低竞价", 0.0, 1000.0, float(settings.get("min_bid", 0.02)), 0.01)
    with col6:
        settings["max_bid"] = st.number_input("最高竞价", 0.0, 1000.0, float(settings.get("max_bid", 8.0)), 0.1)
    settings["brand_terms"] = [x.strip() for x in st.text_area("品牌词名单（一行一个）", "\n".join(settings.get("brand_terms", []))).splitlines() if x.strip()]
    settings["competitor_terms"] = [x.strip() for x in st.text_area("竞品词名单（一行一个）", "\n".join(settings.get("competitor_terms", []))).splitlines() if x.strip()]
    settings["core_terms"] = [x.strip() for x in st.text_area("核心关键词名单（一行一个）", "\n".join(settings.get("core_terms", []))).splitlines() if x.strip()]
    settings["negative_terms"] = [x.strip() for x in st.text_area("否定词名单（一行一个）", "\n".join(settings.get("negative_terms", []))).splitlines() if x.strip()]
    st.text_area("产品ASIN和SKU对应关系（YAML格式）", yaml.safe_dump(settings.get("asin_sku_map", {}), allow_unicode=True), key="asin_sku_map_text")
    if st.button("保存参数配置", type="primary"):
        try:
            settings["asin_sku_map"] = yaml.safe_load(st.session_state.asin_sku_map_text) or {}
            save_settings(settings)
            st.success("参数已保存，后续分析会使用新配置。")
        except Exception as exc:
            st.error(f"ASIN/SKU映射格式错误：{exc}")


def render_upload_page():
    st.subheader("数据上传")
    files = st.file_uploader("上传亚马逊广告报表（支持CSV/XLSX/XLS，多文件）", type=["csv", "xlsx", "xls", "tsv"], accept_multiple_files=True)
    mapper = FieldMapper()
    manual_mapping = {}
    with st.expander("手动字段映射（当系统无法识别字段时使用）"):
        st.caption("格式：左侧填写原始字段名，右侧选择标准字段。未上传文件时可先跳过。")
        for i in range(5):
            c1, c2 = st.columns([2, 2])
            old = c1.text_input(f"原始字段名 {i + 1}", key=f"old_{i}")
            new = c2.selectbox(f"标准字段 {i + 1}", [""] + mapper.canonical_choices(), key=f"new_{i}")
            if old and new:
                manual_mapping[old] = new
    if files and st.button("开始读取并分析", type="primary"):
        with st.spinner("正在读取、清洗、识别和分析数据..."):
            try:
                cleaned, suggestions, metadata, quality, mappings = analyze_uploads(files, manual_mapping)
                st.session_state.cleaned = cleaned
                st.session_state.suggestions = suggestions
                st.session_state.metadata = metadata
                st.session_state.quality = quality
                st.session_state.mappings = mappings
                if not cleaned.empty:
                    save_run({
                        "run_name": "streamlit_upload",
                        "total_spend": cleaned["spend"].sum(),
                        "total_sales": cleaned["sales"].sum(),
                        "total_orders": cleaned["orders"].sum(),
                        "acos": 0 if cleaned["sales"].sum() == 0 else cleaned["spend"].sum() / cleaned["sales"].sum(),
                    })
                st.success("分析完成。")
            except Exception as exc:
                logger.exception("分析失败")
                st.error(f"分析失败：{exc}")
    if not st.session_state.metadata.empty:
        st.write("识别结果")
        st.dataframe(st.session_state.metadata, use_container_width=True)
    if not st.session_state.cleaned.empty:
        st.write("数据预览")
        st.dataframe(st.session_state.cleaned.head(50), use_container_width=True)
    if not st.session_state.quality.empty:
        st.write("数据质量")
        st.dataframe(st.session_state.quality, use_container_width=True)


def render_overview():
    df = st.session_state.cleaned
    st.subheader("账户总览")
    if df.empty:
        st.info("请先上传并分析报表。")
        return
    total_spend = df["spend"].sum()
    total_sales = df["sales"].sum()
    total_orders = df["orders"].sum()
    acos = 0 if total_sales == 0 else total_spend / total_sales
    roas = 0 if total_spend == 0 else total_sales / total_spend
    cols = st.columns(8)
    metrics = [
        ("Spend", total_spend),
        ("Sales", total_sales),
        ("Orders", total_orders),
        ("ACOS", acos),
        ("ROAS", roas),
        ("CTR", df["clicks"].sum() / df["impressions"].sum() if df["impressions"].sum() else 0),
        ("CVR", df["orders"].sum() / df["clicks"].sum() if df["clicks"].sum() else 0),
        ("Profit", df["profit_after_ads"].sum()),
    ]
    for col, (name, value) in zip(cols, metrics):
        col.metric(name, f"{value:.1%}" if name in ["ACOS", "CTR", "CVR"] else f"{value:.2f}")
    if "date" in df.columns and df["date"].notna().any():
        trend = aggregate_metrics(df, ["date"])
        st.plotly_chart(px.line(trend, x="date", y=["spend", "sales"], title="花费 / 销售额趋势"), use_container_width=True)
    st.write("历史分析")
    st.dataframe(load_runs(), use_container_width=True)
    st.text(executive_summary(df, st.session_state.suggestions, load_settings()))


def render_diagnostics():
    sug = st.session_state.suggestions
    st.subheader("问题诊断")
    if sug.empty:
        st.info("暂无建议。请先上传分析，或当前数据未触发规则。")
        return
    cols = st.columns(5)
    priority = cols[0].multiselect("优先级", sorted(sug["priority"].dropna().unique()), default=sorted(sug["priority"].dropna().unique()))
    confidence = cols[1].multiselect("可信度", sorted(sug["confidence"].dropna().unique()), default=sorted(sug["confidence"].dropna().unique()))
    action = cols[2].multiselect("动作类型", sorted(sug["action_type"].dropna().unique()))
    campaign = cols[3].multiselect("Campaign", sorted([x for x in sug["campaign"].dropna().unique() if x]))
    rule = cols[4].multiselect("问题类型", sorted(sug["diagnosis"].dropna().unique()))
    view = sug[sug["priority"].isin(priority) & sug["confidence"].isin(confidence)]
    if action:
        view = view[view["action_type"].isin(action)]
    if campaign:
        view = view[view["campaign"].isin(campaign)]
    if rule:
        view = view[view["diagnosis"].isin(rule)]
    st.dataframe(view, use_container_width=True)


def render_search_terms():
    df = st.session_state.cleaned
    st.subheader("搜索词分析")
    if df.empty or "search_term" not in df.columns:
        st.info("当前数据没有搜索词字段。")
        return
    view = categorize_search_terms(df, load_settings())
    st.dataframe(view, use_container_width=True)


def render_keyword_mining():
    df = st.session_state.cleaned
    st.subheader("搜索词直观分组")
    if df.empty or "search_term" not in df.columns:
        st.info("当前数据没有搜索词字段。请上传 Search Term Report。")
        return
    settings = load_settings()
    product_tables = product_group_keyword_tables(df, settings)
    source = product_tables["product_group_terms"]
    with st.expander("筛选条件", expanded=True):
        c1, c2, c3 = st.columns(3)
        product_groups = c1.multiselect("产品组", _unique_values(source, "product_group"), key="main_product_group_filter")
        asins = c2.multiselect("ASIN", _unique_values(source, "asin_list"), key="main_asin_filter")
        campaigns = c3.multiselect("Campaign", _unique_values(source, "campaign"), key="main_campaign_filter")
        c4, c5, c6 = st.columns(3)
        ad_groups = c4.multiselect("广告组", _unique_values(source, "ad_group"), key="main_ad_group_filter")
        roots = c5.multiselect("词根", _unique_values(source, "word_root"), key="main_word_root_filter")
        statuses = c6.multiselect("关键词状态", _unique_values(source, "keyword_status"), key="main_status_filter")
        keyword = st.text_input("搜索词包含", key="main_keyword_filter")

    filtered_source = _filter_keyword_table(
        source,
        {
            "product_group": product_groups,
            "asin_list": asins,
            "campaign": campaigns,
            "ad_group": ad_groups,
            "word_root": roots,
            "keyword_status": statuses,
            "__keyword": keyword,
        },
    )
    tables = search_term_insight_tables(filtered_source, settings)
    product_terms = filtered_source
    product_roots = _filter_keyword_table(product_tables["product_group_roots"], {"product_group": product_groups, "word_root": roots})
    asin_summary = _filter_keyword_table(product_tables["asin_summary"], {"product_group": product_groups, "asin": asins})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("筛选后搜索词", len(product_terms))
    c2.metric("表现好词", len(tables["good_terms"]))
    c3.metric("建议否定词", len(tables["negative_terms"]))
    c4.metric("产品组词根", len(product_roots))
    main_tab, product_tab = st.tabs(["搜索词直观分析", "按产品组/ASIN看关键词"])
    with main_tab:
        tab1, tab2, tab3, tab4 = st.tabs(["表现好，可以扩量", "需要否定", "同场景同广告组", "同词根长尾词"])
        with tab1:
            st.caption("这些词已经产生订单，且ACOS不高于目标。适合收割为Exact精准词，或单独建广告组放量。")
            st.dataframe(tables["good_terms"], use_container_width=True)
        with tab2:
            st.caption("这些词点击达到门槛但没有订单。系统不会自动执行否定，请先人工确认相关性。")
            st.dataframe(tables["negative_terms"], use_container_width=True)
        with tab3:
            st.caption("这些词围绕同一个词根或使用场景，可以考虑放在同一个广告组里统一测试和控价。")
            st.dataframe(tables["ad_group_clusters"], use_container_width=True)
        with tab4:
            st.caption("这些长尾词来自同一个词根，适合做词根广告组、词组匹配或长尾词扩展。")
            st.dataframe(tables["root_longtails"], use_container_width=True)
    with product_tab:
        p1, p2, p3 = st.tabs(["产品组-搜索词明细", "产品组-同词根汇总", "ASIN汇总"])
        with p1:
            st.dataframe(product_terms, use_container_width=True)
        with p2:
            st.dataframe(product_roots, use_container_width=True)
        with p3:
            st.dataframe(asin_summary, use_container_width=True)


def _unique_values(df: pd.DataFrame, column: str) -> list[str]:
    if df.empty or column not in df.columns:
        return []
    return sorted([v for v in df[column].dropna().astype(str).unique().tolist() if v and v != "nan"])


def _filter_keyword_table(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for column, values in filters.items():
        if column.startswith("__") or not values or column not in out.columns:
            continue
        out = out[out[column].astype(str).isin(values)]
    keyword = str(filters.get("__keyword", "") or "").strip().lower()
    if keyword and "search_term" in out.columns:
        out = out[out["search_term"].astype(str).str.lower().str.contains(keyword, na=False)]
    return out


def render_bid():
    df = st.session_state.cleaned
    st.subheader("竞价优化")
    if df.empty:
        st.info("请先上传并分析报表。")
        return
    cols = [c for c in ["campaign", "ad_group", "targeting", "search_term", "match_type", "current_bid", "cpc", "target_cpc", "break_even_cpc", "recommended_bid", "bid_change_pct", "confidence"] if c in df.columns]
    st.dataframe(df[cols], use_container_width=True)


def render_budget():
    df = st.session_state.cleaned
    st.subheader("预算优化")
    if df.empty:
        st.info("请先上传并分析报表。")
        return
    campaign = aggregate_metrics(df, ["campaign"])
    budget = campaign_budget_recommendations(campaign, load_settings())
    st.dataframe(budget, use_container_width=True)


def render_placement():
    df = st.session_state.cleaned
    st.subheader("广告位分析")
    if df.empty or "placement" not in df.columns:
        st.info("当前数据没有广告位字段。")
        return
    st.dataframe(placement_analysis(df, load_settings()), use_container_width=True)


def render_profit():
    df = st.session_state.cleaned
    st.subheader("利润分析")
    if df.empty:
        st.info("请先上传并分析报表。")
        return
    cols = [c for c in ["campaign", "sku", "asin", "spend", "sales", "orders", "gross_profit_before_ads", "profit_after_ads", "computed_break_even_acos", "acos"] if c in df.columns]
    st.dataframe(df[cols], use_container_width=True)


def render_export():
    st.subheader("导出报告")
    df = st.session_state.cleaned
    sug = st.session_state.suggestions
    if df.empty:
        st.info("请先上传并分析报表。")
        return
    if st.button("生成Excel分析报告", type="primary"):
        path = generate_excel_report(df, sug, load_settings(), st.session_state.quality)
        st.success(f"报告已生成：{path}")
        st.download_button("下载Excel报告", data=path.read_bytes(), file_name=path.name)
    if st.button("生成Bulk File草稿（需人工审核）"):
        out = ROOT / "data" / "exports" / "Bulk_Operations_Draft.xlsx"
        export_bulk_draft(sug, out)
        st.success(f"Bulk草稿已生成：{out}")
        st.download_button("下载Bulk草稿", data=out.read_bytes(), file_name=out.name)


def main():
    ensure_state()
    st.title("亚马逊广告自动诊断与优化建议系统")
    if st.session_state.get("used_defaults", True):
        st.caption("当前使用默认业务参数；如未填写目标ACOS，报告会按默认参数计算。")
    pages = {
        "数据上传": render_upload_page,
        "业务参数": render_settings_page,
        "账户总览": render_overview,
        "问题诊断": render_diagnostics,
        "搜索词分析": render_search_terms,
        "搜索词直观分组": render_keyword_mining,
        "竞价优化": render_bid,
        "预算优化": render_budget,
        "广告位分析": render_placement,
        "利润分析": render_profit,
        "导出报告": render_export,
    }
    page = st.sidebar.radio("页面", list(pages.keys()))
    pages[page]()


if __name__ == "__main__":
    main()
