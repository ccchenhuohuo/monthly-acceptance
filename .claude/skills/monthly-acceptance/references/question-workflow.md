# 全景、议题与审核协议（0.0.1）

## 权威对象与版本

当前发布、运行、政策、协议、台账与抽样数据格式版本统一为 `0.0.1`；使用显式 `architecture="panorama_questions"` 识别当前全景与议题架构。旧版本号仅用于旧冻结运行的兼容分支，不作为当前架构的版本门槛。

- `coverage-plan.json`：从冻结配置/预期平台/完整查询生成的检查分母。`panorama/index.json` 绑定完整查询及全部派生事实，具体约束具有独立 passed/failed/unavailable/not_applicable 状态。
- `panorama/facts-*.json`：连续的全部标准路径×自然月，含无观测，保留价格带、SPU/品牌进入退出和正负贡献。raw-paths、platforms、country-layers 保留完整上下文及原始行定位。读取 JSON 的全量路径/月份分区，不只看排序结果。
- `panorama/readings`：实际阅读者的判断、读法、未读范围、独立性和原始产物绑定。生成文件或统计行数本身不算理解。
- `investigations/cases/state.json`：唯一业务议题主台账，沿用不可变版本和分块存储。一个 case 有多个 scope_units。没有并行的 question/ticket 主台账。
- `investigations/plan.json` 冻结协调身份与资源政策。调查义务动态重建自现行议题，每个范围为 `question_scope:<case_id>:<scope_unit_id>`，不从 candidates/samples 反推。
- 分派、子包、progress、submission、review、证据导入和退回继续使用既有引擎。已分派合同哈希绑定问题、版本、范围、检查、反向分量及停止条件。

改造不删除旧证据。所有旧运行（包括历史 3.x 和 4.x）继续使用自己的冻结版本。当前全景与议题架构明确拒绝历史 3.x 的逐候选 classify/defer、机诊采集/诊断、旧 case-queue、单义务 amend/add、原候选 proof 等入口，防止绕回旧调查分母；已有普通观察可作回放比较，不进入新队列。

正式提交、主审和 audit 在各自操作内复用已校验的证据页面，绑定真实来源、元数据、原始字节、任务、计划和事件版本；落盘或返回前重新检查全部已读依赖及缓存内容。来源或缓存变化使本次核验失败，下一次主审/audit 建立新快照。完整操作数和分页仍逐项核对；程序复算不替代 Agent 独立判读。报告分别显示已主审、已核实、外部受阻范围数。

当前全景与议题架构中，全景 SPU 按国家/平台/父体组合键计数、分带和追踪进退，基础样本保留 platform；品牌仍按同国品牌聚合。历史冻结 SQL 不回写，新查询仅在新运行生效。新运行不能通过 override 回退旧分母；历史回归通过须有各层精确历史对象和新月证据，全景阅读须绑定非空原始判读产物。旧原因残余容差不再作为当前架构的启动条件。

## 登记

先 `analyze` 生成全景，再 `investigation-plan --coordinator <实际主Agent ID>`。`question-register RUN --actor MAIN --file topic.json` 接受一个议题或议题数组：

```json
{
  "business_question":"某事件是否造成采集范围、身份或量额的不可比？",
  "priority":"normal",
  "report_placement":"main",
  "reason":"完整轨迹形成的具体质量疑问及宏观关注依据。",
  "next_check":"先做能够区分解释的完整聚合。",
  "stop_condition":"质量问题有证据答案，适用影响清楚，重大反证已处理。",
  "constraint_ids":[],
  "origin_refs":[{"job_id":"实际任务ID","row_index":0,"record_sha256":"实际原始行哈希"}],
  "scope_units":[{
    "scope_unit_id":"country-event",
    "site":"DE",
    "platforms":["Amazon"],
    "source_layers":["raw","std"],
    "path_layer":"std",
    "paths":[[]],
    "event_months":["2026-07"],
    "background_months":["2026-06"],
    "stage":"aggregate_first",
    "required_checks":["scope","identity","conservation","counterevidence","impact"],
    "required_components":[{"component_id":"opposite","question":"必须单独回答的反向子树或恢复对象。"}],
    "scope_rationale":"这些月份和完整子树组成一个待核事件。",
    "next_check":"具体区分解释的证据动作。",
    "stop_condition":"这个国家和反向分量均有答案。"
  }]
}
```

`paths:[[]]` 表示全站完整总体，其他数组是完整子树前缀。std 路径最长三级，raw 最长六级；一个 raw 路径选择不能直接假装覆盖 std。跨 raw/std 的标准子树定向查询通过完整平台/父体/SKU 原键 EXISTS 选取 raw 源记录，不把多对多 join 放大隐藏在 DISTINCT 后。

必选 scope/counterevidence/impact；其余从 completeness/duplication/identity/conservation/time_series/price_bands/historical_regression 选择，并必须包含起因强约束对应主线。反向分量和整个事件保留，不按单月、同比/环比、指标再拆义务。背景月份是已经明确要使用的证据范围，不把已读的 31 月都机械要求再次查询为明细。

版本修改提供 `case_id` 与 `previous_case_version`，保留 reason。新增范围可以增加单位；原范围不能删除国家/平台/月/层/路径/必要检查或弱化反向分量。活跃任务先保存进度并重排。需要排除原先范围时，先为其形成有证据的独立处置，不能用改标题或循环合并抹掉责任。共享议题不声称共同根因。

## 判读与历史冻结

`panorama-read RUN --actor READER --file reading.json` 字段：binding_sha256、partition_ids、status（complete/partial）、judgment、read_method、unread_scope（完整时为 none）、independence_disclosure、source_refs、reader_artifacts（path/sha256）。实际使用分区完整轨迹和原始文件；机器不能验证读者是否理解。

`independent-freeze --file discovery.json` 至少提供 independence_disclosure。确未读取历史答案时才使用 `--history-blind`。随后 history-plan/sampling-plan；晚揭露的真实缺陷回归可增加议题，但必须把独立发现与历史引导分开计数。

## 定向查询与深查

```text
question-jobs RUN --obligation-id OID --mode aggregate --output jobs.json
question-collect RUN --jobs-file jobs.json
question-jobs RUN --obligation-id OID --mode cohorts --output cohorts.json
question-collect RUN --jobs-file cohorts.json
```

aggregate 按自然月保存，可在重叠议题间按相同 SQL 复用；cohorts 是完整国家/平台/父体的多月存在组合及各月量额。只有需要时使用 details；它保留源行数、原身份、路径、量价和标题变体计数。details 不自动建立逐 SKU 原因义务。所有查询均经程序再生成、分页、唯一键和真实执行版本核对。

raw 的同月 std 子树 EXISTS 只证明已承接集合，不能证明退出对象在全 raw 中消失。需要追查跨路径/跨父体去向时，用 `--mode lineage --anchor-month YYYY-MM --anchor-paths-file paths.json` 固定完整锚定对象，再查询问题的全站 raw/std 身份轨迹；多个锚定月重复参数。有效 SKU 可以追踪换父体，占位 SKU 只按本父体原键；平台始终独立。该结果是身份补证，不能替代原子树同口径守恒分母。

长窗初查需要分阶段时，重复 `--query-month YYYY-MM` 选择当前取证月。完整业务合同仍保留，未覆盖月份不能获得完成信用。全程用真实原生执行记录处理超时和重试。多个进程可共享查询容量，事件日志按文件锁串行追加；发生旧包事件链失效时保留失败原貌，以修复后的新冻结运行复验，不回写或重造旧事件。

未回答的质量疑问必须续查；普通市场涨跌没有业务原因不构成强制续查。旧 max_unexplained_ratio/cause_credit/cohort.not_assessed 不是新议题通用完成门槛。声称已验证的缺陷对象与错误影响仍须完整证据，不能把未查对象填成 verified。

## Worker 答卷与主审

必须实际启动 Worker，再用其真实 ID 分派。子包 `brief.json` 给完整问题、scope_unit 和 obligation。使用 `main:ev-...` 引父包已有证据；新查询可在子包执行，沿既有共享容量及证据导入机制。Worker 写 submission.json，主 Agent 执行 investigation-submit；该动作不批准调查。

每个答案至少包含：

- obligation_id、与合同完全相同的 scope，business_verdict、repair_state、defect_confirmed、status_dimensions。
- quality_result：ruled_out / confirmed_defect / blocked_external。未回答完的质量问题保存 progress，不作为完成答卷。
- checks：逐 required_checks 的 status、finding、counterevidence、actions、evidence_ids、raw_locators。not_applicable 必须具体有据；scope/counterevidence/impact 不可豁免。
- proof_jobs：每页的 evidence_id、job_id、record_sha256。程序核对实际来源、完整分页、SQL、国家/平台/层/月/路径覆盖。deep_review 的事件必须有完整 cohort 或明细，只有旧触发聚合不能完成。
- raw_locator：evidence_id、pointer（如 /response/data/0）、record_sha256、fields、object_keys、purpose。须对回实际原始行；不是把自己写的摘要当查询。
- component_responses：每个 required_component 的精确回应、raw_locators 和非空 calculation_ids；主审须核对这些计算是否实际回答该分量。
- calculations：每项 calculation_id、method、operands（locator 与 field）、result、meaning。sum/sum_abs/max/difference/ratio/distinct_count 等由程序重算；未知分母不写 0%/100%。
- metric_impacts：spus/bands/units/amount 分别给 state、basis。合同 impact_metrics 中的适用指标用 measured + calculation_ids（对应 calculation.metric 必须相同），或确有受阻的 unknown + missing；其余明确 not_applicable 的边界。默认适用 SPU/销量/金额，价格疑问增加 bands。
- quality_impact：observation_change、proven_error、unresolved_quality_risk，分别说明 finding、scope、calculation_ids。错误不成立不能把市场变化当错误金额；普通变化不能充当未解决质量风险。
- conclusion、next_action、stop_rationale：针对原质量疑问，不以耗时/预算/数量目标为停止依据。

blocked_external 的每个受阻动作必须指明 type、required_record、provider_role、attempted_access、why_unavailable、decision_impact、resume_action、实际 attempt_evidence_ids。运行失败和容量不足不属于外部缺证。

主审逐 obligation 绑定 submission_sha256、case_id/case_version、conclusion_scope、material_sha256。逐 check_reviews 和 evidence_checked；raw_evidence_checks 标注 check_ids，recomputations 重算全部关键值，component_reviews 覆盖全部反向分量，alternatives_assessment 与 remaining_uncertainty 必须具体。原始查询或材料变更需重审。高优先级的正常判断、历史回归或冲突还需真实第三人 independent_review，绑定合同/答卷、原始定位和结论。

程序可校验材料、算术、覆盖与职责，不能靠字段证明因果或 Agent 的业务理解。实际主审必须阅读质量问题与反证，不能填模板代签。

## 基础检查与完成门槛

baseline-review 使用既有覆盖来源，明确 check_id、不同 investigator_id/reviewer_id、status、finding/action/counterevidence、evidence_ids/raw_locators。程序绑定全部引用材料的哈希，审后重验。

- 抽查从全体适用 L3 单元按原种子和国家×月分层。确定样本必须全部核对原始复合键；失败扩展同国检查总体，并绑定覆盖实际范围的议题，不生成全体 SKU 深查义务。
- 历史来源逐段清点；必要回归核具体原始对象、原月份和新月份、raw/std。历史 `passed` 需分别提供 `raw_object_locators / std_object_locators` 和 `raw_proof_jobs / std_proof_jobs`；后两者逐页使用 `evidence_id / job_id / record_sha256` 绑定原生执行、不可变计划与完整分页。对象定位必须属于对应层的已验证查询。单值、多值 ID 或 `sku_keys` 均须还原国家/平台/父体/必要 SKU；历史时期或复合身份不明确时保留 unavailable。自报 verified_object_ids、附件自报 SQL 或只修展示不授予回归信用。纯语义事项以有出处的辅助处置保留，不设语义正确性测试。
- 内部对账需要真实冻结内部数据及双方复算。发布元数据不能替代实际对象量额承接。缺输入如实 unavailable；不能把缺证写成不适用。
- 明确失败有实际范围归属；如果已完成证据调查并打回，检查失败可有完成的处置，业务仍 reject。未核或受阻不记 verified。

validate / seal 重建：冻结输入与计划、全查询完整性、所有约束及失败归属、实际阅读、样本/历史/内部/发布检查、有效问题主审、复用来源、原始证据版本、末次指纹以及父/子包之后无新查询、报告对所有议题的承接。

零议题允许，但这些门槛仍必须完成。业务报告分列查询/事实/判读/议题/独立范围/任务/有效主审/待查与成本，不把普通观察显示为待调查债务，也不声称未立案就已证明正常。
