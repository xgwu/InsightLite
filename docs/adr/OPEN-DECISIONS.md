# ADR 开放决策清单（W7）

> 规则：先拍板，后改权威（YAML），再改实现，最后改断言与文档（冲突处置流程 1-6）。

| # | 决策点 | 背景 | 影响 | 状态 |
|---|--------|------|------|------|
| ADR-D5 | SSO 角色 → 策略角色映射（exec→hr_admin 暂定） | sso_mock 角色名（employee/manager/hrbp/exec）与 YAML 角色（employee/dept_manager/hr_admin/auditor）不一致；映射表在 `backend/api/config.py`，未登记一律 403 | exec 是否可见全员薪酬属薪酬敏感面 | **待裁决** |
| R5 | 敏感列 group_by 硬规则 | 非 hr_admin 对 critical 列 group_by：输出列被 MASK，但 GROUP BY 按真值分组，组数泄漏取值基数（权限矩阵冲突报告 [C2]） | 需同步写回 YAML hard_rules + decide() + 矩阵断言 | **待裁决** |
| AUDITOR | auditor 是否可见 public 数据 | 权威内部矛盾：`roles.auditor.scope=none` vs `permission_matrix.public.auditor=allow`（冲突报告 [C3]），当前 scope=none 优先全 DENY | 矩阵 A1 对 auditor 非 DENY 格暂不断言 | **待裁决** |
| D3 | PydanticAI 升级（Thinking 模式） | 技术基线 D2 备选路径，W13 按触发判据检查 | Agent 编排层 | 观察项 |
| D4 | 管理员豁免语义 | 技术基线遗留，W7 内需拍板 | 治理层 | **待裁决** |

## 已收口

- D1（2026-09-16）：A 路线自研 SMQ 编译器 + 权限策略引擎（四判据否决 Cube 复用）
- D2（2026-09-19，待发起人确认）：Agent 框架不引入 LangChain/LangGraph；openai 兼容 SDK + Pydantic 契约 + 自研轻量编排
