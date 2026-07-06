# 亚马逊广告自动诊断与优化建议系统

这是一个可在 Windows 本地运行的亚马逊广告报表分析系统。用户上传亚马逊后台导出的 CSV 或 Excel 报表后，系统会自动识别字段、清洗数据、计算指标、诊断问题、生成中文优化建议，并导出 Excel 分析报告。

第一版只输出建议，不会登录亚马逊后台，也不会自动修改广告、竞价、预算或否定关键词。

## 安装方式

建议使用 Python 3.11 或更新版本。

```bash
pip install -r requirements.txt
```

## 启动方式

方式一：

```bash
streamlit run app.py
```

方式二：

双击：

```text
启动亚马逊广告分析系统.bat
```

启动后浏览器会打开本地页面。

## 云端网页版

如果你不想每次在本机启动，可以部署成云端网页版。本项目已包含：

- `.streamlit/config.toml`
- `runtime.txt`
- `render.yaml`
- `Dockerfile`
- `CLOUD_DEPLOYMENT.md`

最简单方式是上传到 GitHub 后部署到 Streamlit Community Cloud：

```text
https://share.streamlit.io/
```

部署时 Main file path 填：

```text
app.py
```

详细步骤见：

```text
CLOUD_DEPLOYMENT.md
```

提醒：云端版会把你上传的广告报表传到部署平台服务器处理。如果数据敏感，建议使用本地版或部署到你自己的服务器。

## 支持的报表

系统通过字段特征和配置文件识别报表，当前配置覆盖：

- Sponsored Products Search Term Report
- Sponsored Products Targeting Report
- Sponsored Products Advertised Product Report
- Sponsored Products Campaign Report
- Sponsored Brands Search Term Report
- Sponsored Brands Campaign Report
- Sponsored Display Campaign Report
- Placement Report
- Business Report
- Search Query Performance Report
- Bulk Operations File
- Purchased Product Report

至少已针对 Sponsored Products Search Term、Targeting、Campaign 三类报表提供样例和自动测试。

## 字段映射

字段别名在这里维护：

```text
config/field_aliases.yaml
```

例如以下字段会自动识别为同一标准字段：

- `Spend / Cost / 花费 / 广告花费`
- `Sales / 7 Day Total Sales / 销售额`
- `Orders / Purchases / 订单量`
- `Search Term / Customer Search Term / 客户搜索词`
- `Campaign Name / 广告活动名称`

如果系统发现无法识别的字段，可以在“数据上传”页面展开“手动字段映射”，把原始字段指定为标准字段。

## 业务参数

在“业务参数”页面可以设置：

- 目标ACOS
- 盈亏平衡ACOS
- 产品售价和成本结构
- 最低点击、曝光、订单判断门槛
- 竞价最大上调和下调幅度
- 品牌词、竞品词、核心词、否定词
- ASIN 与 SKU 对应关系

如果用户不修改，系统使用默认配置：

```text
目标ACOS：30%
最低有效点击量：8
高点击无订单判断值：10
低曝光判断值：500
竞价下调幅度：15%
竞价上调幅度：10%
```

默认值文件：

```text
config/default_settings.yaml
```

## 核心公式

系统会自动处理分母为 0 的情况。

```text
CTR = Clicks / Impressions
CPC = Spend / Clicks
CVR = Orders / Clicks
ACOS = Spend / Sales
ROAS = Sales / Spend
CPA = Spend / Orders
RPC = Sales / Clicks
TACOS = Spend / Total Sales
Break-even CPC = Conversion Rate × 产品售价 × 盈亏平衡ACOS
Target CPC = RPC × Target ACOS
Recommended Bid = Current Bid × Target ACOS / Actual ACOS
```

竞价建议会受最低竞价、最高竞价、单次最大上涨和单次最大下降限制。

## 诊断规则

规则阈值不写死在界面中，配置文件在：

```text
config/optimization_rules.yaml
```

当前规则包括：

- 高花费无订单
- ACOS明显高于目标
- ACOS略高于目标
- 低ACOS且订单稳定
- 点击率过低
- 点击率高但转化率低
- 曝光低但转化好
- 搜索词收割
- 搜索词否定
- 关键词重复和内部竞争
- 广告位优化
- CPC异常
- 流量结构风险

系统不会机械地把所有无订单词都否定。品牌词、核心词、低可信度词会优先建议观察或降价。

## 页面说明

- 数据上传：上传单个或多个文件，查看识别结果、字段匹配、数据预览和错误提示。
- 业务参数：维护目标ACOS、成本、阈值和词库。
- 账户总览：查看Spend、Sales、Orders、ACOS、ROAS、CTR、CVR、Profit和历史记录。
- 问题诊断：按P0/P1/P2、Campaign、动作、可信度筛选建议。
- 搜索词分析：查看优质词、潜力词、高花费无订单词、建议收割词等。
- 竞价优化：查看当前竞价、CPC、目标CPC、盈亏平衡CPC和建议竞价。
- 预算优化：查看Campaign预算增减建议。
- 广告位分析：查看Top of Search、Rest of Search、Product Pages等表现。
- 利润分析：查看广告前毛利、广告后利润和盈亏情况。
- 导出报告：导出完整Excel报告和Bulk File草稿。

## Excel报告

报告文件默认保存在：

```text
data/exports/
```

文件名类似：

```text
Amazon_Ads_Optimization_Report_日期.xlsx
```

包含工作表：

1. Executive Summary
2. Account Overview
3. Campaign Analysis
4. Ad Group Analysis
5. Targeting Analysis
6. Search Term Analysis
7. Bid Recommendations
8. Budget Recommendations
9. Negative Keyword Suggestions
10. Search Term Harvesting
11. Placement Analysis
12. SKU Analysis
13. Profit Analysis
14. Data Quality Report
15. Rule Trigger Log
16. Raw Data Cleaned

Excel 会冻结首行、开启筛选、调整列宽，并对百分比、金额和P0/P1/P2做基础格式化。

## 历史记录

系统会把每次分析的汇总结果保存到本地 SQLite：

```text
data/processed/history.sqlite
```

用于后续比较不同时间段表现。

## Bulk File草稿

当前版本只提供待人工审核的 Bulk Operations 草稿：

```text
bulk_operations/
```

系统不会声称兼容所有亚马逊Bulk模板，也不会自动执行修改。导出的草稿会标记：

```text
Requires Manual Review = Yes
```

## 示例数据

示例数据在：

```text
data/samples/
```

可直接上传测试。

## 常见错误处理

- 文件乱码：系统会自动识别编码；如仍异常，请另存为 UTF-8 CSV 后再上传。
- 字段无法识别：在“数据上传”页面使用手动字段映射，或修改 `config/field_aliases.yaml`。
- 报表类型未知：检查是否包含 Campaign、Spend、Clicks 等关键字段，或修改 `config/report_types.yaml`。
- 指标为0：通常是销售额、点击或曝光字段缺失，查看 Data Quality Report。
- Excel无法打开：确认本地没有正在占用同名报告文件。

## 数据安全

- 所有数据只在本机处理。
- 原始上传文件不会被自动删除或修改。
- 系统不连接亚马逊账号。
- 系统不自动暂停广告、不改竞价、不改预算、不添加否定词。
- 所有建议都需要人工审核后再执行。

## 运行测试

```bash
pytest
```

测试覆盖除数为0、空值/字段映射、中文表头、英文表头、主要规则、竞价边界和Excel导出。
