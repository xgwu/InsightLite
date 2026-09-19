# -*- coding: utf-8 -*-
"""W7 API · 请求/响应模型（Pydantic v2）。"""
from typing import Any, Optional

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """确定性 SMQ 查询（LLM 产物契约，直接进 /query 即可绕过 NL 解析）。"""
    smq: dict = Field(..., description="SMQ 请求对象（dataset/columns/filter/...）")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


class DecisionInfo(BaseModel):
    decision: str
    reason: str = ""
    model_version: str = ""


class QueryResponse(BaseModel):
    status: str                       # answered / denied / error
    decision: Optional[str] = None    # ALLOW / MASK / DENY
    reason: str = ""
    text: str = ""
    sql: Optional[str] = None
    smq: Optional[dict] = None
    total: Optional[int] = None


class DatasetsResponse(BaseModel):
    model_version: str
    datasets: dict[str, Any]
