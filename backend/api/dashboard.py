# -*- coding: utf-8 -*-
"""W9 · 看板 API（FR-24~29 一期子集）。

核心安全语义（PRD FR-33「同一看板不同角色打开范围不同」的落地方式）：
  看板只存「口径」（SMQ + 标题 + 图型），**不存数据**。
  读取时对每个 widget 经 _execute() 以当前请求者身份重新执行——
  权限在读取时重新裁定（decide -> compile(policy 注入) -> execute），
  manager 看到行级收窄、employee 看到本人行、auditor 看到拒绝标签。
  看板因此永远不可能成为绕过权限引擎的旁路（复用唯一执行核心，无平行实现）。

存储：data/dashboard.json（v1 单文件；一期试点 30 人规模足够，多用户并发写入
  冲突可接受——数据不入库、可随时重建，gitignore 与种子库同款纪律）。
"""
import json
import os
import sqlite3
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import smq_schema as S
from deps import build_ctx
from main import DB_PATH, _execute

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

STORE = os.environ.get("INSIGHTLITE_DASHBOARD_STORE", os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "dashboard.json"))
MAX_WIDGETS = 50                            # W13 稳定性：看板容量上限（防无界增长拖垮读取）
_LOCK = threading.Lock()                     # 进程内串行化（v1 单机足够）

CHARTS = ("bar", "line", "pie", "table")


def _load() -> list:
    if not os.path.exists(STORE):
        return []
    with open(STORE, encoding="utf-8") as f:
        return json.load(f)


def _save(widgets: list) -> None:
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(widgets, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)                   # 原子替换：半写状态不可见


class WidgetIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=60)
    smq: dict
    chart: str = "bar"


class LayoutIn(BaseModel):
    layouts: list                            # [{id, x, y, w, h}]


@router.post("")
def add_widget(req: WidgetIn, ctx: dict = Depends(build_ctx)):
    """一键固化（FR-24）：对话结果带完整口径入看板。入板即校验 SMQ（fail early），
    权限不在入板时裁定——读取时按角色重裁（入板者可见 ≠ 消费者可见）。"""
    if req.chart not in CHARTS:
        raise HTTPException(status_code=422, detail=f"chart must be one of {CHARTS}")
    try:
        S.validate(req.smq)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"invalid SMQ: {e}")
    with _LOCK:
        widgets = _load()
        if len(widgets) >= MAX_WIDGETS:
            raise HTTPException(status_code=409,
                                detail=f"dashboard is full ({MAX_WIDGETS} widgets); "
                                       f"please clean up before adding")
        w = {"id": uuid.uuid4().hex[:12], "title": req.title.strip(),
             "smq": req.smq, "chart": req.chart,
             "layout": {"x": 0, "y": 0, "w": 6, "h": 8},
             "created_by": ctx.get("role"), "created_at": int(time.time())}
        widgets.append(w)
        _save(widgets)
    return {"widget": w}


@router.get("")
def list_widgets(ctx: dict = Depends(build_ctx)):
    """看板首屏（FR-25/33）：每个 widget 以当前请求者身份重新执行（权限读取时重裁）。
    DENY 的 widget 保留卡片并显示拒绝原因——「存在但不可见数据」比静默消失更诚实，
    也给管理员清理无权看板的线索。"""
    widgets = _load()
    conn = sqlite3.connect(DB_PATH)
    out = []
    try:
        for w in widgets:
            item = dict(w)
            try:
                result = _execute(w["smq"], ctx, conn, source="dashboard-read")
            except Exception as e:
                result = {"status": "error", "text": f"查询失败（{type(e).__name__}）"}
            item["result"] = result
            out.append(item)
    finally:
        conn.close()
    return {"widgets": out, "model_version": _load_version()}


def _load_version():
    import dsl_loader
    return dsl_loader.REG.version


@router.delete("/{widget_id}")
def delete_widget(widget_id: str, ctx: dict = Depends(build_ctx)):
    """删除：创建者同角色或 HR 线（hrbp/exec/hr_admin）可删；其余 403（fail-closed）。"""
    allowed = {"hrbp", "exec", "hr_admin"}
    with _LOCK:
        widgets = _load()
        target = next((w for w in widgets if w["id"] == widget_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="widget not found")
        if target.get("created_by") != ctx.get("role") and ctx.get("role") not in allowed:
            raise HTTPException(status_code=403, detail="not allowed to delete this widget")
        widgets = [w for w in widgets if w["id"] != widget_id]
        _save(widgets)
    return {"deleted": widget_id}


@router.put("/layout")
def save_layout(req: LayoutIn, ctx: dict = Depends(build_ctx)):
    """布局持久化（FR-25）：前端拖拽后批量回存 {id -> x/y/w/h}。布局是视图偏好，
    不含口径，因此不做角色区分。"""
    with _LOCK:
        widgets = _load()
        index = {w["id"]: w for w in widgets}
        for l in req.layouts:
            w = index.get(l.get("id"))
            if w is None:
                continue
            w["layout"] = {k: int(l[k]) for k in ("x", "y", "w", "h") if k in l}
        _save(widgets)
    return {"saved": len(req.layouts)}
