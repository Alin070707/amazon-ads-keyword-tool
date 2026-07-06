from __future__ import annotations

import pandas as pd
import streamlit as st
import yaml

from src.bid_optimizer import add_bid_recommendations
from src.confidence_engine import add_confidence
from src.data_cleaner import clean_frame, combine_cleaned
from src.field_mapper import FieldMapper
from src.file_loader import read_uploaded_file
from src.metric_calculator import add_core_metrics
from src.profit_analyzer import add_profit_metrics
from src.report_detector import ReportDetector
from src.search_term_analyzer import product_group_keyword_tables, search_term_insight_tables
from src.utils import ROOT, load_yaml, logger


st.set_page_config(page_title="搜索词直观分析窗口", layout="wide")

DEFAULT_SETTINGS_PATH = ROOT / "config" / "default_settings.yaml"


def load_settings() -> dict:
    return load_yaml(DEFAULT_SETTINGS_PATH)


def analyze_files(files, settings: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    mapper = FieldMapper()
    detector = ReportDetector()
    cleaned_frames = []
    metadata_rows = []

    for file in files:
        for table in read_uploaded_file(file, filename=file.name):
            if table.frame.empty:
                metadata_rows.append({"文件": table.source_file, "状态": "读取失败", "说明": "; ".join(table.errors)})
                continue

            mapped, _, unmapped = mapper.apply_mapping(table.frame)
            detected = detector.detect(mapped)
            cleaned, quality = clean_frame(mapped)
            cleaned["source_file"] = table.source_file
            cleaned["source_sheet"] = table.sheet_name
            cleaned["report_type"] = detected["report_name"]
            cleaned["ad_type"] = cleaned.get("ad_type", detected["ad_type"])
            cleaned_frames.append(cleaned)
            metadata_rows.append({
                "文件": table.source_file,
                "工作表": table.sheet_name,
                "识别报表": detected["report_name"],
                "读取行数": len(table.frame),
                "清洗后行数": len(cleaned),
                "未识别字段": ", ".join(unmapped),
                "缺失关键字段": ", ".join(quality.get("missing_critical_fields", [])),
            })

    combined = combine_cleaned(cleaned_frames)
    if not combined.empty:
        combined = add_profit_metrics(combined, settings)
        combined = add_core_metrics(combined, settings)
        combined = add_confidence(combined, settings)
        combined = add_bid_recommendations(combined, settings)
    return combined, pd.DataFrame(metadata_rows)


def parse_yaml_text(text: str, fallback):
    try:
        value = yaml.safe_load(text) if text.strip() else fallback
        return value if value is not None else fallback
    except Exception as exc:
        st.sidebar.error(f"YAML格式错误：{exc}")
        return fallback


def unique_values(df: pd.DataFrame, column: str) -> list[str]:
    if column not in df.columns or df.empty:
        return []
    values = df[column].dropna().astype(str)
    return sorted([v for v in values.unique().tolist() if v and v != "nan"])


def apply_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for column, values in filters.items():
        if column in out.columns and values:
            out = out[out[column].astype(str).isin(values)]
    keyword = filters.get("__keyword", "").strip().lower()
    if keyword and "search_term" in out.columns:
        out = out[out["search_term"].astype(str).str.lower().str.contains(keyword, na=False)]
    return out


def render_filters(source: pd.DataFrame, key_prefix: str) -> dict:
    with st.expander("筛选条件", expanded=True):
        c1, c2, c3 = st.columns(3)
        filters = {
            "product_group": c1.multiselect("产品组", unique_values(source, "product_group"), key=f"{key_prefix}_product_group"),
            "asin_list": c2.multiselect("ASIN", unique_values(source, "asin_list"), key=f"{key_prefix}_asin"),
            "campaign": c3.multiselect("Campaign", unique_values(source, "campaign"), key=f"{key_prefix}_campaign"),
        }
        c4, c5, c6 = st.columns(3)
        filters["ad_group"] = c4.multiselect("广告组", unique_values(source, "ad_group"), key=f"{key_prefix}_ad_group")
        filters["word_root"] = c5.multiselect("词根", unique_values(source, "word_root"), key=f"{key_prefix}_word_root")
        filters["keyword_status"] = c6.multiselect("关键词状态", unique_values(source, "keyword_status"), key=f"{key_prefix}_status")
        filters["__keyword"] = st.text_input("搜索词包含", key=f"{key_prefix}_keyword")
    return filters


def render_table(title: str, caption: str, df: pd.DataFrame) -> None:
    st.subheader(title)
    st.caption(caption)
    if df.empty:
        st.info("当前没有符合条件的数据。")
        return
    st.dataframe(df, use_container_width=True, height=520)
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(f"下载：{title}", data=csv, file_name=f"{title}.csv", mime="text/csv")


def main() -> None:
    st.title("搜索词直观分析窗口")
    st.caption("上传报表后，可筛选产品组、ASIN、Campaign、广告组、词根、关键词状态，并查看好词、否定词、同场景广告组和同词根长尾词。")

    settings = load_settings()
    with st.sidebar:
        st.header("判断参数")
        settings["target_acos"] = st.number_input("目标ACOS", 0.0, 2.0, float(settings.get("target_acos", 0.30)), 0.01, format="%.2f")
        settings["min_clicks"] = st.number_input("最低点击判断门槛", 1, 1000, int(settings.get("min_clicks", 8)), 1)
        settings["min_orders"] = st.number_input("最低订单判断门槛", 1, 1000, int(settings.get("min_orders", 2)), 1)
        st.divider()
        st.header("产品组 / ASIN分组")
        default_groups = settings.get("product_asin_groups", {}) or {}
        groups_text = st.text_area(
            "产品组配置（YAML）",
            yaml.safe_dump(default_groups, allow_unicode=True, sort_keys=False),
            height=160,
            help="例：\n猫磨爪盒:\n  - B0AAAA1111\n  - B0BBBB2222\n鸟毛:\n  - B0CCCC3333",
        )
        settings["product_asin_groups"] = parse_yaml_text(groups_text, default_groups)
        st.caption("没有ASIN列时，系统会尝试从Campaign/广告组/投放名里提取 B0 开头的ASIN。")

    files = st.file_uploader(
        "上传 Search Term Report 或包含 Customer Search Term 字段的广告报表",
        type=["csv", "xlsx", "xls", "tsv"],
        accept_multiple_files=True,
    )

    if not files:
        st.info("请先上传搜索词报表。上传后会出现筛选条件和分析结果。")
        return

    try:
        with st.spinner("正在分析搜索词..."):
            cleaned, metadata = analyze_files(files, settings)
    except Exception as exc:
        logger.exception("搜索词直观分析失败")
        st.error(f"分析失败：{exc}")
        return

    with st.expander("文件识别与字段情况", expanded=False):
        st.dataframe(metadata, use_container_width=True)

    if cleaned.empty:
        st.error("没有读取到可分析的数据。")
        return
    if "search_term" not in cleaned.columns:
        st.error("当前文件没有识别到搜索词字段。请确认报表里有 Search Term / Customer Search Term / 客户搜索词。")
        return

    all_tables = product_group_keyword_tables(cleaned, settings)
    filter_source = all_tables["product_group_terms"]
    filters = render_filters(filter_source, "standalone")

    product_terms = apply_filters(all_tables["product_group_terms"], filters)
    product_roots = apply_filters(all_tables["product_group_roots"], filters)
    asin_filters = dict(filters)
    asin_filters["asin"] = asin_filters.pop("asin_list", [])
    asin_summary = apply_filters(all_tables["asin_summary"], asin_filters)

    filtered_terms_for_classic = product_terms.rename(columns={"product_group": "_product_group"})
    classic_tables = search_term_insight_tables(filtered_terms_for_classic, settings)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("筛选后搜索词", len(product_terms))
    c2.metric("表现好词", len(classic_tables["good_terms"]))
    c3.metric("建议否定词", len(classic_tables["negative_terms"]))
    c4.metric("产品组词根", len(product_roots))

    main_tab, product_tab = st.tabs(["搜索词直观分析", "按产品组/ASIN看关键词"])

    with main_tab:
        tab1, tab2, tab3, tab4 = st.tabs(["表现好，可以扩量", "需要否定", "同场景可放同广告组", "同词根长尾词"])
        with tab1:
            render_table("表现好，可以扩量", "有订单，且ACOS不高于目标。适合新增Exact精准词、单独广告组，或提高预算/竞价。", classic_tables["good_terms"])
        with tab2:
            render_table("需要否定", "点击达到判断门槛、花费了钱但没有订单。请先人工确认相关性，再决定Negative Exact或Negative Phrase。", classic_tables["negative_terms"])
        with tab3:
            render_table("同场景可放同广告组", "这些词围绕同一个需求、场景或词根，可以考虑放在同一个广告组里统一测试和控价。", classic_tables["ad_group_clusters"])
        with tab4:
            render_table("同词根长尾词", "这些长尾词来自同一个词根，适合做词根广告组、词组匹配、广泛扩词或长尾词收割。", classic_tables["root_longtails"])

    with product_tab:
        ptab1, ptab2, ptab3 = st.tabs(["产品组-搜索词明细", "产品组-同词根汇总", "ASIN汇总"])
        with ptab1:
            render_table("产品组-搜索词明细", "按产品组把多个ASIN的同一搜索词合并，看这个词到底适合哪一组产品。", product_terms)
        with ptab2:
            render_table("产品组-同词根汇总", "同一产品组下，把相同词根的长尾词汇总，方便建立同一个广告组。", product_roots)
        with ptab3:
            render_table("ASIN汇总", "查看每个ASIN在上传报表里的搜索词、花费、订单和销售额表现。", asin_summary)


if __name__ == "__main__":
    main()
