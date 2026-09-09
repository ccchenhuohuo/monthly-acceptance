# 仓库边界

本工作区 `/Users/chenyu/dev/大盘数据治理` 的验收 Skill 唯一远端为 `https://github.com/ccchenhuohuo/monthly-acceptance.git`，私有仓库。

提交或推送前检查 `git rev-parse --show-toplevel`、`git remote -v` 与暂存文件清单。不得将此处开发的 Skill 推送到 flywheel-plugin 或另一个工作区的远端。

实际维护 `.claude/skills/monthly-acceptance`；`.agents/skills/monthly-acceptance` 为相对软链接。只跟踪 Skill 与根目录发布资料；验收运行、证据、项目配置、内部数据和凭据保持本地。

当前发布、运行、政策、协议、台账和抽样数据格式版本统一为 `0.0.1`，发布 tag 为 `v0.0.1`。当前全景与议题架构使用显式 `architecture="panorama_questions"`，不得依据版本号大小回退旧流程。旧数字版本仅留在历史兼容分支和明确的历史资料中；不改写旧冻结运行。

代码改动运行对应行为测试；完整发布执行 pytest。使用 `PYTHONDONTWRITEBYTECODE=1` 和 `-p no:cacheprovider`，避免将生成缓存纳入发布。
