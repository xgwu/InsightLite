# -*- coding: utf-8 -*-
"""W7 API · 配置（零外部依赖：环境变量 + 常量）。

SSO_ROLE_MAP 的冲突背景（tests/test_perm_matrix.py 冲突报告 [C1]）：
  sso_mock 签发角色 = employee / manager / hrbp / exec（Sprint 0 五角色遗留命名）
  权限引擎策略角色 = employee / dept_manager / hr_admin / auditor（YAML 权威）
  映射表是两者的唯一缝合点：
    - 未登记的 SSO 角色 -> 403（fail-closed，绝不默认放行）
    - manager->dept_manager / hrbp->hr_admin 为明显对位
    - exec->hr_admin 为暂定值（exec 可见全员薪酬属薪酬敏感面）——待发起人
      确认后写入 docs/adr（ADR-D5）；裁决变更只改本表，引擎与矩阵不动。
"""
import os
import sys

CORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")
if CORE not in sys.path:
    sys.path.insert(0, CORE)

# SSO 角色 -> 策略角色（暂定映射，见上）
SSO_ROLE_MAP = {
    "employee": "employee",
    "manager": "dept_manager",
    "hrbp": "hr_admin",
    "exec": "hr_admin",          # 暂定，待裁决（ADR-D5）
}

# 环境配置
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")          # /ask 需要；缺省 503
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
API_READ_ONLY = os.environ.get("API_READ_ONLY", "1") == "1"   # 当前只有只读查询


def policy_role(sso_role):
    """SSO 角色 -> 策略角色；未登记 -> None（fail-closed）。"""
    return SSO_ROLE_MAP.get(sso_role)
