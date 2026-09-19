# -*- coding: utf-8 -*-
"""
InsightLite · 核心域包（backend/core）
Sprint 0 资产平移而来（原 chatbi-lite-sprint0/scripts，2026-09-19 迁移），
模块间保持平铺 import；W7 建 FastAPI 骨架时再做正式包化与分层重构。

模块清单（迁移后正名）：
  dsl_loader   语义模型 YAML -> 内存注册表（产品心脏的装载器）
  smq_schema   SMQ 契约与校验（LLM 输出的唯一合法形态）
  compiler     SMQ -> SQL 确定性编译器（原名 spike_sqlglot，sqlglot AST）
  perm_engine  权限三态引擎（ALLOW / MASK / DENY，fail-closed）
  render       渲染与对话层，ask() 五环节唯一入口
  llm_intent   LLM 意图解析（DeepSeek/OpenAI 兼容，含提示词契约；原名 spike_llm_intent）
  sso_mock     模拟 SSO：5 角色 JWT 签发与校验（原名 spike_sso_mock）
  e2e_live     端到端实跑脚本（需 DEEPSEEK_API_KEY）
"""
