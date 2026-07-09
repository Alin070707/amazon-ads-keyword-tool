from __future__ import annotations

import html

import pandas as pd
import plotly.express as px
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
from src.strategy_advisor import build_campaign_architecture_plan, build_operator_strategy_summary, build_readable_strategy_brief, build_recommendation_audit
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


def parse_lines(text: str) -> list[str]:
    return [x.strip() for x in text.replace("，", "\n").replace(",", "\n").splitlines() if x.strip()]


def inject_style() -> None:
    st.markdown(
        """
        <style>
        .main .block-container {padding-top: 2rem; max-width: 1380px;}
        .app-hero {
            border: 1px solid #E5E7EB;
            background: #FFFFFF;
            padding: 22px 24px;
            border-radius: 8px;
            margin-bottom: 16px;
        }
        .app-hero h1 {font-size: 2rem; margin: 0 0 6px 0; letter-spacing: 0;}
        .app-hero p {margin: 0; color: #6B7280; font-size: 0.98rem;}
        .section-note {
            border-left: 4px solid #2563EB;
            background: #F8FAFC;
            padding: 12px 14px;
            border-radius: 6px;
            color: #374151;
            margin: 8px 0 16px 0;
        }
        .strategy-card {
            border: 1px solid #DDE3EA;
            border-radius: 8px;
            padding: 16px;
            background: #FFFFFF;
            min-height: 135px;
        }
        .strategy-card h3 {font-size: 1.05rem; margin: 0 0 8px 0;}
        .strategy-card p {color: #4B5563; margin: 0; line-height: 1.55;}
        .small-label {font-size: 0.82rem; color: #6B7280; margin-bottom: 4px;}
        .pill {display:inline-block; padding:3px 8px; border-radius:999px; font-size:0.78rem; border:1px solid #D1D5DB; color:#374151; background:#F9FAFB; margin-right:6px;}
        .danger-pill {border-color:#FCA5A5; background:#FEF2F2; color:#991B1B;}
        .good-pill {border-color:#A7F3D0; background:#ECFDF5; color:#065F46;}
        .warn-pill {border-color:#FDE68A; background:#FFFBEB; color:#92400E;}
        div[data-testid="stMetric"] {border:1px solid #E5E7EB; padding:12px 14px; border-radius:8px; background:#FFFFFF;}
        div[data-testid="stMetricValue"] {font-size:1.35rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def money_text(value: float) -> str:
    return f"${float(value or 0):,.2f}"


def pct_text(value: float) -> str:
    return f"{float(value or 0):.1%}"


def safe_sum(df: pd.DataFrame, column: str) -> float:
    if df.empty or column not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[column], errors="coerce").fillna(0).sum())


def render_hero() -> None:
    st.markdown(
        """
        <div class="app-hero">
          <h1>亚马逊广告搜索词诊断台</h1>
          <p>先看账户健康，再选调整策略，最后按Campaign、广告组、关键词执行。所有建议都基于上传报表数据，不自动修改后台。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_account_snapshot(cleaned: pd.DataFrame, architecture_tables: dict, classic_tables: dict) -> None:
    spend = safe_sum(cleaned, "spend")
    sales = safe_sum(cleaned, "sales")
    orders = safe_sum(cleaned, "orders")
    clicks = safe_sum(cleaned, "clicks")
    impressions = safe_sum(cleaned, "impressions")
    acos = spend / sales if sales else 0.0
    cvr = orders / clicks if clicks else 0.0
    ctr = clicks / impressions if impressions else 0.0

    st.markdown("### 账户健康概览")
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("广告花费", money_text(spend))
    m2.metric("广告销售额", money_text(sales))
    m3.metric("整体ACOS", pct_text(acos))
    m4.metric("订单", f"{int(orders)}")
    m5.metric("转化率", pct_text(cvr))
    m6.metric("点击率", pct_text(ctr))

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("建议新建Campaign", len(architecture_tables.get("campaign_plan", pd.DataFrame())))
    s2.metric("关键词投放动作", len(architecture_tables.get("keyword_plan", pd.DataFrame())))
    s3.metric("可扩量好词", len(classic_tables.get("good_terms", pd.DataFrame())))
    s4.metric("否定候选词", len(classic_tables.get("negative_terms", pd.DataFrame())))


def render_campaign_charts(cleaned: pd.DataFrame) -> None:
    if cleaned.empty or "campaign" not in cleaned.columns:
        return
    metric_cols = [c for c in ["spend", "sales", "orders", "clicks"] if c in cleaned.columns]
    if not metric_cols:
        return
    summary = cleaned.groupby("campaign", dropna=False)[metric_cols].sum().reset_index()
    if summary.empty:
        return
    if "spend" in summary.columns and "sales" in summary.columns:
        summary["acos"] = summary.apply(lambda r: r["spend"] / r["sales"] if r["sales"] else 0, axis=1)
    top = summary.sort_values("spend" if "spend" in summary.columns else metric_cols[0], ascending=False).head(12)
    c1, c2 = st.columns([1, 1])
    with c1:
        fig = px.bar(top, x="campaign", y="spend", title="花费最高的Campaign", labels={"campaign": "Campaign", "spend": "Spend"})
        fig.update_layout(height=330, margin=dict(l=10, r=10, t=45, b=80), xaxis_tickangle=-30)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        if "sales" in top.columns:
            fig = px.bar(top, x="campaign", y="sales", title="销售额最高的Campaign", labels={"campaign": "Campaign", "sales": "Sales"})
            fig.update_layout(height=330, margin=dict(l=10, r=10, t=45, b=80), xaxis_tickangle=-30)
            st.plotly_chart(fig, use_container_width=True)


def render_reliability_panel(recommendation_audit: pd.DataFrame) -> None:
    st.markdown("### 本次建议可靠性")
    if recommendation_audit.empty:
        st.info("当前数据不足，暂时无法生成可靠性总览。")
        return
    audit_records = recommendation_audit.to_dict("records")
    cols = st.columns(4)
    for col, item in zip(cols, audit_records):
        col.metric(item["检查项"], item["结果"])
    with st.expander("查看建议依据和安全边界", expanded=False):
        st.dataframe(recommendation_audit, use_container_width=True, hide_index=True)
        st.markdown("- 建议只来自上传报表里的曝光、点击、花费、订单、销售额、ACOS、CPC、CVR等字段。")
        st.markdown("- 数据量不足时，系统会把证据等级标为低，并提示只观察或小预算测试。")
        st.markdown("- 第一版只输出建议，不会自动修改亚马逊广告后台。")


def render_strategy_selector(readable_strategy: pd.DataFrame) -> None:
    st.markdown("### 四种调整策略")
    st.caption("这里不是执行步骤，而是四种不同目标下的打法。你现在想要什么结果，就看对应那一种。")
    if readable_strategy.empty:
        st.info("当前数据量不足，还不能生成整盘调整策略。")
        return
    options = readable_strategy["策略类型"].tolist()
    selected = st.radio("选择你当前最想采用的打法", options, horizontal=True)
    row = readable_strategy[readable_strategy["策略类型"].eq(selected)].iloc[0]
    c_left, c_right = st.columns([0.42, 0.58])
    with c_left:
        st.markdown(
            f"""
            <div class="strategy-card">
              <span class="pill good-pill">推荐打法</span>
              <h3>{html.escape(str(row['策略类型']))}</h3>
              <p>{html.escape(str(row['适合什么时候用']))}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("#### 核心思路")
        st.write(row["核心思路"])
        st.markdown("#### 预算建议")
        st.write(row["预算建议"])
        st.markdown("#### 风险与观察重点")
        st.write(row["风险与观察重点"])
    with c_right:
        st.markdown("#### Campaign和广告组怎么建")
        st.text_area("搭建清单", value=str(row["关键词怎么放"]), height=520, disabled=True, label_visibility="collapsed")


def render_campaign_blueprint(campaign_plan: pd.DataFrame, keyword_plan: pd.DataFrame) -> None:
    st.markdown("### Campaign搭建图纸")
    st.caption("按这里新建Campaign、广告组、匹配方式和预算。每个Campaign都带可靠性和依据。")
    if campaign_plan.empty:
        st.info("当前没有可新建的Campaign建议。")
        return
    keep = [c for c in ["策略场景", "优先级", "建议新建Campaign", "建议日预算", "建议广告组", "放入关键词数量", "证据可靠性", "依据摘要", "执行提醒"] if c in campaign_plan.columns]
    st.dataframe(campaign_plan[keep], use_container_width=True, hide_index=True, height=260)
    for _, campaign in campaign_plan.iterrows():
        name = str(campaign.get("建议新建Campaign", ""))
        with st.expander(f"查看搭建细节：{name}", expanded=False):
            c1, c2, c3 = st.columns(3)
            c1.metric("建议日预算", money_text(campaign.get("建议日预算", 0)))
            c2.metric("放入关键词", int(campaign.get("放入关键词数量", 0)))
            c3.metric("证据可靠性", str(campaign.get("证据可靠性", "-")))
            st.write(campaign.get("Campaign目标", ""))
            st.caption(campaign.get("建组原因", ""))
            kw = keyword_plan[keyword_plan.get("建议Campaign", pd.Series(dtype=str)).astype(str).eq(name)].copy() if not keyword_plan.empty else pd.DataFrame()
            if not kw.empty:
                show_cols = [c for c in ["建议广告组", "关键词", "匹配方式", "建议竞价", "历史订单", "历史花费", "历史销售额", "历史ACOS", "证据等级", "触发依据", "执行前检查"] if c in kw.columns]
                st.dataframe(kw[show_cols], use_container_width=True, hide_index=True, height=280)


def unique_values(df: pd.DataFrame, column: str) -> list[str]:
    if column not in df.columns or df.empty:
        return []
    values = df[column].dropna().astype(str)
    return sorted([v for v in values.unique().tolist() if v and v != "nan"])


def _matches_selected_cell(value: object, selected: list[str]) -> bool:
    text = str(value or "")
    if not selected:
        return True
    parts = [p.strip() for chunk in text.split("|") for p in chunk.split(",") if p.strip()]
    parts.append(text.strip())
    return any(item in parts for item in selected)


def apply_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for column, values in filters.items():
        if column.startswith("__"):
            continue
        if column in out.columns and values:
            out = out[out[column].map(lambda value: _matches_selected_cell(value, values))]
    keyword = filters.get("__keyword", "").strip().lower()
    if keyword and "search_term" in out.columns:
        out = out[out["search_term"].astype(str).str.lower().str.contains(keyword, na=False)]
    return out


def render_filters(source: pd.DataFrame, key_prefix: str) -> dict:
    with st.expander("筛选条件", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        filters = {
            "portfolio": c1.multiselect("广告组合名称", unique_values(source, "portfolio"), key=f"{key_prefix}_portfolio"),
            "product_group": c2.multiselect("产品组", unique_values(source, "product_group"), key=f"{key_prefix}_product_group"),
            "asin_list": c3.multiselect("ASIN", unique_values(source, "asin_list"), key=f"{key_prefix}_asin"),
            "campaign": c4.multiselect("Campaign", unique_values(source, "campaign"), key=f"{key_prefix}_campaign"),
        }
        c5, c6, c7 = st.columns(3)
        filters["ad_group"] = c5.multiselect("广告组", unique_values(source, "ad_group"), key=f"{key_prefix}_ad_group")
        filters["word_root"] = c6.multiselect("共同短语词根", unique_values(source, "word_root"), key=f"{key_prefix}_word_root")
        filters["keyword_status"] = c7.multiselect("关键词状态", unique_values(source, "keyword_status"), key=f"{key_prefix}_status")
        filters["__keyword"] = st.text_input("搜索词包含", key=f"{key_prefix}_keyword")
    return filters


def render_table(title: str, caption: str, df: pd.DataFrame, height: int = 520) -> None:
    st.subheader(title)
    st.caption(caption)
    if df.empty:
        st.info("当前没有符合条件的数据。")
        return
    st.dataframe(df, use_container_width=True, height=height)
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(f"下载：{title}", data=csv, file_name=f"{title}.csv", mime="text/csv")


def main() -> None:
    inject_style()
    render_hero()

    settings = load_settings()
    with st.sidebar:
        st.header("判断参数")
        settings["target_acos"] = st.number_input("目标ACOS", 0.0, 2.0, float(settings.get("target_acos", 0.30)), 0.01, format="%.2f")
        settings["min_clicks"] = st.number_input("最低点击判断门槛", 1, 1000, int(settings.get("min_clicks", 8)), 1)
        settings["min_orders"] = st.number_input("最低订单判断门槛", 1, 1000, int(settings.get("min_orders", 2)), 1)
        settings["analysis_days"] = st.number_input("报表天数", 1, 365, int(settings.get("analysis_days", 30)), 1)
        settings["product_price"] = st.number_input("产品售价", 0.0, 9999.0, float(settings.get("product_price", 25)), 0.5, format="%.2f")

        st.divider()
        st.header("推排名核心词")
        ranking_text = st.text_area(
            "想重点推排名的大词",
            "",
            height=90,
            help="每行一个词。系统会优先把这些词或相关短语放入推排名策略。",
        )
        settings["ranking_terms"] = parse_lines(ranking_text)
        st.caption("可选填写：如果你有明确想推排名的大词，就一行一个填进去；不填时，系统会根据上传报表自动挑选表现较好的词根和搜索词。")

        st.divider()
        st.header("产品组 / ASIN分组")
        default_groups = settings.get("product_asin_groups", {}) or {}
        groups_text = st.text_area(
            "产品组配置（YAML）",
            yaml.safe_dump(default_groups, allow_unicode=True, sort_keys=False),
            height=160,
            help="例：\n水杯盖收纳:\n  - B0AAAA1111\n  - B0BBBB2222",
        )
        settings["product_asin_groups"] = parse_yaml_text(groups_text, default_groups)
        st.caption("没有ASIN列时，系统会尝试从Campaign、广告组、投放名里提取 B0 开头的ASIN。")

    files = st.file_uploader(
        "上传 Search Term Report 或包含 Customer Search Term 字段的广告报表",
        type=["csv", "xlsx", "xls", "tsv"],
        accept_multiple_files=True,
    )

    if not files:
        st.info("请先上传搜索词报表。上传后会出现筛选条件、分析表和运营策略汇总。")
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

    analysis_base = product_terms.copy()
    classic_tables = search_term_insight_tables(analysis_base, settings)
    strategy_tables = build_operator_strategy_summary(analysis_base, settings)
    architecture_tables = build_campaign_architecture_plan(analysis_base, settings)
    readable_strategy = build_readable_strategy_brief(architecture_tables["campaign_plan"], architecture_tables["keyword_plan"])
    recommendation_audit = build_recommendation_audit(analysis_base, architecture_tables["campaign_plan"], architecture_tables["keyword_plan"], settings)

    if analysis_base.empty:
        st.warning("当前筛选条件下没有可分析的数据。请减少筛选条件或换一个广告组合/Campaign。")

    render_account_snapshot(analysis_base, architecture_tables, classic_tables)
    render_campaign_charts(analysis_base)

    strategy_tab, main_tab, product_tab, data_tab = st.tabs(["运营策略", "搜索词分析", "产品/ASIN", "数据质量"])

    with strategy_tab:
        st.markdown("## 整盘广告重构方案")
        st.markdown('<div class="section-note">先判断本次建议是否可靠，再选择一种调整目标，最后按Campaign搭建图纸执行。</div>', unsafe_allow_html=True)
        render_reliability_panel(recommendation_audit)
        render_strategy_selector(readable_strategy)
        render_campaign_blueprint(architecture_tables["campaign_plan"], architecture_tables["keyword_plan"])
        detail_tab1, detail_tab2, detail_tab3 = st.tabs(["降ACOS明细", "低预算高回报明细", "推排名明细"])
        with detail_tab1:
            render_table("降低ACOS-逐词辅助表", "用于解释为什么某些词要降价、隔离或加入否定审核。", strategy_tables["reduce_acos"], height=520)
        with detail_tab2:
            render_table("低预算高回报-逐词辅助表", "用于解释哪些词已经验证过转化，可以进入精准收割Campaign。", strategy_tables["efficient_budget"], height=520)
        with detail_tab3:
            render_table("推排名大词-逐词辅助表", "用于解释哪些共同短语词根适合单独做排名推进预算池。", strategy_tables["rank_push"], height=520)

    with main_tab:
        st.markdown("## 搜索词直观分析")
        tab1, tab2, tab3, tab4 = st.tabs(["表现好，可以扩量", "需要否定", "同场景可放同广告组", "同词根长尾词"])
        with tab1:
            render_table("表现好，可以扩量", "有订单，且ACOS不高于目标。适合新增Exact精准词、单独广告组，或提高预算/竞价。", classic_tables["good_terms"])
        with tab2:
            render_table("需要否定", "点击达到判断门槛、花费了钱但没有订单。请先人工确认相关性，再决定Negative Exact或Negative Phrase。", classic_tables["negative_terms"])
        with tab3:
            render_table("同场景可放同广告组", "这些词围绕同一个需求、场景或共同短语词根，可以考虑放在同一个广告组里统一测试和控价。", classic_tables["ad_group_clusters"])
        with tab4:
            render_table("同词根长尾词", "这里的词根是多个搜索词共同出现的短语，不是单个单词，适合做词组匹配和长尾词收割。", classic_tables["root_longtails"])

    with product_tab:
        st.markdown("## 按产品组 / ASIN看关键词")
        ptab1, ptab2, ptab3 = st.tabs(["产品组-搜索词明细", "产品组-同词根汇总", "ASIN汇总"])
        with ptab1:
            render_table("产品组-搜索词明细", "按产品组把多个ASIN的同一搜索词合并，看这个词到底适合哪一组产品。", product_terms)
        with ptab2:
            render_table("产品组-同词根汇总", "同一产品组下，把相同共同短语词根的长尾词汇总，方便建立同一个广告组。", product_roots)
        with ptab3:
            render_table("ASIN汇总", "查看每个ASIN在上传报表里的搜索词、花费、订单和销售额表现。", asin_summary)

    with data_tab:
        st.markdown("## 数据质量")
        st.caption("这里用于检查文件识别、字段匹配和清洗情况。建议导出或执行前先看一眼。")
        st.dataframe(metadata, use_container_width=True, hide_index=True)
        render_table("四种调整策略-表格版", "同样内容的表格版本，方便下载。", readable_strategy, height=360)


if __name__ == "__main__":
    main()








