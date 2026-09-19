# -*- coding: utf-8 -*-
"""W10 · 连接器管理 API（dev-plan W10：轻量 REST 拉取连接器 v1 的产品化入口）。

权限语义（fail-closed）：
  连接器管理 = 数据边界操作，仅 hr_admin（hrbp/exec 经映射成为 hr_admin）可操作；
  employee / dept_manager / auditor 一律 403。auditor 即便只读也被拒——
  连接器配置含上游系统 URL 与鉴权方式，属 scope=none 之外的数据面资产。

字段映射人工确认流（FR-2 推断链路）：
  POST /connectors                       登记配置（校验 fail-closed）
  POST /connectors/{id}/sync             手动同步 -> staging；未确认时响应带推断建议
  POST /connectors/{id}/confirm          人工确认字段映射（冻结 mapping + confirmed）
  GET  /connectors                       列表（配置脱敏回显 + 最近状态）
  DELETE /connectors/{id}                删除配置（staging 表保留由运维清理）
"""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from main import DB_PATH

import connector as CC
from deps import build_ctx

router = APIRouter(prefix="/connectors", tags=["connectors"])


def _require_admin(ctx: dict):
    if ctx.get("role") != "hr_admin":
        raise HTTPException(status_code=403,
                            detail="connector management requires hr_admin role")


class ConnectorIn(BaseModel):
    id: str
    name: str = ""
    url: str
    auth: dict = {}
    params: dict = {}
    data_path: str = "data"
    pagination: dict = {"type": "none"}
    field_mapping: dict = {}
    target_table: str
    schedule_minutes: int = 0          # 0 = 仅手动；>0 = 定时间隔（分钟）
    enabled: bool = True


class ConfirmIn(BaseModel):
    field_mapping: dict


@router.get("")
def list_connectors(ctx: dict = Depends(build_ctx)):
    _require_admin(ctx)
    state = CC.load_state()
    return {"connectors": [
        {**CC.redact(c), "state": state.get(c["id"], {})} for c in CC.load_configs()
    ]}


@router.post("")
def create_connector(req: ConnectorIn, ctx: dict = Depends(build_ctx)):
    _require_admin(ctx)
    cfg = req.model_dump()
    cfg["mapping_confirmed"] = bool(cfg.get("field_mapping"))
    err = CC.validate_config(cfg)
    if err:
        raise HTTPException(status_code=422, detail=err)
    cfgs = CC.load_configs()
    if any(c["id"] == cfg["id"] for c in cfgs):
        raise HTTPException(status_code=409, detail=f"connector id exists: {cfg['id']}")
    cfgs.append(cfg)
    CC.save_configs(cfgs)
    return {"connector": CC.redact(cfg)}


@router.post("/{cid}/sync")
def sync_connector(cid: str, ctx: dict = Depends(build_ctx)):
    """手动触发一次同步。未确认映射 -> 落 staging + 返回推断建议（人工确认输入）。"""
    _require_admin(ctx)
    cfg = next((c for c in CC.load_configs() if c["id"] == cid), None)
    if cfg is None:
        raise HTTPException(status_code=404, detail="connector not found")
    conn = sqlite3.connect(DB_PATH)
    try:
        return CC.sync(cfg, conn)
    except Exception as e:                    # noqa: sync 已完成告警+状态落盘
        raise HTTPException(status_code=502, detail=str(e)[:200])
    finally:
        conn.close()


@router.post("/{cid}/confirm")
def confirm_mapping(cid: str, req: ConfirmIn, ctx: dict = Depends(build_ctx)):
    """人工确认字段映射（FR-2 闭环的人工半环）：冻结 mapping + confirmed=true。"""
    _require_admin(ctx)
    cfgs = CC.load_configs()
    cfg = next((c for c in cfgs if c["id"] == cid), None)
    if cfg is None:
        raise HTTPException(status_code=404, detail="connector not found")
    for k, v in req.field_mapping.items():
        import re as _re
        if not _re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(v)):
            raise HTTPException(status_code=422, detail=f"目标列名不合法: {v!r}")
    cfg["field_mapping"] = req.field_mapping
    cfg["mapping_confirmed"] = True
    CC.save_configs(cfgs)
    return {"connector": CC.redact(cfg)}


@router.delete("/{cid}")
def delete_connector(cid: str, ctx: dict = Depends(build_ctx)):
    _require_admin(ctx)
    cfgs = CC.load_configs()
    if not any(c["id"] == cid for c in cfgs):
        raise HTTPException(status_code=404, detail="connector not found")
    CC.save_configs([c for c in cfgs if c["id"] != cid])
    return {"deleted": cid}
