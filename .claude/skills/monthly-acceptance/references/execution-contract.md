> 版本边界：以下为 3.x 冻结兼容资料。4.0 新运行只使用 [全景与议题权威协议](question-workflow.md)，不执行下文逐候选义务、defer 或全体市场原因解释门槛。

# 执行契约 3.3.0

3.3 新运行的分类、适用调查集合和三种报告位置以 [业务分类协议](business-classification.md) 为准。原始义务保留；经审暂留不需逐项深查、不计实质核实。以下逐义务合同仅用于实际分派的任务，旧冻结运行维持原规则。
本契约适用于 3.3.0 新运行；旧冻结包按各自原契约读取。新增案件/机诊和必查流程见 [case-machine-protocol.md](case-machine-protocol.md)，实质审核字段见 [review-v32-schema.md](review-v32-schema.md)。**固定扫描 → 全量义务 → 独立取证 → 主 Agent 逐项审核与返工 → 业务判断 → 最终封存。** 候选登记、子 Agent 提交、主审确认外部阻塞都不等于实质核实。业务调查详见 [investigation-protocol.md](investigation-protocol.md)，计算与判断边界见 [methodology.md](methodology.md)。

## 阶段与完成条件

| 阶段 | 必须留下的记录 | 完成条件 |
| --- | --- | --- |
| 冻结 | `run.json / inputs / plan.json` | 用户范围内所有国家、历史、四层、MoM/YoY、价格带与政策可复核；历史来源已冻结，旧判断暂不载入 |
| 全景扫描 | 每个任务的实际 SQL、全部响应页及开始/结束事件 | 所有必跑任务成功；失败必须恢复，不能从分母删除 |
| 独立观察 | `checks / candidates / cases / samples.json` 与冻结事件 | 自然月比较、贡献守恒和全体候选程序生成；先冻结自主发现，再揭示历史提示 |
| 全量调查计划 | `investigations/plan.json`、追加义务与任务事件 | 全部候选、样本、历史来源/事项、范围、本品、具名品牌、发布及跨平台专项都有唯一义务；合并取证不减少义务 |
| 独立调查 | `workers/<TASK>/brief.json`、查询/计算证据、`submission.json` | 每个义务逐项回答 `required_checks`；结果变化与路径缺口有完整总体证明及逐实体核验，正式提交要求未调查实体为零，不能只交摘要或局部样本 |
| 主 Agent 审核 | `submissions/<TASK>.json / reviews/<TASK>.json` | 逐义务、逐检查核对真实证据、对象、分母、反证、四项影响与残余，绑定答卷哈希；不合格退回重派 |
| 阶段存档 | `checkpoints/`、当前报告与看板 | 保留未完成、返工或确有外部阻塞的真实进度；运行继续，不作最终交付 |
| 最终核验 | 结束指纹、`validation.json`、报告与看板 | 最后一次主包或子包数据查询之后重取指纹；本轮适用义务实质调查完成，暂留分流审核有效并完整记录，无未处理的结构错误 |
| 最终封存 | `sealed.json` | 独立重验上述门槛后才允许 `seal`；已证缺陷可形成打回结论，调查完成仍不代表数据修复或业务放行 |

基础任务固定覆盖原始/标准指纹、原始完整路径逐月分布、完整键双向对账、国家与标准 L1/L2/L3 四项结果、固定价格带、SPU/品牌正负变动与进出，以及当前各 L3 的固定明细样本。基础品牌变动汇总只是触发线索；调查必须取得具体品牌名称及其完整贡献。

抽样标题依次使用 `product_title / product_title_cn / sku_title` 的非空值，原始类目保留完整路径及路径数。聚合标题、链接和路径只是入口；多标题、多路径或父子归属变化必须展开原始 SKU，不能用 `MAX` 代替全部子体核验。固定样本用于身份、量价与映射结构抽查，不能替代全盘完整性、历史问题或变化原因调查；分类规则语义不属于默认样本必查项。

标准层级相互重叠，不跨层累加；上级 SPU 重新去重。价格带按 SPU 销售额/销量计算，只有正值且细行有效时进入有效带，其余保留在 `-1` 未定价组。相邻观测不一定是相邻自然月，单边缺失只表示观测进出，不代表真实销量为零。

## 开工、查询与恢复

首次从安装目录运行 `start`，随后使用运行包冻结的脚本；方法、查询或政策变化须建立新运行，必要时用 `--related-run` 关联旧包。

```sh
python3 .claude/skills/monthly-acceptance/scripts/acceptance_run.py start --month 2026-07 --base 验收
python3 <RUN>/inputs/skill/scripts/acceptance_run.py scan <RUN>
python3 <RUN>/inputs/skill/scripts/acceptance_run.py analyze <RUN>
python3 <RUN>/inputs/skill/scripts/acceptance_run.py investigation-plan <RUN> --coordinator <MAIN_ID>
```

`scan` 自动跳过已有且完整、哈希有效的任务。政策规定的重试仍失败时保留失败并继续其他任务，该次扫描命令最终返回非零；`drill / diagnose` 失败同样返回非零，不能把 CLI 退出或文件生成当成查询成功。修复原因后重跑。禁止在原包改变范围、政策或 SQL 绕开失败；限流、耗时或上下文不足不能构成放弃调查的理由。

主 Agent 实际创建/唤醒子 Agent 后，以真实身份分派任务；CLI 只登记任务，不会自行启动 Agent。调查者与主审核者必须不同，不能更换一个字符串模拟独立审核。

| 主 Agent 操作 | 冻结脚本后的命令参数 | 作用 |
| --- | --- | --- |
| 按义务分派 | `investigation-assign <RUN> --coordinator <MAIN_ID> --agent <SUB_ID> --ids-file <IDS.json>` | IDs 文件为完整义务 ID 数组；返回任务、尝试和独立子包目录 |
| 从队列取下一批 | 同上，以 `--next` 代替 `--ids-file` | 优先级只决定顺序；调度批次不代表同因或同范围 |
| 追加发现 | `investigation-add <RUN> --coordinator <MAIN_ID> --file <ISSUES.json>` | 记录问题、发现来源、精确范围和证据，追加而不改写原分母 |
| 导入交付 | `investigation-submit <RUN> --coordinator <MAIN_ID> --task-id <TASK>` | 读取子包 `submission.json`，核对全部义务、输入/事件/证据及哈希 |
| 逐项主审 | `investigation-review <RUN> --coordinator <MAIN_ID> --task-id <TASK> --file <REVIEW.json>` | 每个义务、每项检查接受或退回；绑定本次答卷哈希 |
| 返工与恢复 | `investigation-requeue <RUN> --coordinator <MAIN_ID> --task-id <TASK> --reason <具体原因>` | 原记录保留，再分派建立新任务和尝试；禁止修改已提交证据沿用旧主审 |
| 全量对账 | `investigation-status <RUN>` | 显示全部义务、各状态、主审与核实进度；有错误时不能认作零义务 |

全部中心变更由主 Agent 顺序执行，子 Agent 只写自己的 `<WORKER>`。每个子包是有冻结输入和独立事件链的工作目录，可使用其中的同版脚本取证：

```sh
python3 <WORKER>/inputs/skill/scripts/acceptance_run.py drill <WORKER> --candidate-id <ID>
python3 <WORKER>/inputs/skill/scripts/acceptance_run.py diagnose <WORKER> --spec <SPEC.json>
python3 <WORKER>/inputs/skill/scripts/acceptance_run.py query <WORKER> --sql-file <SQL> --label <具体问题>
python3 <WORKER>/inputs/skill/scripts/acceptance_run.py investigation-proof <WORKER> --candidate-id <ID> --file <ANSWER-DRAFT.json> --output <WORKER>/proof.json
```

`drill` 取得候选精确单元的完整双期 SPU 分解，但不会自动判定原因。`diagnose` 使用完整分页，支持 `brands / sku_trace / source_coverage / cross_platform`；规格含 `kind / site / level / path / base_month / current_month`，可选 `product_ids` 是明确分片，不是完整类目证明。用于路径缺口证明的 `source_coverage` 必须不加商品筛选，覆盖完整基期种子。原始路径采用 `level: raw` 并保留六个组件及空值；原始、标准、目标和其他路径结果须区分，不能重复累加为业务影响。

`query` 是自由取证，不自动增加全景覆盖率，也不保证全量分页。需要完整总体时使用可复核的完整分页任务；若发生截断、缺页或空结果，先核实原因，不以返回文件代替覆盖。历史身份、范围去向和发布数据的自定义补查仍应保留精确范围和完整响应。

外部网页、内部文件或其他工具的真实响应通过 `attach <WORKER> --file <FILE> --kind external-source --source <原始来源>` 纳入证据。记录真实工具名、请求/URL、访问时间、返回内容及失败；人工分析另作计算/判断证据，不伪装成工具响应。没有相应付费或外发授权时继续现有权限下的工作，不擅自调用或发送。

本地运行要求 Python 3.11+、PyYAML、MCP Python SDK 和可用的 Doris MCP stdio 服务。默认读取 `~/.codex/config.toml` 的 `mcp_servers.doris`；查询命令可用 `--mcp-config` 指定同结构配置。只用已有凭据，配置值不进入运行包。只读 SQL 检查是防误操作约束，账户本身也应配置适当只读权限；没有接入不能伪造执行完成。

## 输入与冻结政策

- `验收/config.yaml / 项目范围.md` 确定已有项目范围。每周可以重验同一月推送，运行频率与数据粒度分开；最新应验收月由调用者明确指定，不把未到货当月默认为必须存在。
- `policies/default.json` 与显式的 `验收/policy-overrides.json` 在开工冻结。召回阈值不是判错标准；`investigation_workflow` 的并发数及每次任务容量只调度资源，不能减少义务。
- 默认最多 8 个调查 Agent、每次最多 8 项义务；`investigation_workflow.max_parallel_queries = 1`（允许冻结为 1–8）单独限制主包及所有子包同时执行的数据库请求。所有 `scan / finish-scan / drill / diagnose / query` 及其重试共用查询容量，等待时间与数据库实际执行耗时分别记录；成功、失败和取消都释放槽位。容量文件仅用于进程协调，不构成子 Agent 修改主业务台账的授权；其他运行和数据库外部负载不受此限制。
- 3.2 试运行默认 `max_unexplained_ratio = 0.01`、`max_unexplained_band_pp = 0.5`，依据冻结于 `materiality.basis`。它们约束已分派调查的剩余，不是允许数据错误率，也不作为 3.3 分类器的自动暂留阈值；关键冲突、历史具体义务、缺数据或无法界定风险不能豁免。3.3 的经审暂留按业务分类协议处理；其余适用义务须调查，不能因低金额或普通优先级忽略未解释部分。不得为过关临时放宽门槛。改政策必须新运行并明确业务含义。
- `open-issues.json / legacy-issues.json` 为结构化问题数组，至少含唯一 `issue_id / site / path / question`，并保留状态与已证/待证边界。`path` 为完整路径的 JSON 数组字符串，月份和比较关系应明确。关闭的缺陷及重要合理反例仍须复验，不能从历史分母自动删除。
- `runs/*/*/报告.md` 中全部月份的旧运行报告及 `验收/案例库.md` 冻结到 `inputs/history`，由 `history_sources` 记录来源；换验收月份不能遗失旧经验，跨月来源都进入清点义务，但不自动视为本轮同范围缺陷。每个来源的非空原文按稳定段落切分，`source_material.segments` 保存 `segment_id / line_start / line_end / sha256`；每段都要映射具体事项或说明有据不适用。独立观察冻结后提取并核验，不能只读若干段，也不能因没有结构化清单跳过已有报告。自主发现与历史提示找回分别记录。
- `验收/内部数据/YYYY-MM/` 存在时冻结到 `inputs/internal`。先对齐时间、币种、退货、平台、品牌、SKU 和售卖单位；缺文件仍保留内部对拍义务及具体外部阻塞，不能写成通过。
- `scope-contract.json` 维护来源、节点 ID/完整路径、有效起止月和完整清单声明。不能从已观测路径反推全部应采节点；年份、定义或完整叶节点缺失时保留具体缺口，不能以同名叶子或当前节点存在代替历史契约。

## 调查答卷与主审记录

调查计划、任务、答卷和主审各自留存，不把大答卷复制到每条候选中。来源通过义务 ID 和证据映射串联；看板提供全量搜索分页，50 条展示页不是调查截断。

子包 `brief.json` 给出本次 `task / obligations / source_candidates / source_samples`。每个义务有 `obligation_id / source_type / source_id / scope / required_checks / requires_impact / source_evidence_ids`。`scope` 原样复制，不能替换国家、路径、层级、月份或比较关系。所有检查与证据规则见 [任务简报](../assets/investigation-brief.md)。

子包 `submission.json` 顶层为 `task_id / attempt / agent_id / answers[]`。每项必须对应恰好一个已分派义务，回答全部 `required_checks`，包含 `conclusion / next_action / business_verdict / repair_state`，有明确缺陷时记录 `defect_confirmed`。子 Agent 不能写主审核字段。

| 答卷部分 | 验收要求 |
| --- | --- |
| `checks[name]` | `status` 仅为 `verified / not_applicable / blocked_external`，同时有具体 `finding / actions[] / evidence_ids[] / counterevidence`；不接受“已登记”“时间用完”或泛泛待核作为完成 |
| 不适用 | 有 `not_applicable_reason` 及证据；`scope_baseline / four_metric_impact / source_inventory / issue_extraction` 不可豁免 |
| 外部阻塞 | `blocking` 明确类型、所缺记录、提供方角色、实际尝试、不可获得原因、判断影响、恢复动作与受阻检查；其他内部可做工作先完成，不能全挂阻塞 |
| 四项影响 | 需要时填 `impact.spus / bands / units / amount`；每项有 `state / basis / unit / evidence_ids`；已测量另有非负有限的 `exposure / unexplained`，未知另有 `missing` 并对应真实受阻 |
| 结果变化总体证明 | `result_change` 必须提供 `population_proof`：完整双期 SPU 与具名品牌总体守恒、目标对象的精确 SKU 轨迹，以及逐实体原因/反证核验。只放一组证据 ID、少数头部或一个语义错分样本不满足要求 |
| 路径缺口总体证明 | `raw_presence_gap / std_presence_gap` 必须提供 `gap_proof`：缺口前最近观测月完整种子、至包末每个自然月 raw/std 去向及逐实体原因/反证，四项残余按完整基期暴露机算 |
| 历史来源 | `history_coverage` 完整重复每个冻结段的 `segment_id / line_start / line_end / sha256`，并填 `obligation_ids / reason` 或有据 `not_applicable_reason`；新提取问题先追加到台账，不能只写自由 `locator` 或文件已读 |
| 使用与修复 | `business_verdict` 为 `pass / qualified / reject / undetermined`；`repair_state` 为 `not_needed / pending / retested`；确认缺陷或存在阻塞不能直接 `pass` |

`population_proof` 的总体是触发单元的精确双期范围，不能预先过滤疑似对象再计算分母。全部实体接受对应核验，正向和负向贡献均保留；销量头部和旧头部不能因金额小或当前消失而丢失。SKU 轨迹要对应实际售卖对象、规格、父子身份、时间和类目；同一个 ID 或一条聚合标题不是身份已核实的充分证据。逐实体原因判断须引用具体支持与反证，证明已解释及未解释集合的完整归属，不能批量复制同一句原因。总体守恒证明覆盖，仍不能自动证明真实销量或市场原因。

### 结果变化的 `population_proof`

每条 `result_change` 答项中的 `population_proof` 采用以下结构。示例仅说明字段，须换成真实任务、对象和证据，不能原样提交：

```json
{
  "spu_job_id": "drill-<实际candidate_id>",
  "brand_job_id": "<本单元完整brands任务ID>",
  "sku_job_ids": ["<本单元完整sku_trace任务ID或覆盖分片任务ID>"],
  "entity_assessments": [
    {
      "product_ids": ["<实际SPU ID>"],
      "status": "verified",
      "reason": "该对象的原因或缺陷判断及其具体证据",
      "counterevidence": "已经核查的竞争解释与证据边界",
      "evidence_ids": ["ev-<真实证据ID>"]
    }
  ]
}
```

`spu_job_id` 必须为该候选的 `drill-<candidate_id>`；`brand_job_id` 必须是同国家、路径、层级和双期的完整 `brands` 任务。系统重建实际 SQL 与全部分页，核对 SPU、量额与基础候选的双期值，品牌量额也须还原同一总体，不能借不同分母或筛后总体通过。

`sku_job_ids` 只接受同单元的完整 `sku_trace` 任务，可按显式商品清单分片。被声称核验过的每个 SPU 都必须由精确目标路径 SKU 记录还原双期源行数、量额和无效值数量，并保留原始层去向、身份与上下文。分片只是查询组织方式，未分片、未取证或未核验实体仍属于完整总体，不能丢掉。

NULL、空白和“0”是不可用于跨父体关联的 SKU 值。查询在聚合前筛选和最终身份关联两处执行相同规则，完整保留同父体占位源行；数值分母不随身份有效性缩减。结果证明不能将占位 SKU 的其他父体 raw 行当作该对象去向。目标期存在不可追踪身份时，不接受实体 `verified`；已完成可做内部核对的 `blocked_external` 可保留同父体上下文与全部未核影响，不能声称有精确 SKU 去向。

实体组可复用共同证据，但 `product_ids` 必须准确、互不重复且属于总体。`status` 仅为 `verified / blocked_external`；前者须已对具体原因、反证和边界形成证据支持的判断，不能因已下载轨迹就标为核实。草稿中未填实体及受阻实体都计入未核残余，包括量额没有变化的商品；正式提交前每个实体均须完成当前可做的内部调查，其他实体受阻不能豁免尚未开展的调查。

先将含上述 `population_proof` 的单个答项草稿交给 `investigation-proof`，读取输出中的 `computed_impact` 填入该答项的 `impact`，并继续补齐其他检查。机器计算口径如下：

| 指标 | 总暴露 `exposure` | 未解释 `unexplained` |
| --- | --- | --- |
| `spus` | 双期精确路径 SPU 并集数 | 尚未取得 `verified` 实体判断的并集数 |
| `bands` | 两期各固定价格带 SPU 占比的最大值，单位 pp | 相应两期各带未核实体占该期全部 SPU 的占比最大值；未知价格单列 `-1` |
| `units / amount` | 全体 SPU 双期量/额绝对变化之和 | 未核实体的绝对变化之和，正负不能抵消 |

这是核验覆盖和未核残余的机算依据；价格带金额结构、已证缺陷的实际影响、修正敏感性及原因性质仍须按业务方法说明。机器已可计算的总体不能改写成 `unknown`，也不能手填零覆盖正残余。导入时服务端重算、验证声明并保存 `population_proofs`，不信任子 Agent 自填统计；子 Agent 不得在答卷顶层伪造该服务端字段。

### 路径缺口的 `gap_proof`

每条 `raw_presence_gap / std_presence_gap` 答项填写 `gap_proof: {source_coverage_job_id, entity_assessments}`。`entity_assessments` 与上述实体组字段相同：精确且不重复的 `product_ids`、`status: verified | blocked_external`、具体 `reason / counterevidence / evidence_ids`。不能只查少数消失商品，再以文字代表整个路径。

基期取自候选完整基线中**严格早于缺口月的最近同路径观测月**；终期为运行包的 `data_month`。机器复核候选来源任务、实际请求、全部响应页及同路径基线，拒绝用缺口后的恢复月、筛后基期或无依据零值替代锚点。无可靠历史锚点时无法构成完整证明，仍须继续查明并保留未完成。

`source_coverage_job_id` 必须对应同国家、完整路径与层级、上述起止月份的完整标准诊断任务，不接受商品分片。每个基期种子（平台、商品/SKU、完整路径及属性身份）在每个后续自然月的 raw/std 两层都必须有去向、明确 `no_observation` 或 `untraceable_seed_identity`。已有去向须保持精确 SKU 身份，分别识别原父体和新父体；身份不可追踪不能伪装成已证明无观测，也不能标为实体 `verified`。

占位 SKU“0”与 NULL、空白一样不可追踪，不能因另一个父体也有“0”而生成 `same_sku_other_parent`。保留种子全部源行和基期量额，以 `untraceable_seed_identity` 保留每个自然月和来源层的未决去向；同父体记录可另用完整 SKU 轨迹调查，不冒充已恢复具体 SKU 身份。

种子按完整身份去重，SPU 数、源行数、销量和金额须回算到基期锚点。重复出现在后续各月的种子上下文只计一次，两层去向及其他路径记录不重复累加为基期暴露。把含 `gap_proof` 的单个答项草稿交给同一 `investigation-proof` 命令，再将 `computed_impact` 填入 `impact`：

- `spus`：完整基期 SPU 数；未解释为尚未 `verified` 的 SPU 数。
- `bands`：各固定带 SPU 占完整基期总体的最大份额；未解释为各带未核 SPU 占该总体的最大份额，单位 pp，未知价带单列 `-1`。
- `units / amount`：按 SPU 汇总的基期销量/金额绝对值之和；未解释为未核实体的对应绝对值之和。

这些数值是**缺口前已知暴露基数，不是缺口月真实损失**。没有观测不能改写成零销售或扣减值；已知基期暴露也不能改为 `unknown` 隐去残余。去向矩阵完整只证明对象覆盖，合同应采状态、采集/映射原因及合理性仍要逐实体核查支持与反证。

### 全部对象调查完毕，才能正式提交

答项证明类型必须唯一对应信号：`result_change` 只接受 `population_proof`，`raw_presence_gap / std_presence_gap` 只接受 `gap_proof`；其他义务不附这两种证明。缺失、混用或同时提交两类证明均拒绝，主审必须对应同一份完整实体集合。

结果变化与路径缺口均须在导入重算中满足 `cohort.not_assessed = 0`。草稿证明可展示尚未调查的对象，`investigation-submit` 则直接拒绝此类交付；外部阻塞或数值容忍不能掩盖未做的内部核查。`blocked_external` 实体也必须完成当前可做调查，逐项说明具体缺证并保留机算残余。完整内部调查、实质主审和冻结容差共同约束完成；低残余还须可执行的四指标上界证明，按 reviewed_residual 单列，不能授予无保留放行。所有对象有答卷或外部阻塞已获主审接收仍不等于完成。

子包证据用 `ev-…`，引用主包已有证据用 `main:ev-…`。导入时主 Agent 检查原始文件、证据和事件完整性，生成主包证据映射；主审使用导入后的证据 ID。子 Agent 提交后停止写入，不能改动原文件继续沿用批准。

主审文件含 `reviewer_id / submission_sha256 / obligations[]`。每项有 `obligation_id / decision / reason / scope_check / check_reviews / evidence_checked`；`decision` 与每个检查的判断均为 `accept / return`。核对全部动作、总体证明、实体理由及影响数值证据；主审不能只读摘要，也不能把整个任务一次接受代替逐项审核。

含 `population_proof` 或 `gap_proof` 的义务另需 `entity_reviews[]`，每组填 `assessment_sha256 / decision / reason`，覆盖全部 `entity_assessments`。哈希使用**导入后**、证据 ID 已映射到主包的完整实体组对象调用 `acceptance_core.digest(group)` 取得，不使用导入前哈希或只哈希商品 ID。接受义务时每个实体组的 `decision` 都必须是 `accept`，理由须说明实际核查的原因与反证；任一组不合格则将该义务退回。`evidence_checked` 覆盖动作、机算总体、影响数值和所有实体组证据。

### 历史来源逐段对账

`history_source` 答项的 `history_coverage` 必须逐段对应 `source_material.segments`，不漏、不重、不改原始行范围或哈希：

- 有业务事项的段落：保留该段四个定位字段，填写非空、唯一且已登记的具体 `obligation_ids`，并以 `reason` 说明段落内容与各义务如何对应。不能用另一项历史文件阅读任务代替具体核验。
- 仅为标题、说明或确实不适用的段落：保留同样的四个字段，填写具体 `not_applicable_reason`，不同时登记问题 ID。单写“正常”“同上”“已覆盖”不构成依据。
- 原文有多个月份、商品或 SKU 时，保留完整范围并映射全部需要核验的事项；不能只取首次月份或一个代表对象。

主 Agent 接收历史来源时另需 `history_segment_reviews[]`，包含每个冻结 `segment_id` 的 `decision: accept` 与具体 `reason`。任一段对应不当、遗漏事项或不适用理由不成立，退回整个来源义务补查；文件读取成功或段落数一致不等于事项已核实。

`issue_type: historical_population_import_unfinished` 的迁移义务另需 `imported_obligation_ids` 和 `inventory_reconciliation`，逐对象说明完整历史清单如何进入已登记义务；不能引用它自己或仅关闭“导入”这一行。

某义务全部检查获主审接收后，有真实外部阻塞则记 `blocked_external`，无阻塞且满足全部门槛才记 `verified`。一包部分退回时各义务仍有独立状态，不能按任务整体状态冒称全部已证。重派保留旧审核历史，但旧审核不再代表当前义务已完成。

## 兼容索引与报告

**3.2.1 的调查台账与独立主审是业务调查的权威完成记录，另须通过必查、历史回归、抽查与案件完整性门槛。** 不要求为了通过核验再抄写 `decisions/`、`sample-reviews.json`、`historical-recheck.json` 或 `internal-reconciliation.json`，这些旧索引也不能覆盖或替代本版主审。新候选/样本计数从调查台账生成，实质核实与主审确认的外部阻塞分别统计。旧版仍按其原契约读取，处置格式继续兼容：

```json
{
  "candidate_ids": ["cand-实际ID"],
  "title": "结论短句",
  "scope": "国家、路径、月份与适用边界",
  "facts": "同范围前后值、变化、影响及证据",
  "reason": "继续、待核或有据停止的理由",
  "counterevidence": "已检查的竞争解释与尚未证实的部分",
  "next_action": "具体补证、修复或复验动作",
  "evidence_ids": ["ev-实际ID"],
  "status": "needs_evidence",
  "business_verdict": "undetermined"
}
```

旧版 `record <RUN> --file <JSON>` 可登记对象或对象数组；`status` 为 `confirmed_defect / supported_change / low_impact / needs_evidence`，旧低影响格式还需 `residual_impact.spus / bands / units / amount`。保留全部候选 ID 和其基础证据。登记率不是原因核实率；3.2.1 不需要复制这份旧索引，更不能将整组待证处置当成完成调查。

`sample-reviews.json` 的 `sample_ids / status / reason / evidence_ids` 保持兼容，状态为 `checked / needs_evidence / defect`。`checked` 只说明已描述的当前语义检查未见冲突，不证明真实销量、历史价格、原厂身份或整个类目正常。混合父体按正式标准完整路径选实际子体，缺陷影响只算已举证子体。

`report-content.json` 包含 `summary / normal_scope / limitations / findings[]`。每项 finding 有 `title / scope / facts / counterevidence / assessment / action / candidate_ids / obligation_ids / evidence_ids`。全部已主审的结论与阻塞须有报告对应，合并表达保留全部义务 ID；不能用 Top-N 隐藏其他重要事项。没有信号、已查未见异常、无观测、未执行和外部受阻分别表达。

报告开头分点给出使用判断及证据；基础扫描与实质核实分别计数。尚未全部核实必须明显标“阶段进度”，不能写“执行完成但证据受限”。详见 [报告写法](report-writing.md) 和 [报告骨架](../assets/report-template.md)。

## 收尾、状态与旧版本兼容

```sh
python3 <RUN>/inputs/skill/scripts/acceptance_run.py checkpoint <RUN>
python3 <RUN>/inputs/skill/scripts/acceptance_run.py finish-scan <RUN>
python3 <RUN>/inputs/skill/scripts/acceptance_run.py validate <RUN>
python3 <RUN>/inputs/skill/scripts/acceptance_run.py render <RUN>
python3 <RUN>/inputs/skill/scripts/acceptance_run.py seal <RUN>
python3 <RUN>/inputs/skill/scripts/acceptance_run.py verify-seal <RUN>
```

这些命令不表示可以跳过未完成义务。`checkpoint` 保存当前机器核验、报告及事件，运行仍可继续；最终收尾前必须完成所有可做调查和主审。`finish-scan` 在最后一次主包、子包数据查询之后重取结束指纹；随后若补查，须重新收尾并保留新证据，不能复用旧结束指纹。

- `complete`：全部程序及调查义务满足门槛，报告对应完整，必需证据齐备。
- `limited`：结构与实质调查完成，但仍需披露不构成未完成调查的使用边界；不能把外部阻塞或未解释残余改名塞进此状态。
- `incomplete`：任何必跑任务、分页、义务、主审、报告对应或收尾未完成；**已有完整基础扫描但仍有外部受阻事项，也属于调查未完成。**

使用判断独立为 `pass / qualified / reject / undetermined`，修复状态也独立。调查证实了缺陷，可以完整交付打回及返修意见，不能因此放行；数据修复需要授权并复验。只有全部程序门槛满足、本轮适用义务实质调查完成、暂留分流审核有效且不再有证据或范围限制，3.3 才允许最终 `seal`；有界残余已审与充分核实仍分别计数。还在调查、返工或真实外部受阻时，只能阶段存档。

历史 v3.0 包没有本版调查台账，按其冻结版本读取和解释，保留原报告、状态、证据与封存。旧 `limited` 或成功封存可能仅代表当时记录完整，不能推算本版实质核实率，更不能冒充 3.1.0 全流程测试。缺少新字段显示未知/待重建，不补零。

只改变展示、引用校验性能或等价计数的后处理，可在单独留档的后处理器中读旧包并记录版本、源码、哈希与选择事件；保持旧查询计划、政策、分析及输入不变。查询、抽样、调查完成规则、总体核验或业务计算变动须新建运行。已封存包不就地覆盖；补查关联旧运行。事件哈希链和文件哈希用于发现普通改动，不提供第三方签名、恶意重写防护或事务快照保证。

### 结构化附件与原始定位

`attach --file <JSON/JSONL> --kind <来源类型> --source <实际来源>` 保存原始文本和字节哈希，并把 JSON / JSONL 放在结构化 `content` 中；例如 JSON 的 `records[0]` 用 `/content/records/0` 定位。普通文本仍在 `content`，另有带一基行号的 `text_lines` 可逐行引用。解析只提供真实文件内容的可定位视图，不认证其中声明，也不能把手写附件当成已执行查询或实际外部请求。

## 工程续跑入口（3.2.1）

- 未封存观测复用：`evidence-reuse <NEW_RUN> --source <SOURCE_RUN> --job-ids-file <JSON数组>`。新计划重建精确任务，完整核验来源，不算新 SQL；与已封存归档 `archive-replay` 的边界见 [案件协议](case-machine-protocol.md)。
- 再次 `finish-scan` 保留导入和每次执行的不可变收据，重新核验实际分页、事件及指纹时序。失败/取消继续计未完成，不能回退使用旧成功掩盖本轮失败。
- 主审后批量同步：`case-acknowledge-review <RUN> --coordinator <MAIN_ID> --case-ids-file <JSON数组>`，全部案件当前逐义务审核通过校验后保存一次；混入未审项整批拒绝。原单案参数仍支持。
- 答卷内所有 `evidence_id / *_evidence_id / evidence_ids / *_evidence_ids` 都按真实引用校验、导入和归一化，包括辅助计算证据。原始自由文字不自动替换；最终审核使用导入后的主包 ID。

报告执行宏观到微观主线，七类概览与主问题先于工程完成度和辅助观察；字段见 [报告写作](report-writing.md)。默认抽查核身份、量价和映射结构，无信号新发现可显式给维护的 `issue_type`，省略时为 identity；不自动生成分类语义专项。
