# ADR 决策清单

> 规则：先拍板，后改权威（YAML），再改实现，最后改断言与文档（冲突处置流程 1-6）。

## 待裁决 / 观察项

| # | 决策点 | 背景 | 影响 | 状态 |
|---|--------|------|------|------|
| — | （当前无待裁决项） | | | |

## 已收口

- **D1**（2026-09-16）：A 路线自研 SMQ 编译器 + 权限策略引擎（四判据否决 Cube 复用）。
- **D2**（2026-09-19，发起人确认）：Agent 框架不引入 LangChain/LangGraph；openai 兼容 SDK + Pydantic 契约。
  **2026-09-19 修订（D3 裁决连带）**：编排实现由「自研轻量编排」改为 PydanticAI（见 D3）；
  反对重型框架（LangChain/LangGraph）的论证不变——单触点、无多 Agent 图、延迟预算优先。
- **ADR-D5**（2026-09-19，Kenny）：SSO→策略角色映射定版——
  `employee→employee / manager→dept_manager / hrbp→hr_admin / exec→hr_admin（exec 可见全员薪酬）/ auditor→auditor`；
  未登记映射一律 403（fail-closed）。落地：`backend/api/config.py` + sso_mock 白名单补 auditor。
- **R5 硬规则**（2026-09-19，Kenny）：critical/sensitive 字段进 group_by 且非 hr_admin → 整查询 DENY
  （group_by 取值本身即输出，独立于「决策==遮蔽」判定）。落地：YAML hard_rules + `perm_engine.decide()`，
  原 D-3 缺陷销项；矩阵 A6 断言（含误伤侧对照：非敏感 group_by 不受影响）。
- **auditor public 档**（2026-09-19，Kenny）：auditor 可见 public 数据（部门架构类）。
  scope=none 语义修订为「纯 public 引用放行，任一非 public 引用（含 filter/order_by/group_by）整查询 DENY」；
  个人行数据集 row_cond 仍显式空集。落地：YAML 注释 + decide() scope-none 分支重写；
  矩阵 A1 全格无豁免 + A7 决策层 4 条 + C8 行为层 + API 冒烟 A8。
- **D3 PydanticAI**（2026-09-19，Kenny）：**现在就引入 PydanticAI 承接编排层**（推翻原「W13 观察项」安排，
  触发判据不再需要）。落地：`backend/core/orchestrator.py`（pydantic_ai.Agent，
  output_type = Union[SmqOutput, RefuseScope, ClarifyOutput]，提示词仍由 DSL 自动拼装/C6）；
  `render.ask()` 切换 PydanticAI 路径，框架未安装时降级旧 llm_intent 路径；
  离线回归走 pydantic-ai TestModel（tests/test_orchestrator.py，8 断言）；
  下游 S.validate 不可信输入纪律不变（编排层不放松任何 fail-closed）。
- **D4 管理员豁免**（2026-09-19，Kenny）：**默认豁免 + 逐次审计**（对齐行业默认）。
  hr_admin 通过角色/数据集 fail-closed 校验后短路 ALLOW，verdict.exempt 标记；
  每次豁免查询由调用方强制写 append-only 审计（`backend/core/audit.py`，
  data/audit/audit-YYYYMM.jsonl：主体/角色/SMQ/决策/版本/来源）；审计写入失败显式抛错
  （审计不可用 = 豁免查询不可用，不静默降级）。豁免是唯一管理员语义：未来新增约束默认
  对 hr_admin 无效，除非显式写在豁免分支之前。落地：decide() 短路分支 + ask()/query 审计接线；
  矩阵 A8 断言（豁免标记/空策略产物/fail-closed 不豁免/审计五要素/非豁免不写）。
