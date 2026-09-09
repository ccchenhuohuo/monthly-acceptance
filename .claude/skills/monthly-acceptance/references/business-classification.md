> 版本边界：以下为 3.x 冻结兼容资料。4.0 新运行只使用 [全景与议题权威协议](question-workflow.md)，不执行下文逐候选义务、defer 或全体市场原因解释门槛。

# 业务分类与报告位置（3.3）

先做冻结范围的国家、平台、自然月、数据层检查，再决定需要回答哪些业务问题。两个业务决策保存在现有案件中；不改变缺陷、原因、调查、修复、主审和放行状态。

| 决策 | 含义 |
| --- | --- |
| `investigation_route: investigate` | 有宏观异常、系统性迹象或基础缺口；影响未知时先读已有全景聚合、补聚合缺口，再下钻主要贡献 |
| `investigation_route: merge` | 指向现有直接调查案件的活跃任务或有效查询，先共享；原义务各自回答，剩余范围单独补查 |
| `investigation_route: defer` | 主 Agent 核查有限影响域、累计及关联范围后暂留，本轮不自动派发深查 |
| `report_placement: main` | 改变主要分析判断、关键范围缺失或重要实质风险仍未界定 |
| `report_placement: brief` | 有持续观察价值，末尾汇总影响与重查条件 |
| `report_placement: appendix` | 影响有限，保留明细、证据与真实状态 |

不按 SKU 数量、2% 取证排序、1% 调查残余容差决定正文。一个大贡献 SPU 可以是正文；已证实的小问题可以是附件；大幅业务波动须经核查，不能直接记为缺陷。分类语义争议不会自动获得调查或正文优先权。数量级、丢行、放大和错联仍按实际影响处理。

## 正常命令路径

新建运行后只执行该运行冻结的脚本。`investigation-plan`、`case-plan` 保留全部原始义务，然后：

```sh
python <RUN>/inputs/skill/scripts/acceptance_run.py case-queue <RUN> --output <QUEUE.json>
python <RUN>/inputs/skill/scripts/acceptance_run.py case-classify <RUN> --coordinator <MAIN_ID> --file <DECISIONS.json>
python <RUN>/inputs/skill/scripts/acceptance_run.py investigation-assign <RUN> --coordinator <MAIN_ID> --agent <ACTUAL_AGENT_ID> --next
```

`case-classify` 输入是问题组数组；组内案件可以共用一次分流审核。每项必需：

```json
{
  "case_ids": ["现有案件ID"],
  "investigation_route": "investigate",
  "report_placement": "main",
  "business_question": "这个处理环节是否放大本月国家总量和核心业务类目？",
  "route_reason": "引用已有聚合值或准确缺失范围的一句话",
  "placement_reason": "该问题会影响的市场规模、趋势或价格带判断",
  "next_check": "先核对国家与数据层总量，再拆主要贡献",
  "stop_condition": "已回答的问题、已审核影响边界与仍需继续的范围",
  "evidence_locators": ["按review-v32-schema.md填写真实定位对象"]
}
```

定位对象沿用 `evidence_id / pointer / record_sha256 / object_keys / fields / purpose`，必须来自实际已保存证据。占位文字不是可执行输入。`case-queue` 列出可分派问题、原义务、两个决策、理由、下一核查及停止条件。缺少分类的旧记录显示尚未判断，默认保留调查适用性；重要性待判断不等于已证宏观缺陷。

## 暂留的组审核

先核查影响域的范围与证据。低成本聚合仍不能界定风险时保持 `investigate`，不要为了暂留先穷举全盘商品。已有完整总体明细时，可复用它生成累计统计；缺少该证据且确需做暂留审核时，主 Agent 统一收集一次所需国家与月份的总体，供各问题共享。`machine-collect` 的目标证据已完整保存后即能参与统计，不必为分类先完成所有逐商品原因调查。

`case-assess` 的输入包含 `case_ids` 和 `assessments`，后者以案件 ID 为键，每项为：

```json
{
  "specs": [{"site": "US", "level": 0, "path": [], "window_start": "2026-06", "window_end": "2026-07"}],
  "entities": [["US", "Amazon", "实际parent或product_id"]]
}
```

这里只接受该国完整标准层总体，并与独立国家基线及价格带对账。平台始终留在精确实体键中；同源 SPU 在国家和业务类目重算，不直接相加。必须覆盖案件全部自然月和有效比较基期；结果变化案件还必须纳入所有原比较中的变化实体，不能只选小问题隐藏未解释的宏观变化。缺页、失败、身份或数值未知、样本商品遗漏、选入未观测实体均不能形成暂留统计。原始缺口、项目基础义务、必要历史回归继续保留调查，不走暂留。

```sh
python <RUN>/inputs/skill/scripts/acceptance_run.py case-assess <RUN> --file <ASSESSMENT_INPUT.json> --output <PREVIEW.json>
```

阅读输出的国家及每个业务类目统计，再将 `assessments / binding / facts_sha256` 连同上面的两个决策及实际定位一起交 `case-classify`。暂留另外必需：

- `uncertainty_boundary`：为何列出的精确实体涵盖本问题全部有限不确定域；可能存在数量级错误、丢失未知对象或系统性扩散时不能用已观测暴露充当风险上界。
- `metric_review`：`spus / bands / units / amount` 四项分别说明已核范围、统计值、上界推导及其对分析的影响；程序统计不自动证明业务容忍。
- `counterevidence`：已检验的替代解释与具体反证。
- `cumulative_review`：阅读去重累计、各基期正负变化后的判断。
- `unassessed_scope_review`：累计输出里尚无影响统计的相关案件如何继续调查，为何不能据此推定本问题有限域之外也正常。
- `reopen_conditions`：新增信号、扩散、复发、月份、规则/批次、数据或证据变化后的重新调查条件。

这些是**主 Agent 的分流审核**，不替代真实逐义务业务主审。程序验证范围、完整总体、对象、累计、引用和版本，无法凭一句话验证 Agent 是否正确理解有限域。`facts` 明确区分观测暴露与错误上界，未知相关案件不记零。

累计包含所有已登记风险类型；同一国家跨历史月份检查，共同 `processing_rule_ids / batch_ids` 也会关联其他国家，金额按国家分开。新增相关成员、改变影响选择、数据/规则版本或证据会使旧审核失效，回到先查范围的队列。`case-assess` 也可接收组数组，先将本批所有影响选择合到同一累计视图，再输出每组待审核材料。一次批量提交涉及新的多个影响组时，应使用这份共同累计视图再逐组审核；不要复用不同累计版本的哈希。

已有任务不能被分类器静默撤销。先用原 `investigation-progress` 留已做工作和未完成问题，再 `investigation-requeue`，然后由主 Agent审核暂留。分类为 `defer` 后显式派发也会被拒绝；重新需要调查时先重新分类。

## 合并、查询复用与差额补查

`merge` 增加 `merge_target_case_id / shared_question / remaining_checks`；`shared_question` 必须对应目标的 `business_question`，`shared_job_ids` 可引用已经通过完整校验的目标查询。目标必须直接 `investigate` 且分类有效，有活跃任务或有效证据，不能自合并、循环或指向无人执行的空目标。

源任务按原范围重新生成查询计划；只共用国家、层、平台总体、路径筛选、查询用途和月份完全相同的查询。窗口可以部分覆盖：共有月份复用，不同基期月份仍列补查。不同冻结运行不会自动共享。结果完整性依然经过实际 SQL、页、行数、对象及证据哈希核验；失效结果不当成功使用。失败或显式强制重取记录具体重试原因。

3.3 的共享查询采用稳定自然月单元，避免外层窗口不同导致相同 SQL 的执行凭证失配。父体/SKU 追踪仍保留原精确平台身份分片。源义务不同但共有取证正在执行时，共用任务；队列等待共有查询完成后才派发源义务剩余核查。同运行内并发请求同一查询会等待同一执行结果；跨进程仍沿用运行锁，跨子包由主 Agent 在主包统一取证并通过已有证据导入入口复用。

停止调查只能因为问题已回答、已审核有限边界或真实外部缺证，不能因为查询多、耗时或困难。合并只减少重复取证，不能认定同一根因，也不自动增加已调查/已主审数。

## 完成与报告

保留 `expected_obligations` 原数；新字段 `deferred` 与 `applicable_investigation_obligations` 单列。`substantive_completed / verified` 只计真实调查；`substantive_complete` 仍要求原始总体全部调查。交付门槛用 `applicable_investigation_complete`，并继续独立检查必查范围、历史、抽查、主要残余、原始证据和报告覆盖。即使适用工作完成，存在暂留时最高只能有保留接收，不能无保留放行。

正式已审 finding 用 `obligation_ids` 对应当前分类与实际主审；阶段观察可用 `case_ids` 关联范围，展示关联的原义务数量，但不增加已核实或报告主审覆盖数。`report_placement` 可省略，由渲染读取当前案件审核，不允许用报告手填值覆盖。不同报告位置须分开写。缺陷、修复和调查结论仍来自原主审，不因暂留伪造 finding。正文摘要自动列重要待核范围；brief 只给汇总，appendix 保留完整事实证据。旧 `presentation: auxiliary` 继续可读。报告、看板、`classification-queue.json` 与 checkpoint 使用同一分流状态。七类概览始终展示已查范围和缺口，缺一个维度不表示正常。
