# 按问题选择计算

自动全景扫描由 scripts/acceptance_queries.py 固定规划并通过执行器取证。`sql/` 的 8 个 SQL 保留为人工算数示例，程序不会读取它们来生成扫描或议题任务。4.x 优先使用 question-jobs / question-collect；人工示例不能替代完整计划或直接获得调查完成信用。

这些是 Doris 只读示例，不是流水线。涉及 SPU 计数、价格带或商品轨迹时，FILTER 必须限定单一平台；示例中的 product_id 单键聚合不适用于跨平台商品数。中国合并展示不能取消平台身份边界。先固定国家、同一父类目、月份和粒度，再选择/改写查询；沿销量、金额两条线检查贡献，模型决定下钻深度。全部历史观察应记录实际覆盖与分页，查询失败不写成零。

| 例子 | 回答的问题 |
|---|---|
| `00_fingerprint.sql` | 国家各月到货行数、SPU、销量、销售额；不提交/覆盖旧指纹 |
| `10_category_presence.sql` | 路径首次观测、中途缺失、末端未恢复、预期但全期未见 |
| `20_category_results.sql` | 类目全历史四项结果概览：SPU、销量、金额、价格分位；首次月保留 |
| `30_change_contribution.sql` | 固定父类目两期的互斥子集增减：金额/件数、净贡献、增长百分点、抵消 |
| `40_spu_structure.sql` | SPU 汇总量价、销售额排名与累计份额；观察两期头部与商品结构 |
| `50_price_bands.sql` | 同一固定边界下各价带商品数和金额分布；未知价格独立列出 |
| `60_mapping_coverage.sql` | 源层完整键是否进入 std；重复是否放大，匹配两侧量额与键内差异是否一致 |
| `70_variant_price_trace.sql` | 已定位 SPU 的同规格相邻自然月跳价；金额筛选在取前值之后 |

## 参数与计算边界

- `TABLE` 为三段式 `internal.flywheel.monthly_sales_with_dim` 或 `internal.flywheel.monthly_sales_wide_new`。前者主观察公司 std，后者核对采集范围与原始行；必要时使用已查明粒度的子查询。两表月份均按 `YYYY-MM-01` 比较。
- `SITE` 一次一个国家（中国 `cn`，其余 `US/DE/JP/MX/BR`）。不按平台拆中国。先核实 product_id 命名空间；若发现跨平台同 ID 指向不同产品，核对身份再构造复合键，不能默默合并。
- `START/END` 为月初，含首尾；`BASE/CURRENT` 为两比较月。检查两期父类目是否确有观测、金额/销量是否缺失；无观测集合按观测份额为零分解，不代表真实零销量。
- `FILTER` 固定父类目或采集范围，默认 `1=1`；公司完整路径可用 `CAST(JSON_ARRAY(stdcategory1,stdcategory2,stdcategory3) AS STRING)`，不要只用末级名称或自创分隔符。品牌/SPU 也可用 JSON_ARRAY 保留 NULL 身份。
- `PATH_EXPR` 是单一观察层级/轴。平台按实际完整 `sub_category` 核验，核对其格式；范围页的父节点要用对应前缀归并，不把采购父节点误当全量叶节点枚举。公司层级统计不能混加；上级 SPU 重新去重。
- `EXPECTED_PATHS_SQL` 返回同口径的 `category_path, active_from, active_to`，来自有效项目范围或已核实完整清单。可有多个有效区间；无权威枚举时明确范围限制。`MONTH_COUNT` 为自然月数。首次观测在窗口首月不是市场诞生，`terminal_missing` 不是已证实永久退市；是否应该有记录还要看 `is_expected`。NULL 路径的观测仍保留，用于排查分类缺失，不能当成无采集。
- `GROUP_EXPR` 将父类目每条细行归到唯一子集。SPU 例如 `JSON_ARRAY(NULLIF(TRIM(product_id),''))`，品牌例如 `JSON_ARRAY(std_brand_name)`。子集 SPU 数可重叠，不相加冒充父类目数。不同级别贡献也不重复相加。
- `CANDIDATE_FILTER` 在全量分母后执行。`30` 的 `ORDER_EXPR` 分别用 `ABS(amount_change) DESC`、`ABS(units_change) DESC`，保留两条线的重要候选与抵消项；不能只翻金额前几页代替销量检查。净贡献分母接近零时，以绝对变动和增长百分点解释。
- `40` 两期分别取，结合 `30` 获取新增、消失与大变化 SPU；金额排名用来识别主要金额组成，不是唯一异常优先级。负值、缺失或无效 ID 先核查，再解释累计份额。价格用 SPU 总额/总件数。
- `BAND_EXPR` 在相同国家/类目、比较期内固定边界，例如 `CASE WHEN price < 100 THEN '01:<100' WHEN price < 300 THEN '02:100–300' ELSE '03:>=300' END`，仅为参数演示，不是通用价格标准。查询自动列未知价格/无效 ID；`BAND_EXPR` 未命中的有效价格保留到 `unassigned_band`，先修正价带定义再解释分布，各带数量/金额应与类目观测总量核对。分位数仅作概览，不能替代实际价带分布。数据月全无记录不填零。
- `60` 是从原始范围出发的覆盖检查，不证明所有 std 记录都在源层；若维表多出金额或键，再反向 anti-join。同时核对匹配两侧销量、金额和逐完整键的 `amount_unequal_keys/units_unequal_keys`，避免正负差异在月总和抵消；差异是待核线索，需检查精度、合法变换与下游口径。两侧缺失量额计数分别披露；SUM 仅代表非空观测，匹配集合全空保留 NULL，不当真实零或已验证守恒。重复完整键需要查是否真实重复/合法多标签；不直接删重，亦不将不同子体跨类目当父体错误。
- `70` 限定已选 SPU 和观察窗口；`MIN_AMOUNT/RATIO` 按具体问题选，不预设通用判错阈值。跳价不自动证明错价；原始负值/缺失仍需单独看明细。
- 人工改写后须移除示例注释和末尾分号，再交只读执行器；执行器拒绝注释、分号及未闭合字符串。保留实际 SQL、完整分页和原生回包，不将工具错误写成零。
- `PAGE_SIZE/OFFSET` 明确控制分页，工具 `max_rows` 不小于页大小。检查 `total_rows/selected_rows/selected_parts` 与已取记录，记录任何未读部分；不靠全局 LIMIT 宣称全扫描。`00/60` 每月一行，也要匹配工具返回上限。

源行重复、空 ID 或金额/销量缺失会影响所有结果，先明确口径与问题暴露。原始字段、相邻月与连接守恒必须可回溯；贡献是定位线索，敏感性分析和证实错误额另行注明。尾部对四项结果均无实质影响后只集中提示，计算仍包含其全部观测。
