# Sprint 0 资产迁移记录（2026-09-19）

- 来源：`github.com/xgwu/InsightLite`（Initial import 24eba28）→ `chatbi-lite-sprint0/`
- 去向：本仓库（InsightLite 新工程结构，对接《开发启动技术基线》第 6 节目录设计）
- 迁移方式：机械平移 + 正名 + 路径适配，**不重写业务逻辑**；迁移后 `python run_all.py` 全绿。

## 迁移映射表

| 原文件（sprint0/scripts 等） | 新位置 | 改动 |
|---|---|---|
| semantic_model/hr_domain_v1.yaml | semantic_model/hr_domain_v1.yaml | 无（W3 已评审收口版本） |
| scripts/dsl_loader.py | backend/core/dsl_loader.py | YAML_PATH 上探一级（scripts→core 深一层） |
| scripts/smq_schema.py | backend/core/smq_schema.py | 无 |
| scripts/perm_engine.py | backend/core/perm_engine.py | 无 |
| **scripts/spike_sqlglot.py** | **backend/core/compiler.py** | **正名**：它早已是正式编译器（render/eval/test 三方 import），BASE 上探一级 |
| scripts/render.py | backend/core/render.py | import 正名；BASE 上探一级 |
| **scripts/spike_llm_intent.py** | **backend/core/llm_intent.py** | **正名**：LLM 意图层 + 提示词契约；.env 探测路径上探一级 |
| **scripts/spike_sso_mock.py** | **backend/core/sso_mock.py** | **正名**：模拟 SSO（5 角色 JWT，fail-closed 用例） |
| scripts/e2e_live.py | backend/core/e2e_live.py | import 正名；报错文案去旧目录名 |
| scripts/test_dsl_v1.py | tests/test_dsl_v1.py | sys.path 指向 backend/core |
| scripts/eval_harness.py | eval/eval_harness.py | sys.path 指向 backend/core（fixtures/DB 相对路径不变） |
| scripts/eval_fixtures.json | eval/eval_fixtures.json | 无 |
| scripts/make_hr_data.py | deploy/seed/make_hr_data.py | 无（BASE=上两级 仍指向仓库根） |
| scripts/run_all.py | run_all.py（根级） | 步骤路径改新布局，输出语 ALL CHECKS PASSED |
| scripts/demo_transcript.py/.json | demo/ | sys.path 指向 backend/core |
| scripts/make_demo_page.py | demo/make_demo_page.py | **留档不接线**：面向旧 chatbi-lite-site 布局，站点工具链不随迁 |
| sql/001_hr_schema_postgres.sql | deploy/seed/001_hr_schema_postgres.sql | 无 |
| .env.example | .env.example | 无 |
| scripts/spike_*.py 之外的 _*.txt/_*.png 过程文件 | 不迁 | 临时调试产物 |
| cube-spike/（Cube 实证环境） | 不迁（留在原 clone） | D1 决策证据，归档价值 > 运行价值；结论已写入 dev-plan ADR |
| chatbi-lite-review/、chatbi-lite-site/、evidence/ | 不迁（留在原 clone） | 调研文档站与竞品实测证据，与运行代码分离 |

## 正名对照（旧 → 新）

- `spike_sqlglot` → `compiler`（确定性编译器，63 用例回归基准不变）
- `spike_llm_intent` → `llm_intent`（提示词契约 + DeepSeek 调用 + fixtures 录制）
- `spike_sso_mock` → `sso_mock`

物理别名纪律不变：SMQ 语义模型的 t/e/d 别名保持 v0 兼容（compiler 回归基准依赖，见 dsl_loader 注释）。

## 迁移后验证

- `python run_all.py`（managed venv：pyyaml + sqlglot + openpyxl）→ **ALL CHECKS PASSED**
- eval harness 离线回放：26/26 PASS；口径一致率基线 **21/21 = 100%**（≥80% 门槛达标）
- 未验证项：`e2e_live.py` 实跑（需 DEEPSEEK_API_KEY，迁移机未配置）；PostgreSQL DDL 未在 PG 实例执行（Sprint 0 用 SQLite 种子库，DDL 面向独立环境 PG 部署）

## 遗留与后续

1. backend/core 保持平铺 import，**W7 建 FastAPI 骨架时包化**（拆 reasoning/semantic/governance 边界，本包整体挪入）。
2. `demo/make_demo_page.py` 若需再生成演示页，需先适配新站点路径或改为输出到 `demo/output/`。
3. 原 clone `insightlite-sprint0/`（工作区）仅作只读参考与 cube-spike 归档，不参与开发。
