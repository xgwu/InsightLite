# -*- coding: utf-8 -*-
"""W7 · 豁免审计（D4 裁决配套，2026-09-19）。

语义：hr_admin 每次豁免查询（verdict.exempt=True）必须强制写入一条审计记录——
谁（主体/角色）、何时、查了什么（SMQ 全文）、决策是什么、模型版本。
append-only JSONL：不覆盖、不改写；文件按月滚动（audit-YYYYMM.jsonl）。

调用方责任：render.ask() 与 API /query /ask 在拿到 ALLOW/MASK verdict 后调用
record_if_exempt()——豁免语义与审计落盘是同一条纪律的两半，缺一即回到
「无豁免语义的静默提权」。写入失败显式抛错（审计失败 = 查询失败，不做静默降级）。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
AUDIT_DIR = os.environ.get("INSIGHTLITE_AUDIT_DIR",
                           os.path.join(BASE, "data", "audit"))


def audit_path(ts=None):
    """按月滚动的审计文件路径（可注入 ts 便于测试）。"""
    import datetime as _dt
    ts = ts or _dt.datetime.now()
    return os.path.join(AUDIT_DIR, f"audit-{ts:%Y%m}.jsonl")


def record_if_exempt(verdict: dict, ctx: dict, smq: dict, source: str = "api",
                     path: str = None) -> bool:
    """verdict 带豁免标记 -> 写审计并返回 True；否则不写返回 False。
    写失败显式抛 OSError（fail-closed：审计不可用则豁免查询不可用）。"""
    if not verdict.get("exempt"):
        return False
    import datetime as _dt
    entry = {
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "source": source,                     # api-query / api-ask / render-ask
        "actor": {"emp_id": ctx.get("emp_id"), "role": ctx.get("role"),
                  "dept_id": ctx.get("dept_id")},
        "smq": smq,
        "decision": verdict.get("decision"),
        "reason": verdict.get("reason"),
        "model_version": verdict.get("model_version"),
    }
    p = path or audit_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:   # append-only：不覆盖既有记录
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return True


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"audit module ok -> {audit_path()}")
