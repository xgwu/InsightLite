# -*- coding: utf-8 -*-
"""W7/W8 API · FastAPI 主入口。

路由：
  GET  /healthz      存活 + 模型版本
  GET  /datasets     语义模型目录（字段/敏感度/聚合白名单——客户端提示与契约发现）
  POST /query        确定性 SMQ 路径：validate -> decide -> compile(策略产物注入) -> execute -> render
  POST /ask          NL 对话路径（一次性 JSON；需 LLM_API_KEY，缺省 503）
  POST /ask/stream   NL 对话 SSE 路径（W8：intent -> permission -> execute -> render 分阶段事件）
  POST /dev/login    mock SSO 签发（Sprint 0 缝线；真实 SSO 上线后整体删除）

设计要点（技术基线对齐）：
  - 授权先于检索：decide() 在 compile/execute 之前；DENY 短路为 403/主流程，不触库
  - 编译期注入：MASK/行级条件经 verdict.policy 传给 compile_smq，本层只消费不自判
  - 执行核心唯一：/query 与 /ask/stream 共用 _execute()（同一代码路径，无平行实现）
  - D4：豁免 verdict 经 audit.record_if_exempt 逐次落盘（ask 与 query 都接线）
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(os.path.dirname(HERE), "core")
for p in (CORE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import sqlite3  # noqa: E402

from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402

import audit as A  # noqa: E402
import compiler as C  # noqa: E402
import dsl_loader  # noqa: E402
import perm_engine as P  # noqa: E402
import render as R  # noqa: E402
import sso_mock  # noqa: E402
import smq_schema as S  # noqa: E402
from config import LLM_API_KEY  # noqa: E402
from deps import build_ctx  # noqa: E402
from schemas import AskRequest, DevLoginRequest, QueryRequest, QueryResponse  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(CORE)), "data", "hr_demo.db")

app = FastAPI(title="InsightLite API", version="0.2.0-w8",
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


# ---------------------------------------------------------------- 执行核心（唯一代码路径）
def _execute(smq: dict, ctx: dict, conn: sqlite3.Connection, source: str) -> dict:
    """SMQ -> 权限 -> 编译 -> 执行 -> 渲染。/query 与 /ask/stream 共用。
    返回 QueryResponse 形状的 dict；权限 DENY 不抛错（返回 status=denied）。"""
    S.validate(smq)
    verdict = P.decide(smq, ctx, conn=conn)                       # 授权先于检索
    A.record_if_exempt(verdict, ctx, smq, source=source)          # D4: 豁免逐次审计
    if verdict["decision"] == "DENY":
        return {"status": "denied", "decision": "DENY",
                "reason": verdict.get("reason", "")}
    if smq.get("compare_to"):
        return R._answer_compare(smq, conn, verdict)              # 环比双查询路径
    sql, _ast = C.compile_smq(smq, policy=verdict.get("policy"))  # 消费策略产物
    lite = __import__("sqlglot").transpile(sql, read="postgres", write="sqlite")[0]
    rows = conn.execute(lite).fetchall()
    total = len(rows)
    shown = min(total, 20)
    # 结构化数据（W8 图表）：中文列名表头 + 最多 100 行
    data = {"columns": [R._col_header(smq["dataset"], c) for c in smq["columns"]],
            "rows": [list(r) for r in rows[:100]]}
    text = R.render_result(smq, rows[:shown], total, shown, verdict)
    return {"status": "answered", "decision": verdict["decision"],
            "reason": verdict.get("reason", ""), "text": text, "sql": sql,
            "smq": smq, "total": total, "data": data}


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest, ctx: dict = Depends(build_ctx)):
    """确定性查询路径（CI 可回归）：完整走 权限->编译(注入)->执行->渲染。
    决策层 DENY -> denied（授权先于检索）；MASK/ALLOW -> 执行并渲染。"""
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            result = _execute(req.smq, ctx, conn, source="api-query")
        finally:
            conn.close()
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"invalid SMQ: {e}")
    return QueryResponse(**result)


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
                         sql=result.get("sql"), smq=result.get("smq"),
                         data=result.get("data"))


# ---------------------------------------------------------------- W8: SSE 对话流
def sse_events(question: str, ctx: dict, api_key: str, intent_fn=None,
               conn: sqlite3.Connection = None):
    """对话管线 SSE 事件生成器：stage 事件（intent/permission/execute）-> final 事件。
    intent_fn 可注入（离线测试：绕过真实 LLM，其余管线与线上一致——同代码路径纪律）。
    事件格式：`data: {json}\\n\\n`；final 事件携带 QueryResponse 形状的完整结果。"""
    def fmt(obj) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    own_conn = conn is None
    c = conn or sqlite3.connect(DB_PATH)
    try:
        yield fmt({"stage": "intent"})
        if intent_fn is None:
            try:
                import orchestrator as O
                intent_fn = lambda q: O.interpret(q, api_key)          # D3 路径
            except ImportError:                                        # 框架缺失 -> 旧路径
                import llm_intent as L
                intent_fn = lambda q: R._interpret(L.call_deepseek(q, api_key))
        kind, payload = intent_fn(question)
        if kind == "refused_scope":
            yield fmt({"stage": "final", "status": "refused_scope",
                       "text": f"该问题超出了当前数据范围，无法回答：{payload}"})
            return
        if kind == "clarify":
            opts = "".join(f"\n- {o}" for o in payload["options"])
            yield fmt({"stage": "final", "status": "clarify",
                       "text": f"为了给您准确的答案，请先确认：{payload['question']}{opts}"})
            return
        if kind == "invalid":
            yield fmt({"stage": "final", "status": "error",
                       "text": f"查询失败（意图解析异常）：{payload}"})
            return
        smq = payload
        yield fmt({"stage": "permission", "smq": smq})                 # 事件附 SMQ：前端可展示口径
        try:
            result = _execute(smq, ctx, c, source="api-ask")
        except Exception as e:
            yield fmt({"stage": "final", "status": "error",
                       "text": f"查询失败（{type(e).__name__}）：{str(e)[:150]}"})
            return
        if result.get("status") != "denied":                           # denied 未触库：不发 execute 事件
            yield fmt({"stage": "execute"})
        yield fmt({"stage": "final", **result})
    finally:
        if own_conn:
            c.close()


@app.post("/ask/stream")
def ask_stream(req: AskRequest, ctx: dict = Depends(build_ctx)):
    """SSE 对话路径（W8）：未配置 LLM_API_KEY -> 503（流开始前显式失败）。"""
    if not LLM_API_KEY:
        raise HTTPException(status_code=503,
                            detail="LLM_API_KEY not configured; /ask/stream unavailable")
    return StreamingResponse(sse_events(req.question, ctx, LLM_API_KEY),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- W8: mock SSO 签发
DEV_IDENTITY = {          # 演示身份（sso_mock 不校验员工存在性；仅注入行级上下文）
    "employee": ("1001", 6, [1, 2, 6]),
    "manager":  ("2001", 2, [1, 2]),
    "hrbp":     ("9001", 1, [1]),
    "exec":     ("9002", 1, [1]),
    "auditor":  ("9003", 1, [1]),
}


@app.post("/dev/login")
def dev_login(req: DevLoginRequest):
    """mock SSO 签发（Sprint 0 缝线）：按角色签发 JWT；白名单外角色 403（fail-closed）。"""
    if req.role not in sso_mock.ROLES:
        raise HTTPException(status_code=403, detail=f"unknown sso role: {req.role!r}")
    emp, dept, path = DEV_IDENTITY[req.role]
    return {"token": sso_mock.issue(emp, req.role, dept, path), "role": req.role}
