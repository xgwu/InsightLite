# ADR 开放决策清单

> 规则：先拍板，后改权威（YAML），再改实现，最后改断言与文档（冲突处置流程 1-6）。

## 待裁决

| # | 决策点 | 背景 | 影响 | 状态 |
|---|--------|------|------|------|
| D4 | 管理员豁免语义 | 技术基线遗留 | 治理层 | **待裁决** |
| D3 | PydanticAI 升级（Thinking 模式） | 技术基线 D2 备选路径，W13 按触发判据检查 | Agent 编排层 | 观察项 |

## 已收口

- **D1**（2026-09-16）：A 路线自研 SMQ 编译器 + 权限策略引擎（四判据否决 Cube 复用）。
- **D2**（2026-09-19，发起人确认）：Agent 框架不引入 LangChain/LangGraph；openai 兼容 SDK + Pydantic 契约 + 自研轻量编排。
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
