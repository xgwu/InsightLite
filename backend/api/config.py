# -*- coding: utf-8 -*-
"""W7 API · 配置（零外部依赖：环境变量 + 常量）。

SSO_ROLE_MAP（2026-09-19 发起人裁决定版，见 docs/adr/OPEN-DECISIONS.md 已收口）：
  sso_mock 签发角色 = employee / manager / hrbp / exec / auditor
  权限引擎策略角色 = employee / dept_manager / hr_admin / auditor（YAML 权威）
  映射表是两者的唯一缝合点：
    - 未登记的 SSO 角色 -> 403（fail-closed，绝不默认放行）
    - manager->dept_manager / hrbp->hr_admin / exec->hr_admin（裁决确认）
    - auditor->auditor：scope=none，仅可访问纯 public 引用查询（裁决确认）
"""
import os
import sys

CORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")
if CORE not in sys.path:
    sys.path.insert(0, CORE)

# SSO 角色 -> 策略角色（2026-09-19 裁决定版）
SSO_ROLE_MAP = {
    "employee": "employee",
    "manager": "dept_manager",
    "hrbp": "hr_admin",
    "exec": "hr_admin",          # 裁决确认：exec 可见全员薪酬
    "auditor": "auditor",        # 裁决确认：scope=none，仅 public 档可看
}

# 环境配置
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")          # /ask 需要；缺省 503
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
API_READ_ONLY = os.environ.get("API_READ_ONLY", "1") == "1"   # 当前只有只读查询


def policy_role(sso_role):
    """SSO 角色 -> 策略角色；未登记 -> None（fail-closed）。"""
    return SSO_ROLE_MAP.get(sso_role)
