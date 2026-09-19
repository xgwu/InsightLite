# -*- coding: utf-8 -*-
"""InsightLite API 层（W7）：FastAPI 骨架。

模块定位（技术基线 chatbi-lite-dev-kickoff-tech-design.html）：
  - 鉴权：JWT（mock SSO 验证端，替换真实 SSO 只换签发方）
  - 角色：SSO 角色名 -> 策略角色名映射（fail-closed，未登记一律 403）
  - 查询：/query 确定性 SMQ 路径（无 LLM，CI 可回归）
         /ask   NL 对话路径（六状态，需 LLM_API_KEY）

当前为平铺 import（backend/core 以 sys.path 接入）——与 tests/ 一致；
FastAPI 包化在 W8+ 与 SQLModel/依赖注入一起做（技术基线 D2 备选路径）。
"""
