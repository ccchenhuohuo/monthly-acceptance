# 问题调查任务简报（0.0.1）

先阅读本子包 `brief.json` 中的 task、obligations、完整 `question_scope` 合同及 `source_material`。以实际问题、范围、检查、反向分量和停止条件开展调查；不得用本提示替换冻结合同。详细字段以 [问题协议](../references/question-workflow.md) 为准。

1. 从已有全景和完整聚合开始，核对国家、平台、事件月、背景月、路径和 raw/std。复用父包证据用 `main:ev-...`，新增子包证据用 `ev-...`。
2. 主包可先生成定向 jobs；子包用 `question-collect` 执行已生成任务。需要变更范围或新增议题时提交建议，由协调者登记，不能自行缩减合同。
3. 必要时补 cohort、明细或跨父体/路径的 lineage。分片不增加业务义务；细查服务于质量疑问，不机械要求全部 SKU 的市场原因。
4. 逐 required_checks、required_components 回答，关键量值保留原始定位和复算。分别说明观察变化、已证错误与未解决风险；价格未知与空分母不填零。
5. 尚未回答质量疑问时保存实际进度、尝试和全部剩余工作。只有具体外部记录确实受阻且有实际尝试，才提交 blocked_external；查询失败或容量不足须续跑。
6. 写本子包 `submission.json`：外层为 task_id、attempt、agent_id、answers。主 Agent 执行 submit 后另行阅读、复算与 review；Worker 不审核自己，不填写主审结论代签。

交接时指出实际证据、查询覆盖、当前判断和剩余动作。原始任务、进度与旧答卷留存，后续版本不能覆盖历史材料。
