# monthly-acceptance

**月度大盘数据验收 Skill · 0.3.0**

服务商每月把爬虫加算法估出的市场数据全量重推到 Doris `internal.flywheel.monthly_sales_wide`（平台类目 + AI 标准类目 + 属性标签合在一张表）。这个 Skill 自顶向下判断这批数能不能信、哪里不能信、对我们用的数影响多大，交付一篇文字验收报告。

## 设计

```mermaid
flowchart LR
    A[init：冻结配置、范围、类目标准<br/>记下源表更新时间与现役发布] --> B[views：全量视图<br/>全历史 × 全部站点]
    B --> C[digest：每个模块一份摘要<br/>固定规则的初筛标记]
    C --> D[判读：模型自顶向下读<br/>十二个模块]
    D --> E[下钻：q.py 追到 SPU/ASIN<br/>打标抽样判定]
    E --> F[对照历史台账]
    F --> G[check：运行期间数据未变<br/>写报告]
```

程序只做两件事：把全量 SQL 跑完并压成可读摘要，以及精确复算。是否异常、是什么病、影响多大由模型判断。可复核靠每个结论都能回到运行目录里落盘的 SQL 与结果，不靠哈希链、角色分离或完成门槛。

十二个每期必跑模块：到货与完整性、站点水位、覆盖还是估算、标准类目四项结果（SPU 数、价格带、销量、销售额）、价格、长尾、品牌层、平台类目树、打标质量、本品对拍、治理承接、已知缺陷复验。方法与长期有效的数据事实在 [methodology](.claude/skills/monthly-acceptance/references/methodology.md)，模块细则在 [checks](.claude/skills/monthly-acceptance/references/checks.md)。

## 使用

见 [SKILL.md](.claude/skills/monthly-acceptance/SKILL.md)。项目侧需要 `验收/config.yaml`、`项目范围.md`、`scope.json`、`已知缺陷台账.json` 与各台账。

```sh
python3 -m pip install -r requirements.txt
python3 .claude/skills/monthly-acceptance/scripts/views.py init --month 2026-09
```

环境：Python 3.11+，PyMySQL、pandas、PyYAML；Doris 连接参数取自环境变量 `DORIS_HOST/PORT/USER/PASSWORD` 或本机 doris MCP 配置；冻结类目标准需要已登录的 `lark-cli`。

## 测试

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider .claude/skills/monthly-acceptance/tests
```

## 版本

- 0.3.0（2026-10-30）：在下游用数的粒度（小类 × 品牌 × 金额）上补检查。起因是 2026-09 季报顺手找出 11 处验收没报的问题。打标抽样加金额前三与贵价商品；新增小类单品集中度、少见平台根节点、跟同类比的离群价（22_leaf_items）；品牌清洗核对（标准名吞进不相干的原始品牌、一个品牌拆成多个标准名、片假名与大小写变体、非品牌词）与二级类目 OTHERS 同比、各小类前五品牌（23_brand_map、24_leaf_brands）；成片的不符必须进报告并按整叶量化，tagging.py 汇总里直接列出这类叶子；离群价把“-其他”叶单列；品牌名按“/”和括号拆分（日本“カタカナ(Latin)”写法）；三张小类视图按国家×平台分段跑，天猫再按一级类目分段；漏检案例按真实比例写成 tests/test_missed_cases.py 里的考题（金额取整、商品换代号）。
- 0.2.0（2026-10-30）：接入服务商沟通，按“方法进 skill、知识留项目、证据留运行目录”分三层。服务商的口径声明留在项目侧沟通台账，init 冻结成 inputs/服务商口径.md 开工读；准则裁定随判定标准冻结（taxonomy.py 合并 验收/准则裁定.md）；具体问题与修复承诺进项目侧沟通台账（ledger.py 渲染），第 6 步复验。新增 vendor.py 每期冻结服务商需求表与群聊增量（打码、附件不下载）；views.py 记上一次运行，报告新增“上期事项对账”；服务商说明过的口径波动降级为“已说明的口径行为”。
- 0.1.1（2026-10-29）：首跑 2026-09 后补的三处方法缺口——叶子的平台类目构成视图（不靠抽样估整叶错分）、单品集中度视图（抓价格不跳的身份错联与估算失真）、打标判定的统一计法与汇总脚本 tagging.py。
- 0.1.0（2026-10-29）：按单表 monthly_sales_wide 与 VIJIM 标准重建；恢复价格、长尾、头部法、品牌、本品、类目树维度，新增打标质量与治理承接；去掉 0.0.x 的证据链、议题合同与完成门槛。
- 0.0.2 及更早：全景与议题架构，基于已删除的旧表，保留在 tag `v0.0.2-final`。
