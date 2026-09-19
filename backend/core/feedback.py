# -*- coding: utf-8 -*-
"""W11 · stakeholder 纠正反馈闭环（dev-plan W11–12：纠正 → eval case 闭环）。

闭环纪律（PRD 8.2 / dev-plan 第 4 节）：
  任何用户对一次问答提出纠正 -> 登记（open）-> hr_admin 审核（accept/reject）
  -> accept 自动生成 eval case **骨架**（进 eval/pending_cases.json 待人工补断言）
  -> 骨架转正（进 eval_harness.CASES）后回归纳入口径一致率分母。

为什么骨架不直接转正：断言（has_col / any_filter 等）编码的是「正确口径」，
口径本身必须由人确认——这正是 FR-27 / 连接器映射确认同一条纪律：
机器可以提议，口径必须人工拍板。静默错误率的分子也来自本文件（accepted wrong_number）。

存储：data/feedback.json（单文件 + 原子替换，与 dashboard.json 同款 v1 纪律）。
"""
import datetime as _dt
import json
import os
import threading
import uuid

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORE = os.environ.get("INSIGHTLITE_FEEDBACK_STORE", os.path.join(BASE, "data", "feedback.json"))
PENDING = os.environ.get("INSIGHTLITE_PENDING_CASES",
                         os.path.join(BASE, "eval", "pending_cases.json"))

CATEGORIES = ("wrong_number", "wrong_scope", "should_refuse", "should_answer", "other")
AGING_HOURS = 48                             # 闭环 SLA：48h 内处理（日报点名超时项）
_LOCK = threading.Lock()


def _load() -> list:
    if not os.path.exists(STORE):
        return []
    with open(STORE, encoding="utf-8") as f:
        return json.load(f)


def _save(items: list) -> None:
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)


def submit(question: str, category: str, note: str, ctx: dict,
           answer_status: str = "", smq: dict = None) -> dict:
    """用户登记一条纠正。任何登录用户可提交（auditor 也在内——反馈是权利不是数据面）。"""
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}")
    fb = {
        "id": uuid.uuid4().hex[:12],
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "question": (question or "")[:500],
        "answer_status": answer_status[:40],
        "category": category,
        "note": (note or "")[:500],
        "smq": smq,
        "reporter": {"role": ctx.get("role"), "emp_id": ctx.get("emp_id")},
        "status": "open",                     # open -> accepted / rejected
    }
    with _LOCK:
        items = _load()
        items.append(fb)
        _save(items)
    return fb


def list_items(status: str = None) -> list:
    items = _load()
    if status:
        items = [f for f in items if f["status"] == status]
    return sorted(items, key=lambda f: f["ts"], reverse=True)


def aging_open(hours: int = AGING_HOURS) -> list:
    """登记后超过 SLA 仍未处理的 open 项（日报点名）。"""
    import datetime as _d
    cutoff = _d.datetime.now() - _d.timedelta(hours=hours)
    out = []
    for f in _load():
        if f["status"] != "open":
            continue
        try:
            if _d.datetime.fromisoformat(f["ts"]) < cutoff:
                out.append(f)
        except ValueError:
            out.append(f)
    return out


# category -> eval case 骨架的 expect.type 建议（人工确认后转正）
_SUGGEST = {"wrong_number": "smq", "wrong_scope": "smq",
            "should_refuse": "out_of_scope", "should_answer": "smq", "other": "flexible"}


def review(fid: str, action: str, note: str = "") -> dict:
    """hr_admin 审核。accept -> 生成 eval case 骨架（待人工补断言）。"""
    with _LOCK:
        items = _load()
        fb = next((f for f in items if f["id"] == fid), None)
        if fb is None:
            raise KeyError(fid)
        if action not in ("accept", "reject"):
            raise ValueError("action must be accept/reject")
        fb["status"] = "accepted" if action == "accept" else "rejected"
        fb["reviewed_at"] = _dt.datetime.now().isoformat(timespec="seconds")
        fb["review_note"] = (note or "")[:300]
        _save(items)
    if action != "accept":
        return fb
    skeleton = {
        "id": f"FB-{fb['id']}",
        "q": fb["question"],
        "category": fb["category"],
        "expect_type_suggested": _SUGGEST.get(fb["category"], "flexible"),
        "note": "自动骨架：断言（has_col/any_filter 等）须由人工确认正确口径后补齐并转正进 eval_harness.CASES",
        "feedback_ts": fb["ts"],
        "status": "pending-confirm",
    }
    os.makedirs(os.path.dirname(PENDING), exist_ok=True)
    pend = []
    if os.path.exists(PENDING):
        with open(PENDING, encoding="utf-8") as f:
            pend = json.load(f)
    pend.append(skeleton)
    tmp = PENDING + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pend, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PENDING)
    return {**fb, "eval_case_skeleton": skeleton}


def pending_cases() -> list:
    if not os.path.exists(PENDING):
        return []
    with open(PENDING, encoding="utf-8") as f:
        return json.load(f)


def accepted_wrong_number(days: int = 7) -> int:
    """静默错误率分子：窗口内 accepted 且 category=wrong_number 的条数
    （用户答错且系统未自察——明确的「静默」证据）。"""
    import datetime as _d
    cutoff = _d.datetime.now() - _d.timedelta(days=days)
    n = 0
    for f in _load():
        if f.get("status") != "accepted" or f.get("category") != "wrong_number":
            continue
        try:
            if _d.datetime.fromisoformat(f["ts"]) >= cutoff:
                n += 1
        except ValueError:
            continue
    return n
