# monthly-acceptance

私有、独立维护的月度大盘数据验收 Skill。当前发布、运行、政策、协议和数据格式版本统一为 **0.0.1**。

从国家、平台、数据层和连续月份的全景出发，再登记具体质量议题，按需深入 SPU/SKU 并独立主审。重点检查完整性、重复放大、身份连续性、数值守恒、时间序列、价格带及历史缺陷回归。分类语义不做默认专项。

## 使用

- [Skill 入口](.claude/skills/monthly-acceptance/SKILL.md)
- [执行协议与字段](.claude/skills/monthly-acceptance/references/question-workflow.md)
- [报告写作](.claude/skills/monthly-acceptance/references/report-writing.md)

仓库保留当前工作区目录：实际维护内容位于 `.claude/skills/monthly-acceptance`，`.agents/skills/monthly-acceptance` 为同一 Skill 的相对软链接。将这个目录安装到目标项目，并按 Skill 入口准备该项目的 `验收/config.yaml`、`项目范围.md` 和可用契约。数据库凭据由本机配置管理，不在仓库中。

执行环境为 Python 3.11+、支持文件锁的 macOS/Linux、PyYAML 与 MCP Python SDK；测试另需 pytest。只读 Doris MCP 默认读取本机 `~/.codex/config.toml` 的 `mcp_servers.doris`，也可通过 CLI 指定配置文件。

```sh
python3 -m pip install -r requirements.txt
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider .claude/skills/monthly-acceptance/tests
```

启动验收会冻结脚本、政策与项目输入；随后执行该运行中的冻结脚本。实际数据库表和字段须符合协议。本仓库不包含生产数据、运行证据、历史报告、内部项目资料或凭据。

## 版本与验证

`VERSION`、Skill metadata、运行、政策、协议、台账和抽样数据格式统一使用 `0.0.1`，发布 tag 为 `v0.0.1`。当前全景与议题架构由显式 `architecture="panorama_questions"` 标识，不依赖版本号大小选择架构。旧数字版本仅用于历史兼容分支，旧冻结包保持原版本与原 SQL。

本版完整测试 **764 项通过**；包含历史回归来源验证、平台身份隔离、阅读原始产物要求及旧入口清理。旧 3.3.0/4.0.0/4.0.1 查询计划已核对兼容。测试通过不等于整月业务数据验收完成。

唯一远端为 `https://github.com/ccchenhuohuo/monthly-acceptance.git`。本项目与 flywheel-plugin 分开维护和发布。
