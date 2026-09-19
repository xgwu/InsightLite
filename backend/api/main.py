# -*- coding: utf-8 -*-
"""W7 API · FastAPI 骨架主入口。

路由：
  GET  /healthz    存活 + 模型版本
  GET  /datasets   语义模型目录（字段/敏感度/聚合白名单——客户端提示与契约发现）
  POST /query      确定性 SMQ 路径：validate -> decide -> compile(策略产物注入) -> execute -> render
  POST /ask        NL 对话路径（六状态；需 LLM_API_KEY，缺省 503）

设计要点（技术基线对齐）：
  - 授权先于检索：decide() 在 compile/execute 之前；DENY 短路为 403，不触库
  - 编译期注入：MASK/行级条件经 verdict.policy 传给 compile_smq，本层只消费不自判
  - LLMProvider 抽象留待 W8（R6 防锁定）：/ask 当前直连 llm_intent.call_deepseek
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(os.path.dirname(HERE), "core")
for p in (CORE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import sqlite3  # noqa: E402

from fastapi import Depends, FastAPI, HTTPException  # noqa: E402

import compiler as C  # noqa: E402
import dsl_loader  # noqa: E402
import perm_engine as P  # noqa: E402
import render as R  # noqa: E402
import smq_schema as S  # noqa: E402
from config import LLM_API_KEY  # noqa: E402
from deps import build_ctx  # noqa: E402
from schemas import AskRequest, QueryRequest, QueryResponse  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(CORE)), "data", "hr_demo.db")

app = FastAPI(title="InsightLite API", version="0.1.0-w7",
              description="轻量级 ChatBI：语义 YAML + SMQ 确定性编译 + 三态权限策略引擎")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "model_version": dsl_loader.REG.version}


@app.get("/datasets")
def datasets():
    """语义模型目录：字段元数据 + 敏感度 + 聚合白名单（契约发现）。"""
    out = {}
    for ds in dsl_loader.REG.tables:
        fields = {}
        for f, meta in (dsl_loader.REG.own[ds] or {}).items():
            fields[f] = {"display_name": meta.get("display_name"),
                         "kind": meta.get("kind"),
                         "sensitivity": meta.get("sensitivity", "internal"),
                         "allowed_aggs": meta.get("allowed_aggs", [])}
        for f, meta in (dsl_loader.REG.inherited[ds] or {}).items():
            fields.setdefault(f, {"display_name": meta.get("display_name"),
                                  "kind": meta.get("kind"),
                                  "sensitivity": meta.get("sensitivity", "internal"),
                                  "allowed_aggs": meta.get("allowed_aggs", []),
                                  "inherited": True})
        out[ds] = {"table": dsl_loader.REG.tables[ds], "fields": fields}
    return {"model_version": dsl_loader.REG.version, "datasets": out}


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest, ctx: dict = Depends(build_ctx)):
    """确定性查询路径（CI 可回归）：完整走 权限->编译(注入)->执行->渲染。
    决策层 DENY -> 403（授权先于检索）；MASK/ALLOW -> 执行并渲染。"""
    try:
        S.validate(req.smq)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"invalid SMQ: {e}")
    conn = sqlite3.connect(DB_PATH)
    try:
        verdict = P.decide(req.smq, ctx, conn=conn)          # 授权先于检索
        import audit as A
        A.record_if_exempt(verdict, ctx, req.smq, source="api-query")   # D4: 豁免逐次审计
        if verdict["decision"] == "DENY":
            return QueryResponse(status="denied", decision="DENY",
                                 reason=verdict.get("reason", ""))
        sql, _ast = C.compile_smq(req.smq, policy=verdict.get("policy"))
        import sqlglot
        lite = sqlglot.transpile(sql, read="postgres", write="sqlite")[0]
        rows = conn.execute(lite).fetchall()
        total = len(rows)
        shown = min(total, 20)
        text = R.render_result(req.smq, rows[:shown], total, shown, verdict)
        return QueryResponse(status="answered", decision=verdict["decision"],
                             reason=verdict.get("reason", ""), text=text,
                             sql=sql, smq=req.smq, total=total)
    finally:
        conn.close()


@app.post("/ask", response_model=QueryResponse)
def ask(req: AskRequest, ctx: dict = Depends(build_ctx)):
    """NL 对话路径：六状态（answered/refused_scope/clarify/denied/error）。
    未配置 LLM_API_KEY -> 503（显式失败，不做降级假回答）。"""
    if not LLM_API_KEY:
        raise HTTPException(status_code=503,
                            detail="LLM_API_KEY not configured; /ask unavailable "
                                   "(use /query for deterministic SMQ path)")
    result = R.ask(req.question, LLM_API_KEY, ctx)           # ask 内部：授权先于检索
    return QueryResponse(status=result.get("status", "error"),
                         reason=result.get("text", "")[:200] if result.get("status") == "denied" else "",
                         text=result.get("text", ""),
                         sql=result.get("sql"), smq=result.get("smq"))
