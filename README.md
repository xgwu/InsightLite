# InsightLite（轻量级 ChatBI）

> 让业务人员把自己手上的表格变成可对话、可下钻、可固化的分析应用，
> 同时让每一次问答都严格落在企业已有的权限与口径之内。
> **LLM 只输出 SMQ（语义模型查询），SQL 由确定性编译器生成。**

本仓库承接《功能描述书》（prd.html · 47 项功能点）与《开发规划》（dev-plan.html · 方案 B / 14+2 周）的
一期工程，核心资产自 Sprint 0（chatbi-lite-sprint0）迁移而来，迁移记录见 `docs/MIGRATION.md`。

## 已锁定决策

| 决策 | 结论 |
|---|---|
| D1 核心路线 | **A · 自研 SMQ 白名单编译器 + 权限策略引擎**（Cube 路线四判据实证否决，2026-09-16） |
| D2 Agent 框架 | 原生 OpenAI 兼容 SDK + Pydantic 结构化输出 + 自研轻量编排（PydanticAI 为 Thinking 备选，见技术基线） |
| 试点数据域 | HR（考勤 / 假期 / 薪资脱敏 / 组织架构），表结构与权限场景由发起人定义 |
| LLM | DeepSeek（OpenAI 兼容），经 `llm_intent` 抽象可换 Moonshot / Ollama |
| 部署 | 独立环境：PostgreSQL + 模拟 SSO（JWT），不碰企业内网 |

## 目录结构

```
InsightLite/
├── run_all.py               # 一键自检（离线，零 API 依赖）
├── semantic_model/          # 语义模型 YAML（进 Git，走 MR 评审）
│   └── hr_domain_v1.yaml    # HR 域 5 表 26 字段、4 级敏感度（W3 已评审收口）
├── backend/core/            # 核心域包（Sprint 0 资产，平铺 import；W7 包化）
│   ├── dsl_loader.py        #   YAML -> 内存注册表
│   ├── smq_schema.py        #   SMQ 契约与校验
│   ├── compiler.py          #   SMQ -> SQL 确定性编译器（原 spike_sqlglot）
│   ├── perm_engine.py       #   权限三态引擎（fail-closed）
│   ├── render.py            #   ask() 五环节对话入口
│   ├── llm_intent.py        #   LLM 意图解析（原 spike_llm_intent）
│   ├── sso_mock.py          #   模拟 SSO / 5 角色 JWT（原 spike_sso_mock）
│   └── e2e_live.py          #   端到端实跑（需 DEEPSEEK_API_KEY）
├── eval/                    # 评测集 + harness（离线回放 / --live 双模式）
├── tests/                   # 契约与权限矩阵单测（CI 门禁）
├── demo/                    # 演示转录与生成工具（make_demo_page 为旧站点留档）
├── deploy/seed/             # HR 种子数据生成器 + PostgreSQL DDL
└── docs/                    # 迁移记录与 ADR
```

## 快速开始

```bash
# 依赖（建议 venv）：pyyaml sqlglot openpyxl
cp .env.example .env            # 填入 DEEPSEEK_API_KEY（离线自检可不填）
python run_all.py               # 一键自检：种子数据 -> 单测 -> 编译器 -> SSO -> eval
python backend/core/e2e_live.py # 端到端实跑（需 API Key）
```

基线状态（迁移日 2026-09-19 验证）：`ALL CHECKS PASSED`，
口径一致率基线 **21/21 = 100%**（门槛 80%；过拟合声明见 dev-plan 8.2，W11–12 盲测集另行计分）。

## 纪律（自 dev-plan 继承）

1. **结对维护**：DSL / 编译器 / 权限引擎三个核心模块的变更，两人都必须能评审。
2. **每 2 周一个可演示增量**（M0–M5 里程碑见 dev-plan 第 5 节）。
3. **砍单按清单**：临时需求进 backlog，出线评审统一裁决；权限矩阵缺陷例外，永远立即修。
4. **本地提交，不自动 push**；推送需发起人明确要求。
