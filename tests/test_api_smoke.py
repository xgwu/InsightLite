# -*- coding: utf-8 -*-
"""
W7 API 冒烟（离线，无 LLM Key）：FastAPI /query /ask /datasets /healthz 鉴权与三态行为。
覆盖：
  A1 无 token / 篡改 token -> 401；未登记 SSO 角色 -> 403
  A2 hr_admin 查薪酬 -> 200 ALLOW，数值列非空（正向对照）
  A3 manager 映射 dept_manager：MASK 决策 + 行级收窄（total == 子树 oracle）
  A4 employee R4 指名查他人 -> denied（200 语义体，decision=DENY）
  A5 /ask 无 Key -> 503（显式失败，不做降级假回答）
  A6 非法 SMQ -> 422
"""
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend", "api"))
sys.path.insert(0, os.path.join(BASE, "backend", "core"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient  # noqa: E402

import sso_mock  # noqa: E402
from main import app  # noqa: E402

client = TestClient(app)

# 主体验证
TOK_EMP = sso_mock.issue("1001", "employee", 6, [1, 2, 6])
TOK_MGR = sso_mock.issue("2001", "manager", 2, [1, 2])       # 技术部经理
TOK_ADMIN = sso_mock.issue("9001", "hrbp", 1, [1])           # hrbp -> hr_admin


def seg_a():
    print("== A 鉴权 fail-closed ==")
    # 「已签发但未登记映射 -> 403」：临时摘掉 exec 的映射（fail-closed 语义验证）
    import config
    saved = config.SSO_ROLE_MAP.pop("exec")
    try:
        tok_unmapped = sso_mock.issue("1", "exec", 1, [1])
        r = client.post("/query", headers=_auth(tok_unmapped),
                        json={"smq": {"dataset": "employee", "columns": ["name"]}})
        check("A1 未登记 SSO 角色 -> 403", r.status_code == 403, str(r.status_code))
    finally:
        config.SSO_ROLE_MAP["exec"] = saved

RESULTS = []


def check(name, ok, note=""):
    RESULTS.append((name, bool(ok), note))
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  {note}" if note and not ok else ""))


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def seg_a():
    print("== A 鉴权 fail-closed ==")
    r = client.post("/query", json={"smq": {"dataset": "employee", "columns": ["name"]}})
    check("A1 无 token -> 401", r.status_code == 401, str(r.status_code))
    bad = TOK_EMP[:-6] + "AAAAAA"
    r = client.post("/query", headers=_auth(bad), json={"smq": {"dataset": "employee", "columns": ["name"]}})
    check("A1 篡改 token -> 401", r.status_code == 401, str(r.status_code))

    print("== A 三态行为（决策+数据） ==")
    smq_admin = {"dataset": "payroll", "columns": [{"field": "net_pay", "agg": "avg"}]}
    r = client.post("/query", headers=_auth(TOK_ADMIN), json={"smq": smq_admin})
    body = r.json()
    check("A2 hr_admin avg(net_pay) -> 200 ALLOW 真值",
          r.status_code == 200 and body["decision"] == "ALLOW"
          and body["total"] == 1 and "—" not in body["text"].split("|")[2],
          f"{r.status_code} {body.get('decision')}")

    smq_detail = {"dataset": "payroll", "columns": ["pay_month", "net_pay"],
                  "filter": [{"field": "emp_id", "op": "eq", "value": 1001}]}
    r = client.post("/query", headers=_auth(TOK_MGR), json={"smq": smq_detail})
    body = r.json()
    check("A3 manager->dept_manager 查 net_pay -> MASK",
          r.status_code == 200 and body["decision"] == "MASK", f"{body.get('decision')}")
    check("A3 MASK 下 SQL 含遮蔽表达式", "NULL AS" in (body.get("sql") or ""),
          body.get("sql", "")[:100])

    smq_dept = {"dataset": "attendance", "columns": ["emp_id", "work_date"]}
    r = client.post("/query", headers=_auth(TOK_MGR), json={"smq": smq_dept})
    body = r.json()
    # oracle：部门 2 子树的考勤行数（矩阵 C2 同源；明细查询，total = 行数）
    conn = sqlite3.connect(os.path.join(BASE, "data", "hr_demo.db"))
    oracle = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE emp_id IN "
        "(SELECT id FROM employee WHERE dept_id IN (2,6,7,8))").fetchone()[0]
    oracle_all = conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
    conn.close()
    check("A3 manager 行级收窄 total == 子树 oracle",
          body.get("total") == oracle, f"got {body.get('total')} vs {oracle}")
    check("A3 行级确实收窄（子树 < 全量）", body.get("total", 0) < oracle_all,
          f"{body.get('total')} < {oracle_all}")

    r = client.post("/query", headers=_auth(TOK_EMP),
                    json={"smq": {"dataset": "employee", "columns": ["name"],
                                  "filter": [{"field": "name", "op": "eq", "value": "刘杰"}]}})
    body = r.json()
    check("A4 employee 指名查他人 -> denied(R4)",
          r.status_code == 200 and body["status"] == "denied" and body["decision"] == "DENY",
          f"{r.status_code} {body.get('status')}")

    r = client.post("/query", headers=_auth(TOK_ADMIN),
                    json={"smq": {"dataset": "employee", "columns": ["nonexistent"]}})
    check("A6 非法 SMQ -> 422", r.status_code == 422, str(r.status_code))

    r = client.post("/ask", headers=_auth(TOK_ADMIN), json={"question": "8 月迟到几次"})
    check("A5 /ask 无 Key -> 503", r.status_code == 503, str(r.status_code))

    r = client.get("/healthz")
    check("A7 /healthz 200 + 模型版本", r.status_code == 200 and "model_version" in r.json())
    r = client.get("/datasets")
    check("A7 /datasets 含 payroll.net_pay(critical)",
          r.status_code == 200
          and r.json()["datasets"]["payroll"]["fields"]["net_pay"]["sensitivity"] == "critical")

    print("== A auditor 裁决路径（2026-09-19：public=allow） ==")
    tok_aud = sso_mock.issue("5001", "auditor", 1, [1])      # 裁决后白名单可签发
    r = client.post("/query", headers=_auth(tok_aud),
                    json={"smq": {"dataset": "department", "columns": ["name"]}})
    body = r.json()
    check("A8 auditor department(public) -> 200 ALLOW",
          r.status_code == 200 and body["decision"] == "ALLOW",
          f"{r.status_code} {body.get('decision')} {body.get('reason','')[:60]}")
    check("A8 auditor department 行数 == 全表", body.get("total") == 14,
          f"got {body.get('total')}")
    r = client.post("/query", headers=_auth(tok_aud),
                    json={"smq": {"dataset": "payroll", "columns": ["pay_month"]}})
    body = r.json()
    check("A8 auditor payroll(internal) -> denied",
          r.status_code == 200 and body["status"] == "denied" and body["decision"] == "DENY",
          f"{r.status_code} {body.get('status')}")


def seg_b_w8():
    print("== B W8：dev 登录 / 结构化数据 / SSE ==")
    # B1 /dev/login：5 角色可签发且可验证；白名单外 403
    import config
    for role in ("employee", "manager", "hrbp", "exec", "auditor"):
        r = client.post("/dev/login", json={"role": role})
        tok = r.json().get("token", "")
        check(f"B1 /dev/login {role} 签发+验证", r.status_code == 200
              and sso_mock.verify(tok) is not None, f"{r.status_code}")
    r = client.post("/dev/login", json={"role": "superadmin"})
    check("B1 /dev/login 白名单外 -> 403", r.status_code == 403, str(r.status_code))

    # B2 /query 结构化 data（W8 图表源）
    r = client.post("/dev/login", json={"role": "hrbp"})
    tok_admin = r.json()["token"]
    smq_grp = {"dataset": "employee",
               "columns": ["dept_name", {"field": "id", "agg": "count"}],
               "group_by": ["dept_name"]}
    r = client.post("/query", headers=_auth(tok_admin), json={"smq": smq_grp})
    body = r.json()
    data = body.get("data") or {}
    check("B2 /query 返回结构化 data（columns+rows）",
          len(data.get("columns", [])) == 2 and len(data.get("rows", [])) == 9
          and body.get("total") == 9,
          f"cols={data.get('columns')} rows={len(data.get('rows', []))} total={body.get('total')}")

    # B3 SSE：intent_fn 注入（离线全管线，与线上同代码路径）
    from main import sse_events
    import json as _json
    conn = sqlite3.connect(os.path.join(BASE, "data", "hr_demo.db"))
    smq_sse = {"dataset": "employee",
               "columns": ["dept_name", {"field": "id", "agg": "count"}],
               "group_by": ["dept_name"]}
    events = [ _json.loads(e[len("data: "):].strip())
               for e in sse_events("各部门人数", {"role": "hr_admin"}, "KEY",
                                  intent_fn=lambda q: ("smq", smq_sse), conn=conn) ]
    stages = [e.get("stage") for e in events]
    final = events[-1]
    check("B3 SSE 事件序列 intent->permission->execute->final",
          stages == ["intent", "permission", "execute", "final"], str(stages))
    check("B3 SSE final 含 answered+data+sql+total",
          final.get("status") == "answered" and final.get("data")
          and final.get("sql") and final.get("total") == 9,
          f"{final.get('status')} total={final.get('total')}")
    check("B3 SSE permission 事件附 SMQ（前端口径展示）",
          events[1].get("smq") == smq_sse)

    # B4 SSE clarify 分支：澄清绝不触库（无 execute 阶段）
    events = [ _json.loads(e[len("data: "):].strip())
               for e in sse_events("上个月", {"role": "hr_admin"}, "KEY",
                                  intent_fn=lambda q: ("clarify", {"question": "哪个月?", "options": ["8月", "9月"]}),
                                  conn=conn) ]
    check("B4 SSE clarify -> final（无 execute）",
          [e.get("stage") for e in events] == ["intent", "final"]
          and events[-1]["status"] == "clarify")

    # B5 SSE denied 分支：R4 拒绝不触库
    events = [ _json.loads(e[len("data: "):].strip())
               for e in sse_events("李娜的工资", {"role": "employee", "emp_id": 1001}, "KEY",
                                  intent_fn=lambda q: ("smq", {"dataset": "payroll", "columns": ["net_pay"],
                                                               "filter": [{"field": "name", "op": "eq", "value": "刘杰"}]}),
                                  conn=conn) ]
    check("B5 SSE denied -> final（无 execute，DENY 归因 R4）",
          events[-1].get("status") == "denied"
          and "execute" not in [e.get("stage") for e in events],
          str(events[-1])[:100])
    conn.close()

    # B6 /ask/stream 无 Key -> 503（流开始前显式失败）
    r = client.post("/ask/stream", headers=_auth(TOK_ADMIN), json={"question": "8 月迟到几次"})
    check("B6 /ask/stream 无 Key -> 503", r.status_code == 503, str(r.status_code))


if __name__ == "__main__":
    seg_a()
    seg_b_w8()
    n_ok = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nAPI SMOKE: {n_ok}/{len(RESULTS)} " + ("PASSED" if n_ok == len(RESULTS) else "FAILED"))
    sys.exit(0 if n_ok == len(RESULTS) else 1)
