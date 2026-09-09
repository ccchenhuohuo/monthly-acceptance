> 版本边界：以下为 3.x 冻结兼容资料。4.0 新运行只使用 [全景与议题权威协议](question-workflow.md)，不执行下文逐候选义务、defer 或全体市场原因解释门槛。

# 3.2 调查答卷、原始证据与主审字段

新门槛在冻结 `policy.version >= 3.2` 或 `review_gate.enabled=true` 时启用。旧 3.1 包沿其冻结代码运行。以下为字段模板，不是业务结论；`<…>` 必须替换为本运行的实际 ID、内容与计算。不得把测试里的合成证据带入真实运行。

程序可以核对记录、具体字段、对象关系、版本、算术和职责分离。程序无法验证 Agent 是否真正理解了记录，也无法从措辞或相关性推导因果；这些是有姓名、理由和版本绑定的独立复核与主审责任。

## 1. 自动生成并冻结的合同

`investigation-plan` 为每项原始义务生成 `contract`。`investigation-assign` 把完整合同写入子包 `brief.json`，并在中心任务固定 `contract_hashes`。主要字段：

- `obligation_id / source_id / source_hash / scope / original_scope`。
- `object_population`：全部范围成员、明确商品/SKU 清单（已知时）、平台及正式身份维度。`null` 不表示空总体，而表示须从完整限定范围中建立。
- `history_window.required_comparison_months`：原观察月、全部历史明确月份、自然月比较基期；另保留冻结历史起讫、缺口前最近完整基线及逐月 raw/std 追踪规则。
- `questions[] / required_evidence[] / drill_triggers / completion_conditions / blocking_conditions`。
- `allowed_fulfillment_modes / scope_change_authority / policy_sha256 / sha256`。

子 Agent 不得改合同。`investigation-amend-scope` 仅主 Agent 可用，输入 `new_scope / reason / evidence_locators`；先保存并重排全部受影响任务，保留原范围、前版本、原始证据和变更事件。固定候选的数值比较不能改分母；扩展或另一个范围应新增义务承接。

## 2. 原始证据 locator

调查每个 `checks.<check_id>` 增加 `evidence_locators[]`。每个定位须引用 `evidence_ids` 中的真实证据，选中一条实际 JSON 记录，再明确核对字段和真实身份键。例如实际工具响应中某一行：

```json
{
  "evidence_id": "<真实 ev-ID，主包引用在子包中加 main: 前缀>",
  "pointer": "/response/data/0",
  "record_sha256": "<acceptance_core.digest(所选完整记录对象)>",
  "object_keys": {
    "site": "<实际站点>", "month_dt": "<实际自然月首日>",
    "platform": "<实际平台>", "product_id": "<实际父体>",
    "sku_id": "<实际 SKU>", "std_path": "<实际完整 JSON 路径字符串>"
  },
  "fields": ["product_id", "sku_id", "units", "amount", "std_path"],
  "purpose": "<这些字段如何支持这项具体判断；写明结论边界>",
  "check_ids": ["sku_identity", "price_volume"],
  "role": "support"
}
```

字段名须按真实源记录选择，不能照抄不存在的字段。`object_keys` 中每项必须与所选记录相等，国家、月份和路径/具体历史对象必须对应合同。`pointer` 不能是整个文件，`record_sha256` 不等于文件哈希。合法角色还可描述 `historical_context / counterevidence / destination`；角色文字本身不豁免范围校验。跨路径定位须附 `target_relation`（另一个有真实目标路径的 locator），并能核对相同国家、平台、可追踪 SKU；NULL、空白及“0”不支持此关系。

历史原文或外部凭证可归档为带 `source_path` 或对应站点/月份/路径身份的结构化证据记录，记录含原始 `text` 字段。locator 选择该记录，并加 `text_field: "text" / lines: [实际开始行, 实际结束行] / excerpt_sha256`，哈希按这些行以换行符连接后计算。原文必须原样归档，来源说明不能冒充原文。

## 3. 子 Agent 答卷

顶层仍为 `task_id / attempt / agent_id / answers[]`。每项保留 3.1 的 `obligation_id / scope / checks / conclusion / next_action / business_verdict / repair_state / defect_confirmed / impact`，再增加：

```json
{
  "contract_sha256": "<本义务当前合同 sha256>",
  "fulfillment_mode": "direct",
  "status_dimensions": {
    "execution": "success",
    "investigation": "completed",
    "defect": "not_found",
    "cause": "explained",
    "impact": "quantified",
    "review": "pending",
    "acceptability": "qualified"
  },
  "checks": {
    "<每个 required_checks ID 都要有一项>": {
      "status": "verified",
      "finding": "<针对本对象与比较的实际发现>",
      "actions": ["<已经执行的具体动作>"],
      "counterevidence": "<实际检查的主要反证和结果>",
      "evidence_ids": ["<实际证据ID>"],
      "evidence_locators": ["<按上一节填写对象，不能保留本占位字符串>"]
    }
  }
}
```

枚举和值的意义：

| 独立状态 | 允许值 | 完成提交的限制 |
| --- | --- | --- |
| execution | success / failed / timeout / not_executed | 提交完成答卷只允许 success；其他情况保存进度并重排 |
| investigation | completed / in_progress / blocked_external / capacity_pending | 完成答卷只允许 completed 或真实 blocked_external |
| defect | confirmed / not_found / undetermined | 与 defect_confirmed 一致，不能由原因或完成状态推导 |
| cause | explained / partially_explained / unexplained / not_applicable | 有残余不能 explained；仅发现缺陷不等于已解释 |
| impact | quantified / bounded / unknown / not_applicable | unknown 须保留阻塞或继续工作 |
| review | pending / approved / returned | 子 Agent 只能 pending；批准由中心审核记录计算 |
| acceptability | pass / qualified / reject / undetermined | 必须与 business_verdict 一致；原因部分未解释、缺陷未定或外部阻塞不可 pass |

外部阻塞保留 `blocking.type / required_record / provider_role / attempted_access / why_unavailable / decision_impact / resume_action / blocked_checks`。`provider_role` 使用材料责任角色：`missing_external_record → data_provider`、`missing_contract → contract_owner`、`missing_definition → taxonomy_owner`、`missing_internal_data → internal_data_owner`。Agent 名字或本地查询执行者不能冒充外部材料负责方。

`resume_condition` 是结构化对象，`required_record / provider_role` 必须与 blocking 一致，`scope` 必须与原合同相同：

```json
{
  "trigger": "external_material_available",
  "required_record": "<具体缺失的外部材料>",
  "provider_role": "data_provider",
  "required_fields": ["sku_id"],
  "scope": "<原义务完整 scope 对象>"
}
```

`attempt_receipts[]` 的每项是实际原始 locator 加 `attempt`。所有凭证必须列入该动作 `evidence_ids`。当前有两种可核对的操作：

- `attempt: {method: "source_field_absence", request_sha256: "<原始 request 对象 digest>", missing_fields: ["sku_id"]}`：定位 `/response/data/N` 的真实成功查询行。程序沿正式导入记录追到原运行，核对 `query-result / configured:doris`、实际 `doris.exec_query` started/finished 事件、SQL 文件、实际 SQL 与请求哈希，并确认声称缺失字段确实是空值或不可追踪 SKU。`missing_fields` 必须等于恢复所需字段；正常商品行、手工附件、查询失败与容量记录不能替代此凭证。
- `attempt: {method: "frozen_input_absence"}`：调用 `acceptance_review.capture_frozen_input_absence(run, relative_path, obligation["scope"], required_record, provider_role, required_fields)` 实际检查指定 `inputs/project/…` 或 `inputs/internal/…` 材料是否未交付。返回真实 evidence ID，locator 定位 `/inspection`，使用实际记录 hash/fields 及 `operation / relative_path` 作 object_keys。程序重查冻结 inventory、缺失路径和操作事件；已有文件、未执行 SQL 或脚本不能作为外部缺失材料。

未支持的原始凭证格式需要先实现有实际来源校验的适配器。不能为让答卷通过而改写凭证成上述形状。程序验证记录与操作来源；材料是否必要、外部角色是否实际负责仍必须由主审核实。容量、超时和 SQL 失败保存进度并重排，不属于外部缺证。

保留低影响未解释部分时必须 `qualified / undetermined / reject`，并提供 `residual_boundary: {state: "bounded", bound_scope: "recorded_population_only", derivation, recheck_condition, acceptance_effect, evidence_locators, upper_bounds: {spus, bands, units, amount}, recomputations: [...]}`。每个指标的上界都须从真实操作数复算，覆盖程序确定的全部残余对象，上界不得小于未解释暴露，且上界本身也须低于冻结容差。无法定位完整残余对象、平台身份歧义或不可追踪 SKU 时不能进入这条完成路径。冻结 1% / 0.5pp 仅是已量化调查残余的继续排查容差；观察变化、缺数据或缺身份不能充当风险上界。主审接受后状态是 `reviewed_residual`，独立于 `verified`，也不获得无保留放行资格。

`critical_conflicts[]` 每项为 `conflict_id / status / resolution / evidence_locators`。关键冲突未解决，主审必须列补查要求退回。退回后新答卷的 `return_responses[]` 必须精确承接任务 `unresolved_return_items` 的每个 `return_id`，给出 `response / evidence_locators`。

## 4. 数值及缺口证明与原因信用

结果变化保留原有 `population_proof`，缺口保留 `gap_proof`；类型、完整 SQL、分页、SKU 全体、双期守恒或缺口前基线逐月追踪约束都不变。`investigation-proof` 先机算，再把 `computed_impact` 填入答卷。两者每组 `entity_assessments` 的 `status=verified` 在 3.2 仅表示已执行实体内部核查，不能自动清零原因残余。

每组可以有 `cause_claims[]`（尚未解释明确为空）。一项格式如下：

```json
{
  "claim_id": "<本证明内唯一原因ID>",
  "product_ids": ["<该组内确实被解释的精确对象>"],
  "metrics": ["spus", "bands", "units", "amount"],
  "status": "supported",
  "causal_basis": "direct_mechanism",
  "mechanism": "<对象、时间、机制和这部分变化的证据关系>",
  "alternative_explanations": "<实际比较的替代解释、反例和结果>",
  "evidence_locators": ["<覆盖所称对象完整比较成员的真实原始记录定位>" ]
}
```

`status` 可为 `supported / undetermined / refuted`，只有 supported 获得解释信用。`causal_basis` 只接受 `direct_mechanism / controlled_comparison / deterministic_rule`；这些标签不自动证明业务语义，主审仍需核对。`metrics` 只能填写实际已解释的指标；每个对象×指标只能属于一个 supported 原因，关联证据必须进入该组 `evidence_ids`。程序从完整明细计算信用对应的影响，不接受手填的贡献额或解释百分比。外部受阻对象不获得解释信用。

信用证据须与程序从真实总体重建的原始成员逐记录一致，覆盖每个所称对象的实际平台、SKU 和全部比较月份。结果型证明覆盖完整基期/当期；缺口型覆盖最近完整种子与所有逐月 raw/std 实际去向，查全后的缺失 cell 保留查询反证。真正迁移到新父体的原始行由机器已核验的平台+SKU关联归入旧种子，不改写源记录。支持证据可由完整成员索引批量生成 locator，不需重复查询或逐信号重新复制数据；生成定位不等于建立原因。历史背景或外部反例可另标 `historical_context / external_context / counterevidence`，但不会给当前比较的贡献抵扣。只有同父体ID、错误月份、其他平台、其他路径或单条 SKU 的真实附件均不足以抵扣整组变化。

证明新增 `investigated_product_ids / cause_credited_product_ids / metric_residual_product_ids / continuation_requirements`。结果变化另有量额的 `contribution_components`（positive / negative / net / gross / unexplained_positive / unexplained_negative）及 entered / exited / continuing 完整对象清单。重大剩余生成含原候选、精确对象、月份/比较、指标、实算残余与冻结容差的 `continuation_requirements`。`investigation-submit` 即使拒绝提前完成，也先把程序计算出的续查要求保存到中心不可修改的 `investigations/followups/…`，绑定当前合同、答卷、proof 与 task；重派会带入 `previous_followups`。原义务分母不变，得到这份清单不算结案。

## 5. 主 Agent 审核

无具体商品/月份的历史方法 guard 使用真实文档入口：`capture_frozen_source(run, "inputs/project/config.yaml")` 或确切 `inputs/history/…` 返回已读取真实文件的 evidence ID。locator 使用 `/source`、真实 `source_path / source_sha256` object_keys、`role: frozen_source`；检查文本时必须给 `text_field: text / lines / excerpt_sha256`。JSON/YAML 的 `parsed` 来自同一原文的程序解析。历史报告必须匹配该事项 `source_refs` 的原路径和哈希；程序输入与当前 manifest 可以支持方法/范围判断。此入口只适用于维护的非对象 guard 和历史来源清点，不能用来替代商品、销量或缺口的原始成员。

这些 guard 的 `requires_impact=false`，可以据实填写 `cause / impact: not_applicable`，无需虚构四项业务金额为零。独立反例仍需实际方法或来源。配置集合可使用 `set_intersection_count`：操作数 `field: parsed`，以 `value_pointer` 指定真实配置字段（例如 `/站点起始月份` 映射的键与 `/暂不验收站点` 列表），返回实际交集个数；它只证明所指配置集合关系，不证明被排除国家的数据正常。该运算不执行任意表达式。

原 3.1 `reviewer_id / submission_sha256 / obligations[] / check_reviews / entity_reviews / history_segment_reviews` 保留。每个接收的义务另填：

```json
{
  "obligation_id": "<义务ID>", "decision": "accept",
  "reason": "<原始证据、替代解释、范围、残余支持接收的具体理由>",
  "scope_check": "<实际核对范围与完整分母>",
  "conclusion_scope": "<原样复制当前义务 scope 对象>",
  "evidence_checked": ["<全部动作和证明所引用的实际主包证据ID>"],
  "material_sha256": "<由 material_digest 生成>",
  "raw_evidence_checks": ["<关键原始 locator，check_ids覆盖全部 required_checks>"],
  "recomputations": ["<下面的复算对象>"],
  "alternative_explanations": [{
    "hypothesis": "<主要替代解释>", "status": "rejected_with_evidence",
    "resolution": "<实际检验结果>", "evidence_locators": ["<实际原始定位>"]
  }],
  "remaining_uncertainty": {"description": "<剩余不确定性或有据说明无重大未决>", "acceptance_effect": "<对验收判断的影响>"}
}
```

`material_sha256 = acceptance_review.material_digest(stored['sha256'], answer, proof_or_None, run.get_evidence)`，使用中心导入后的当前答卷与证据 ID。不要使用子包 ID 计算中心审核哈希。程序在实际主审时再次执行完整人口证明计算；若有 typed proof，另填 `proof_recalculation: {result: {anchors: <原始重算结果>, computed_impact: <四指标原始重算结果>}, assessment: <数值和范围的业务核查结论>}`。

关键复算对象例式：

```json
{
  "metric": "units", "method": "sum",
  "operands": [{"locator": "<实际 locator 对象>", "field": "<实际销量字段>"}],
  "result": "<数值类型，实际重算值>",
  "target": "/proof/anchors/base/units",
  "meaning": "<为何这组原始操作数完整还原此关键分母>"
}
```

操作支持 `sum / sum_abs / max / distinct_count / difference / ratio`；difference 为第二数减第一数，ratio 为第一数除第二数，分母不得为零。每个操作数必须引用实际核对字段。`result` 必须为有限数值，既匹配实际操作数，也匹配 `target` 指向的本答卷/证明关键数值。typed proof 至少核对 units 与 amount；无 typed proof 时四个指标均须复算且 target 分别为 `/answer/impact/<metric>/exposure`。SPU 去重、价格带重算必须使用适合该定义的完整操作数，不能拿另一指标的和当结论分母。

重大事项、历史事项、关键冲突、历史漏检及机器规则履行必须有 `independent_review`：

- `reviewer_id` 必须不同于原调查者和当前主审；`material_sha256` 绑定同一当前材料；`decision: accept` 及具体 `reason`。
- `raw_evidence_checks`：实际检查的原始定位。
- `method: independent_source` 时，须有原答卷之外的真实来源证据以及 `source_independence`；新增同一来源拷贝不能在业务上冒称独立来源。
- `method: alternative_method / counterexample_recalculation` 时，须有 `method_difference` 与实际 `recomputations[]`；alternative_method 的实际计算配方必须不同于主审。反例方式还须 `counterexamples[]`，逐项含 hypothesis / result / evidence_locators，以及 `test: {recomputation_index: 0, relation: "eq", expected: <有限数值>, outcome: <真实布尔结果>}`。relation 支持 eq/ne/lt/le/gt/ge，程序对照独立复算实际值核查检验结论。
- 重大事项判正常/误报与判缺陷用同一门槛。仅另一 Agent 读同摘要同意不通过。

`decision: return` 无需伪造接受材料，但须 `return_items[]` 逐项含 `check_id / gap / required_action`，中心生成确定的 return_id。未经回应的退回项不能在下一尝试计完成。案件台账存在时，中心审核自动绑定当前 `case_id / case_version`；拆案或反例重开提升版本后旧审核失效。

## 6. 共享取证与规则履行

共享完整明细由正式 machine collection 取证，完整分页、身份、源文件哈希和成员绑定保留。它可以回答共享范围的机器事实，但不能直接替代每项比较的结论。

结果变化已有正式共享入口 `acceptance_shared_proof.verify_shared_result_proof`。主包先按正式案件成员执行 `machine-collect --spec` 与 `machine-diagnose --spec`，完成整个包原始分页、源文件哈希与成员重算。分配子包的答卷改用以下互斥的完整人口证明结构：

```json
{
  "population_proof": {
    "shared_package_id": "<主包真实 machine package ID>",
    "shared_diagnosis_sha256": "<主包 package diagnosis.json 实际内容 digest>",
    "member_id": "<本项原 obligation_id>",
    "entity_assessments": ["<本项比较全部实体的实际判断和 cause_claims>"]
  }
}
```

在子包仍运行 `investigation-proof <WORKER> --candidate-id <ID> --file <ANSWER-DRAFT.json> --output <WORKER>/proof.json`。结果证明调度器自动选择正式共享方式，程序从主包完整明细重算本成员的实际比较窗口、品牌/SPU 与价格带分母，逐对象/指标分配原因信用。主包记录引用使用 `main:ev-…`；额外子包证据用自身 `ev-…`。中心 `investigation-submit` 将二者归一化，不重发 SQL，不复制或伪造 worker 查询事件；正式证明绑定源主包、真实任务、合同、具体成员、共享包、原始页及诊断版本。审核后任一相关原始页、package/member/diagnosis 变更都会使旧审核失效。

环比与同比可引用同一个共享包，但必须分别使用原 member_id 与各自完整基期/当期、实体判断及残余；一个比较不替代另一个比较。单数父体ID不足以解释跨平台身份时保留全部相关指标残余。完整机诊的关键冲突进入 `machine_conflicts` 和答卷 `machine_conflict_responses`，不能仅因摘要未提冲突而省略；未解决冲突不能被批准。

缺口也有正式共享入口 `acceptance_shared_proof.verify_shared_gap_proof`：把上述同结构放在 `gap_proof` 下，保留本缺口原 `member_id`。程序重建缺口前最近完整种子、每个应查月和 raw/std 层的完整去向；多个连续缺口可引用同一真实共享包，各自原义务和解释仍独立存在。原 worker 内标准 `drill + brands + sku_trace` 与 `gap_proof.source_coverage_job_id` 证明仍可使用，同一份答卷不能混填两种方式。不能把任意共享包 evidence ID 冒填成 typed job ID。

正式规则从 `shadow_rule → validate_rule → apply_rule` 产生版本与成员绑定的证明后，答卷可用 `fulfillment_mode: machine_rule` 与 `rule_fulfillment: {rule_id, rule_version, proof_id, proof_sha256, case_id, obligation_id}`。`acceptance_cases.verify_rule_fulfillment` 返回精确 `checks_proven`；只有这些动作可用规则证明回应，其他 required_checks 仍按原合同调查与审核。当前 `contract_prefix_presence` 仅证明 `scope_baseline` 的观察前缀存在，不证明合同语义、全部叶子完整、合理迁移或销量真实。规则独立样本/历史反例失败会重开受影响案件；不能因机器规则消掉部分重复取证就关闭整项业务义务。

默认 `--next` 只从机器诊断后的 `needs_investigation / rule_disposition_pending_review` 路由分派。待完整明细的案件先共享取证；具体历史/专项可通过显式 IDs 做有界核对，完整合同保留且记录分派来源与原因。

新增问题使用 `investigation-add` 或 `add_obligations`。每条 issue 必填 `issue_type`、原始单条 `source_observation` locator，以及既有 `question / discovered_from / site / path / level / month / evidence_ids`；对象的 `product_ids / sku_ids / platforms` 原样保留。维护映射为 `identity / sku_identity → 身份、量价、源映射、反证、四指标五项`，显式 `semantic / classification_semantics → 身份、分类语义、源映射、反证、四指标五项`，`numerical / result_change / price_volume → 完整数值八项`，`gap / raw_presence_gap / std_presence_gap → 完整缺口六项`，不可由 caller 自选 required_checks。语义抽查漏检还携带实际 `coverage_requirement_id / coverage_observation_sha256`，精确绑定那一次原始发现。登记新义务仍需完成相应四指标影响和实质主审，不能借抽查签字消掉发现。

## 7. 保存失败、超时、容量及退回进度

`investigation-progress` 的 state 为 `failed / timeout / capacity_pending / returned`。输入为：

```json
{
  "reason": "<实际中断原因>", "resume_action": "<可以继续的具体动作>",
  "attempted_actions": ["<已做的尝试与真实结果>"],
  "remaining_work": {"<每个原义务ID>": ["<未完成动作，不能留空伪装完成>"]},
  "evidence_ids": ["<已得到的真实证据，可为空>"]
}
```

程序保存独立 checkpoint、已有工作文件哈希、原义务和尝试号；随后 `investigation-requeue` 再分派生成新尝试。运行中任务不能未保存进度就丢弃；失败、超时、容量和退回均不计已核实，也不伪装为外部缺证。

## 辅助引用与默认抽查（3.2.1）

结构化答卷递归识别单值 `evidence_id`、任意 `*_evidence_id`，以及列表 `evidence_ids`、任意 `*_evidence_ids`。例如 `calculation_evidence_id`、`appendix.support_evidence_ids` 都必须是真实 `ev-` ID，子包复用主包可加 `main:` 前缀；类型错误、空/未知 ID 拒绝。即使该辅助证据不在 checks 的主引用中，也会导入主包、统一替换 ID，纳入来源哈希、材料摘要与主审必读集合。自由文本只作为文字保留，不扫描替换其中出现的 ID。

默认样本的 required_checks 为 `sku_identity / price_volume / source_mapping / counterevidence / four_metric_impact`，不要求判断分类规则边界。无信号抽查 observation 可显式指定维护的 `issue_type`（数值、缺口或身份等），未指定按 identity 登记；分类语义专项仅用于已证显著干扰大盘的情况或用户明确指定。已登记/旧冻结义务不能靠改变展示章节消失，合同变更及重新审核仍按原版本门槛执行。
