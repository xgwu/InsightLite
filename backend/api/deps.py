# -*- coding: utf-8 -*-
"""W7 API · 鉴权依赖：JWT 验证 -> 策略 ctx（fail-closed 全链路）。

fail-closed 语义（对应权限矩阵 B 段）：
  无 token / 篡改 / 错钥伪造 / 过期 / 白名单外角色 / 未登记映射 -> 401 或 403
  绝不默认放行；验证端接口与 mock SSO 一致，替换真实 SSO 只换签发方。
"""
from fastapi import Header, HTTPException

import sso_mock
from config import policy_role


def build_ctx(authorization: str = Header(default="")):
    """Authorization: Bearer <jwt> -> 权限引擎 securityContext。
    任何失败都是显式 401/403，不产出降级 ctx。"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[len("Bearer "):]
    claims = sso_mock.verify(token)                 # 篡改/伪造/过期 -> None
    if claims is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    sso_role = claims.get("role")
    prole = policy_role(sso_role)                   # 未登记映射 -> 403
    if prole is None:
        raise HTTPException(status_code=403, detail=f"unmapped sso role: {sso_role!r}")
    ctx = {"role": prole}
    # 主体上下文透传：employee 行级策略需要 emp_id；dept_manager 需要 dept_id
    if claims.get("sub") is not None:
        try:
            ctx["emp_id"] = int(claims["sub"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=403, detail="token sub is not an emp id")
    if claims.get("dept_id") is not None:
        ctx["dept_id"] = claims["dept_id"]
    return ctx
