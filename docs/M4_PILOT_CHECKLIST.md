# M4 试点启动核对单（W10 · 2026-09-19）

> dev-plan M4（W10）出线标准：**权限矩阵全量自动化通过 + 补偿措施三项在产品内可操作**。
> 未达即停线修，不带病推进。逐项勾选后再启动试点。

## 一、M4 出线标准核对

| # | 验收项 | 现状证据 | 结论 |
|---|---|---|---|
| 1 | 权限矩阵全量自动化通过（CI 必过） | `tests/test_perm_matrix.py` 87 断言（决策层逐格 / fail-closed / 行为层 oracle 对照 / 权威一致性 / D4 豁免审计 / C5 validate→decide 管线回归），`run_all.py` 每次提交全跑 | ✅ |
| 2 | FR-12 审计日志在产品内可操作 | D4：hr_admin 豁免逐次写 JSONL（五要素，写失败=查询失败）；W10 新增 `GET /audit/recent`（仅 hr_admin，跨月倒序，测试 G1–G4） | ✅ |
| 3 | FR-45 拒答与引导在产品内可操作 | refused_scope / clarify / denied 三态前端均有明确文案与下一步引导（W8 Chat 分阶段呈现，deny 回显 SMQ 排障） | ✅ |
| 4 | FR-27 入板人工比对确认 | W9 看板入板流程内建比对确认步骤 | ✅ |
| 5 | 看板不构成权限旁路 | widget 只存口径，读取时经唯一 `_execute()` 以请求者身份重裁（W9 live 验证：hrbp 9 组 / 经理 3 组子树） | ✅ |
| 6 | 数据接入不绕过权限 | W10 连接器只能写 `stg_` 前缀表（表名白名单），正式消费必须经语义建模+权限引擎；凭据只存环境变量名 | ✅ |

## 二、试点启动准备（本周内完成）

- [ ] **种子用户**：首批 ≤30 人（HRBP×2、部门经理×4、员工×20、审计×1），发放 `docs/USER_GUIDE.md`
- [ ] **环境**：uvicorn 启动；`LLM_API_KEY` 配置（/ask 可用）；演示身份与真实人员对应表（线下保管）
- [ ] **连接器 v1 配置**：至少 1 条真实上游（考勤/HRIS REST API）完成「登记→同步→字段映射人工确认」全流程；`CONNECTOR_SCHEDULER=1` 开启定时同步
- [ ] **审计巡检**：管理员每周一次 `GET /audit/recent?limit=200` 抽查豁免查询合理性
- [ ] **告警巡检**：每周检查 `data/alerts/`（同步失败须当日处理或暂停该连接器）
- [ ] **基线快照**：试点开始日记录 run_all 全量结果与 eval 26/26 基线，作为 W11–W12 日报对照

## 三、W11–W12 日报机制（试点运行期）

三项指标，每日由 eval harness 口径输出：

1. **口径一致率**（分母 = 语义覆盖集合）：目标 ≥80% 起步、向 90% 收敛
2. **静默错误率**：stakeholder 纠正数 / 总问答数——每条纠正 48h 内转 eval case
3. **明确失败率**：refused+denied+error 占比（明确失败是诚实，不是坏事；关注趋势）

## 四、连接器 v1 操作速查（管理员）

```
POST /connectors                      登记配置（id/url/auth.token_env/pagination/target_table）
POST /connectors/{id}/sync            手动同步 -> stg_ 表；未确认映射时返回推断建议
POST /connectors/{id}/confirm         人工确认字段映射（FR-2 闭环人工半环）
GET  /connectors                      列表 + 最近状态（配置脱敏回显）
DELETE /connectors/{id}               删除配置
GET  /audit/recent?limit=200          豁免审计巡检
```

安全约定：凭据只放环境变量（配置仅存 `token_env` 名）；同步行数须 >0（空拉取视为异常拒刷）；
失败自动重试（指数退避）耗尽后写告警 JSONL 并在状态中标记 error；OAuth / 增量同步留二期。

## 五、红线（试点期不变）

- 不做导出、不做写操作；任何「先取全量再过滤」的实现都是缺陷（W7 教训）
- 权限改动必须走「权威 YAML → 实现 → 断言 → 文档」四步，先红后绿
- `docs/adr/OPEN-DECISIONS.md` 当前 D1–D5 全部收口；新裁决先立项再动代码
