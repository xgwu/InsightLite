# -*- coding: utf-8 -*-
"""W7 API · 请求/响应模型（Pydantic v2）。"""
from typing import Any, Optional

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """确定性 SMQ 查询（LLM 产物契约，直接进 /query 即可绕过 NL 解析）。"""
    smq: dict = Field(..., description="SMQ 请求对象（dataset/columns/filter/...）")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


class DevLoginRequest(BaseModel):
    """mock SSO 签发请求（Sprint 0 缝线，W8）。"""
    role: str = Field(..., description="sso_mock 角色：employee/manager/hrbp/exec/auditor")


class DecisionInfo(BaseModel):
    decision: str
    reason: str = ""
    model_version: str = ""


class QueryResponse(BaseModel):
    status: str                       # answered / denied / refused_scope / clarify / error
    decision: Optional[str] = None    # ALLOW / MASK / DENY
    reason: str = ""
    text: str = ""
    sql: Optional[str] = None
    smq: Optional[dict] = None
    total: Optional[int] = None
    data: Optional[dict] = None       # 结构化结果 {"columns": [...], "rows": [[...]]}（W8 图表用）


class DatasetsResponse(BaseModel):
    model_version: str
    datasets: dict[str, Any]
