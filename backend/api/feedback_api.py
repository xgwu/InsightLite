# -*- coding: utf-8 -*-
"""W11 · 反馈闭环 API 路由（stakeholder 纠正 -> eval case 闭环的产品化入口）。

权限语义（fail-closed，与全项目一致）：
  POST /feedback            任何已登录用户（反馈是权利；auditor 也可提——反馈不是数据面）
  GET  /feedback            仅 hr_admin（反馈内容可能引用敏感口径的 SMQ）
  POST /feedback/{id}/review 仅 hr_admin；accept 自动生成 eval case 骨架
"""
import sys

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import feedback as FB
from deps import build_ctx

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    category: str
    note: str = Field("", max_length=500)
    answer_status: str = ""
    smq: dict = None


class ReviewIn(BaseModel):
    action: str                                # accept / reject
    note: str = Field("", max_length=300)


@router.post("")
def submit_feedback(req: FeedbackIn, ctx: dict = Depends(build_ctx)):
    try:
        fb = FB.submit(req.question, req.category, req.note, ctx,
                       answer_status=req.answer_status, smq=req.smq)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"feedback": fb}


@router.get("")
def list_feedback(status: str = None, ctx: dict = Depends(build_ctx)):
    if ctx.get("role") != "hr_admin":
        raise HTTPException(status_code=403, detail="feedback review requires hr_admin role")
    if status and status not in ("open", "accepted", "rejected"):
        raise HTTPException(status_code=422, detail="status must be open/accepted/rejected")
    items = FB.list_items(status)
    aging = len(FB.aging_open())
    return {"items": items, "open_aging_over_48h": aging,
            "pending_eval_cases": len(FB.pending_cases())}


@router.post("/{fid}/review")
def review_feedback(fid: str, req: ReviewIn, ctx: dict = Depends(build_ctx)):
    if ctx.get("role") != "hr_admin":
        raise HTTPException(status_code=403, detail="feedback review requires hr_admin role")
    try:
        result = FB.review(fid, req.action, req.note)
    except KeyError:
        raise HTTPException(status_code=404, detail="feedback not found")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"feedback": result}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("feedback router ok")
