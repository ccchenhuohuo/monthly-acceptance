# 仓库边界

本工作区 `/Users/chenyu/dev/大盘数据治理` 的验收 Skill 唯一远端为 `https://github.com/ccchenhuohuo/monthly-acceptance.git`，**公开仓库（2026-10-30 核实，创建于 2026-09-09）**。

提交或推送前检查 `git rev-parse --show-toplevel`、`git remote -v` 与暂存文件清单。不得将此处开发的 Skill 推送到 flywheel-plugin 或另一个工作区的远端。

实际维护 `.claude/skills/monthly-acceptance`；`.agents/skills/monthly-acceptance` 为相对软链接。只跟踪 Skill 与根目录发布资料。

**仓库公开，推送即发布。** 验收运行目录、项目配置、商品台账、内部数据、Doris 连接参数与任何凭据一律保持本地（`.gitignore` 已排除除 Skill 与根目录发布资料外的一切）；提交前先看暂存清单，不要把某次运行的结果、报告或服务商数据带进去。

当前版本 `0.1.1`（单表 monthly_sales_wide 架构）。0.0.2 及更早的全景与议题架构保留在 tag `v0.0.2-final`，不再维护；它们的运行目录自带当时的脚本，查的旧表已删除。

代码改动运行对应测试；发布前跑全部测试。使用 `PYTHONDONTWRITEBYTECODE=1` 和 `-p no:cacheprovider`，避免将生成缓存纳入发布。
