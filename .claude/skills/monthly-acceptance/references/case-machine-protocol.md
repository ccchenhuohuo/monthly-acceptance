> 版本边界：以下为 3.x 冻结兼容资料。4.0 新运行只使用 [全景与议题权威协议](question-workflow.md)，不执行下文逐候选义务、defer 或全体市场原因解释门槛。

# 案件、共享明细、机器诊断与残余分流（3.2）

3.3 新运行的分类、适用调查集合和三种报告位置以 [业务分类协议](business-classification.md) 为准。原始义务保留；经审暂留不需逐项深查、不计实质核实。以下逐义务合同仅用于实际分派的任务，旧冻结运行维持原规则。
本层位于原始义务与调查调度之间，复用 `acceptance_core/transport/validate` 的冻结、SQL、分页、哈希和资源控制。原始义务保留不变；案件是处理和共享取证单位，不是共同根因，也不是已结案事项。

## 记录与程序入口

| 层次 | 文件/命令 | 核验含义 |
| --- | --- | --- |
| 应检查范围 | `coverage-plan.json`、`coverage-domain.json`；`coverage-plan/status` | 从冻结国家、自然月和规则生成，再展开完整观测及合同路径；不从信号台账推导必查范围 |
| 独立发现 | `coverage-independent.json`；`independent-freeze --file <DISCOVERIES> --provenance <说明>` | 在历史调查计划之前冻结，保留具体对象/月/原文事实；`--history-blind` 只能用于实际隔离历史答案的观察 |
| 历史对照 | `coverage-history-plan/comparison.json`；`history-plan`、`history-compare --file <JSON>` | 全部历史具体事项保留；独立、引导、未复现、特征映射待核、规格不全与可比性待核分别计数 |
| 无信号抽查 | `coverage-sampling-plan.json`；`sampling-plan/jobs/collect`、`sampling-review --file <JSON>`、`sampling-register-findings --coordinator <主Agent>` | 国家×月分层、类目及具体对象均固定种子；历史月份不可省略；漏检扩查和新规则测试进入队列 |
| 案件及成员 | `investigations/cases/state.json`；`case-plan/status` | 每条信号、样本、历史/来源/专项恰好映射一个处理单元，保存全部比较和实体 |
| 共享明细 | `machine-collect --spec <JSON>` | 先限范围取得完整目标记录，再由全部目标身份生成 raw/std 分片；主子包同一 SQL 容量 |
| 机器诊断 | `machine/packages/<ID>/`；`machine-diagnose --spec <JSON>` | 从原始页重建成员、分母、贡献和残余，核对独立基础汇总；Agent 阅读索引而非反复粘贴全部原始行 |
| 分流 | `case-route --coordinator <MAIN_ID>` | 消费重新验证的诊断；按原成员/范围/窗口核对，不采信传入的成功或成员哈希 |
| 规则 | `rule-shadow --coordinator <MAIN_ID>` / `rule-validate --reviewer <INDEPENDENT_ID>` / `rule-apply` / `rule-invalidate` | 影子运行、实际正反例及独立核验、逐义务机器证明、反例批量重开；业务结论仍需原调查主审 |
| 拆案/重开 | `case-split/reopen --coordinator <MAIN_ID> --case-id <ID> --file <JSON>`；`case-acknowledge-review --coordinator <MAIN_ID> --case-id <ID>` 或 `--case-ids-file <JSON数组>` | 保留变更来源/证据和新版本，旧审核失效；新逐义务实质审核后才同步恢复，ack 命令本身不批准调查 |

同一逻辑取证包可包含多个月份片、身份片和分页查询；包数、SQL 数、根因数与 Agent 数分别统计。每项原始义务必须完整进入一个逻辑取证包，包与案件的正反向映射同时核验，删掉整个包也不能保持覆盖通过。

全景检查五种执行结果：`success_clean / success_signal / missing_data / query_failed / not_executed`。不适用另带条件与理由；数据没有返回不自动表示不适用。缺页、失败、缺国家/月或未完成历史明细抽查不能宣告全景完成。

## 归并的有效边界

- 父子观察别名必须有双期唯一子路径、物理行数、SPU、量额、有效性和价格带一致的证明。同金额、同国家或相似曲线不足以合并原因。
- 同月同比和环比共享明细，保留各自自然月基期和逐义务判断。取父路径完整明细时对子路径逐项筛选并与其基础汇总对账；从子路径推父路径则须证明该比较的完整总体相等。
- 同国、同完整路径的连续缺口合案；恢复后的再缺失重分案。最近未来恢复不能替代缺口前基线。
- 明细抽查批次保留每个样本。历史具体对象、月份、异常特征、证据和影响独立保留；跨平台同 ID 不依赖类目涨跌来触发。
- 反例可按原义务拆案。只有部分义务而无法拆分的实体问题，通过新增义务承接剩余实体，不删除原调查责任。

归并示例：同一路径 1 月有观测、2–3 月无观测、4 月恢复、5 月再次无观测，形成 2–3 月和 5 月两个案件，分别以 1 月和 4 月最近完整观测为种子基线。同月环比和同比可共用一包原始明细，但 2026-07 对 2026-06、2026-07 对 2025-07 仍为两项比较，不能一并继承“已解释”。

## 共享明细与机诊

取证规格包含 `package_id / site / level / path / window_start / window_end / members / obligation_ids`。标准层 `level` 为 0–3；原始路径 `level=raw` 且保留完整六段。不能直接映射到可执行范围的合同或来源事项保留明确阻塞/补范围动作，不静默删除。优先从 `case-plan` 中的 collection package 复制，不手写缩小的成员集合。`members` 保留原始 `obligation_id / source_hash / path / comparison / base_month / current_month`；只有显式有界的技术试跑才选子集，不能据此声称整个共享包完成。

`machine-collect` 先运行 `collection_jobs` 的全部目标月份片，再由 `collection_trace_jobs` 程序计算身份组 × 月份片的完整追踪任务。当前冻结初始配置为每组 100 个身份、目标及追踪月份片各最多 6 个月；实际以本次 `machine_diagnostics` 策略为准。查询先限国家/月份/路径再聚合；分片按平台及父体/有效 SKU，逐片完整处理全部页、重复键与源行数，最后对账所有成员及月份。部分分片成功不能计为完整取证，311 个逻辑结果范围也不能写成 311 条实际 SQL。跨平台同 ID 保留为不同取证身份；正式中国 SPU 及价带仍按冻结的 product_id 口径重算，数值归并不宣称实物相同。

机诊按完整成员逐期计算：SPU/价带、量额、正负增减与抵消、进入退出/持续、商品和具名品牌贡献、无效值与单位/价格线索、父子身份及 raw/std 路径关系。原始/标准/旁支记录是不同用途的证据，不能重复相加。价格与数量守恒不证明真实交易。

缺口包必须用最近的缺口前观测月完整对象为基线，对所有中间自然月保留去向、无观测或身份不可追踪结果。机诊可确认观测事实，不把未来恢复、同名路径或经济下架推成已证原因。占位 SKU 保留本父体量额，缺身份不能标查清。

`diagnosis.json`、`index.json`、逐 `members/*.json`、`conflicts.json` 均绑定原始文件哈希。`machine-status` 会重算诊断及索引，不信任保存的 `complete`。原因提案只影响 `proposed_residual_by_member` 等提案字段，不能未经证明和主审减少业务残余。包内各比较的剩余保留在 `residual_by_member`，不为整包制造一个可相加的解释率。

## 共享明细如何进入正式调查

结果变化义务可以在 `population_proof` 选择共享证明形式：`shared_package_id` 指向主包完整机诊，`shared_diagnosis_sha256` 绑定其确定版本，`member_id` 对应本义务的精确成员，`entity_assessments` 保留完整总体的逐对象原因与反证。此形式与原 `spu_job_id / brand_job_id / sku_job_ids` 取证证明互斥；程序自动从主包原始页、机诊索引和对应比较重建正式分母，不要求每条信号再执行相同 `drill`。

该共享入口由 `acceptance_shared_proof.py` 实现。子包引用主包证据使用 `main:ev-...`，自己的新增证据使用 `ev-...`。主包来源、机诊、成员、原始义务、调查任务和比较基期都要对应；提交、主审和后续核验再次检查其版本。主包新取证或材料变更会使旧提交/审核失效，不能复制一份摘要到子包假装独立查询。

共享形式当前支持结果变化的正式 `population_proof`；路径缺口仍使用其类型对应的 `gap_proof`，其他义务按合同回答，不能混填证明字段。无论采用哪种形式，完整明细与数值守恒都不赋予原因解释量；只有精确对象、指标与原始机制证据经过适用核验，才可得到相应贡献抵扣。未调查对象、占位身份、冲突和外部缺证继续保留。单个比较通过不代替同包其他比较，更不代表整个案件业务结案。

`investigation-assign --next --coordinator <MAIN_ID> --agent <WORKER_ID>` 根据当前可执行路由分派；有界历史调查或机制试跑使用 `--ids-file <IDS> --reason <具体调查目标及边界>`，保留完整原始合同，不把选择的子集写成全部完成。按问题类型复用机证与分派剩余问题，而不是按每条信号机械重复相同深查动作。

## 规则处置及反例

首版内置 `contract_prefix_presence` 只证明 `scope_baseline` 中有源记录的候选前缀存在，不能证明合同语义等价、所有叶节点到货或全部月份业务可用。日本合同半角 `&` 与原始全角 `＆`，以及中国四级合同与三级原始路径的 9 条候选，在冻结探索中覆盖 31 个月；它们只是候选路径存在的证据，不自动规范化合同、删除中间层或业务结案。共享正式总体证明与此规则证明是两个入口，不能把机诊完成或 `population_proof` 的存在当作规则已经得到独立验证。

每条规则有版本、适用成员、所需证据、可证明的检查、反例/不适用条件、剩余和重开条件。主 Agent 用 `rule-shadow --coordinator` 留影子结果，独立复核者用 `rule-validate --reviewer` 提交实际复核；复核者必须不同于规则作者和主审角色。控制样本绑定真实独立 `worker_task_id`、其负责的具体义务与冻结完整原始路径查询 `job_id`，同时覆盖历史反例和实际例外；模型填写 `explained=true`、改一个状态或重读摘要不能批准。复核之后仍由主 Agent `rule-apply --coordinator` 正式应用明确的机器证明。正式应用生成逐义务 `proof_id`，`verify_rule_fulfillment` 只承认其 `checks_proven`；其余检查仍需调查。

首版控制机制限定为 `prefix_present_named_leaf_absent`：规则前缀在受控月份存在，但该前缀下一个此前确有观测的具名叶路径缺失，从而检验“前缀存在不代表叶路径完整”的实际边界。每个控制保留 `kind / obligation_id / worker_task_id / job_id / control_path / months / baseline_month / mechanism / expected_presence`；`control_path` 必须等于原义务的完整六段 raw 路径，国家必须等于规则国家，`months` 必须覆盖原义务全部月份并位于影子窗口中，`baseline_month` 必须是该具名叶路径最近真实缺口前观测。程序逐月要求前缀有记录且叶路径无记录，`expected_presence` 为 `false`。历史反例与实际例外须使用不同具体义务，不能用任意国家的无关历史对象或虚构路径的缺失代替。

控制查询在验证、正式应用及后续核验时都经 `verify_job` 完整重验，包括实际数据库 SQL、请求 SQL、全分页、源总行数和键；只对上 `job_id`、SQL 标签或布尔结果不足以通过。尚无可维护谓词的合同语义等价、类目中间层删除等例外保持影子状态，不用文字说明将其升级为正式规则。

正式应用只产生“规则证明待原义务主审”，未覆盖检查和业务判断仍要完成；`rule-validate` 和 `rule-apply` 均不是业务批准。控制样本或证据版本改变会使原规则验证失效。规则抽检失败或新反例使受影响案件及审核重开。严重缺陷已经有充分证据时进入返修及影响复验，保留尚未解释的其他异常，不重复调查同一已确定事实。

## 残余、分流与完成

`case_workflow.collection_priority_threshold` 的初始值为 0.02，只控制取证优先顺序；允许范围是大于 0 且不超过 1 的有限数值，非数字或越界配置直接拒绝。旧键 `collection_ordering_threshold` 只在主键缺席时兼容，主键优先。2% 和 5% 都不是不查线。不能将观察变化强度当作错误概率、允许误差或未知损失上界。所有低影响项先完成机器检查；缺数据、身份不明或无法证明上界时保持风险不可界定，不能低影响豁免。

同国/完整类目/月累计时，对原对象去重，量额正负分列；MoM/YoY 保留独立分母；SPU/价带重新计算。部分子类目不能凭相加凑出 L1 分母，跨国金额不汇总。完整累计结果及具体义务/案件去重映射写入案件状态，审核时重新验证机诊并复算。观察强度仅排序，`requires_cumulative_review` 或风险不可界定仍须复查/补证，不能凭累计净额为零放行。未知残余不等于 0；量化的观察暴露也不是已确认损失或误差上界。

分流为待取证、具体调查、规则证明待主审、经界定的低影响未核、实际外部缺证等；队列清空不是放行。预算耗尽须 `case-route --budget-exhausted --reason <具体容量原因>` 或 `investigation-progress` 保存未做工作，不能伪装成外部凭证不可取得。`sampling-collect --limit N` 也只限制本次批次，未执行任务仍保留。

3.2 初始调查残余容差为 SPU/量额观察残余比例 1%、价格带残余 0.5 个百分点，依据与范围记录在冻结 `materiality` 和 `investigation_workflow`。它们避免要求不合理的零残余，尚未校准为业务误差许可；缺数据、身份不明、关键冲突和历史具体义务不豁免。非零剩余还需完整对象及四指标的可复算风险边界和明确复查条件，不能仅因观察比例低就停止。

最终门槛由 `validate` 和 `seal` 独立重建。报告分别列扫描、映射、机诊、实质调查、主审、原因解释与历史回归。`verified` 为适用调查已核实，仍不自动说明正常；`reviewed_residual` 为适用调查已履行且非零剩余已独立复核/主审，不属于全部原因解释或无保留资格；`blocked_external` 是已主审的具体凭证阻塞，不计实质调查完成。前两项可按程序口径计入调查动作完成数，三类结果必须分列。`low_impact_unverified` 路由也不能替代任何主审结果。外部阻塞、重大未知或关键冲突保留阶段状态；放行由独立使用门槛判断。

主审绑定每个原始义务的提交材料、原始证据、计算和当前 `case_id / case_version`。拆案或重开后先重新提交并逐义务实质审核，再执行 `case-acknowledge-review` 同步当前审核结果；该命令不能替主 Agent 批准，也不能把有残余或受阻状态改成已全部解释。

## 归档重放和成本

`archive-replay --source <SEALED_RUN>` 只复制与新冻结计划 SQL/分页完全一致的任务，保留原数据时间、来源封存和证据 ID 映射。新旧查询不同的任务保持未执行，必须另查；不自动推定查询语义相同。重放旧查询不算实际数据库调用。

实际查询尝试、成功/错误/取消、SQL 排队、耗时和调查任务事件由程序统计。CLI 未公开的模型 token/计费记录为未知，不能由 Agent 数或案件压缩率推算工时和成本。真实运行报告必须同时披露数据快照/字段变化、尚未完成的业务核验及恢复条件。

## 批量同步与不可变版本（3.2.1）

多个案件的全部原始义务完成当前版本主审后，将 case IDs 写成 JSON 数组，执行 `case-acknowledge-review <RUN> --coordinator <MAIN_ID> --case-ids-file <文件>`。整批先校验当前提交、主审、证据、计算与案件版本，再统一保存；任何未审、过期或不匹配项导致整批拒绝。单案 `--case-id` 保持可用。批量命令只是同步实际审核，不生成审批，也不删减义务。

一次操作内共享任务与义务读取；材料在结束前重验，下一次调用重新读取。案件版本改为不可变分块清单，相同内容共用哈希块；当前 `state.json` 仍保留完整可读 schema。旧的整份快照可读，新版本的清单、块、当前文件和事件任何不一致都会失败。当前文件仍需完整重写，不能宣称成本已经与全量台账无关。

未封存旧运行中的已成功观测可通过 `evidence-reuse <NEW_RUN> --source <SOURCE_RUN> --job-ids-file <JSON数组>` 复用；目标必须新冻结，程序从目标计划重建基础/结束指纹任务并核 SQL、范围、键和全页，未知任务 ID 拒绝。底层函数为 `acceptance_replay.import_planned_jobs`；不能把修改过 SQL 的手写任务冒充同任务复用。保留来源时间、固定事件前缀、源文件哈希与目标导入事件；复用次数不计新查询。来源已封存的整批基线重放继续使用 `archive-replay`。

`finish-scan` 或强制重跑同一任务时，旧导入收据和每次执行保留在 `records/job-versions/<job-id>/`，当前收据指向真实的新执行。程序逐次核验计划、前版本、查询事件及成功全页；失败/取消不获得成功状态，但可据原链续跑。新的结束指纹仍须晚于补查并与起始指纹按原门槛核对。该机制不能修复已损坏的旧运行，也不允许在冻结包中换代码；代码、范围或政策改变须建立新运行。
