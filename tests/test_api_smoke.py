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
    import sqlite3
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


if __name__ == "__main__":
    seg_a()
    n_ok = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nAPI SMOKE: {n_ok}/{len(RESULTS)} " + ("PASSED" if n_ok == len(RESULTS) else "FAILED"))
    sys.exit(0 if n_ok == len(RESULTS) else 1)
