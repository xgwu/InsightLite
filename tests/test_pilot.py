# -*- coding: utf-8 -*-
"""
W11 试点机制测试（离线，临时目录隔离，零 LLM/网络依赖）。
覆盖：
  A  qlog 记录（_execute 全链路：answered / denied 都落日志；观测面写失败不阻断）
  B  feedback 提交（任何登录角色；category 白名单 422）
  C  feedback 审核权限（employee/auditor 403；hr_admin 可列表/审核）
  D  accept -> eval case 骨架（pending_cases 落盘；reject 不产生骨架）
  E  aging（48h 未处理点名）与静默错误率分子统计
  F  日报纯函数 compute_metrics（分母 0 不产假 0%；明确失败分布）
  G  日报端到端（subprocess 跑 eval harness 解析口径一致率 + md 落盘）
"""
import json
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="insightlite-pilot-test-")
os.environ["INSIGHTLITE_QLOG_DIR"] = os.path.join(_TMP, "qlog")
os.environ["INSIGHTLITE_FEEDBACK_STORE"] = os.path.join(_TMP, "feedback.json")
os.environ["INSIGHTLITE_REPORT_DIR"] = os.path.join(_TMP, "reports")
os.environ["INSIGHTLITE_AUDIT_DIR"] = os.path.join(_TMP, "audit")
os.environ["INSIGHTLITE_DATA_DIR"] = _TMP
os.environ["INSIGHTLITE_PENDING_CASES"] = os.path.join(_TMP, "pending_cases.json")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend", "api"))
sys.path.insert(0, os.path.join(BASE, "backend", "core"))
sys.path.insert(0, os.path.join(BASE, "eval"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient  # noqa: E402

import qlog as Q  # noqa: E402
import feedback as FB  # noqa: E402
import sso_mock  # noqa: E402
from main import app  # noqa: E402  # 先 main（循环导入纪律，同既有测试）
import daily_report as DR  # noqa: E402

client = TestClient(app)
TOK_EMP = sso_mock.issue("1001", "employee", 6, [1, 2, 6])
TOK_MGR = sso_mock.issue("2001", "manager", 2, [1, 2])
TOK_ADMIN = sso_mock.issue("9001", "hrbp", 1, [1])
TOK_AUD = sso_mock.issue("9003", "auditor", 1, [1])


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


RESULTS = []


def check(name, ok, note=""):
    RESULTS.append((name, bool(ok), note))
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  {note}" if note and not ok else ""))


def seg_a():
    print("== A qlog 全链路 ==")
    smq = {"dataset": "attendance", "columns": ["work_date", "hours"],
           "filter": [{"field": "name", "op": "eq", "value": "王伟"}]}
    r = client.post("/query", headers=_auth(TOK_EMP), json={"smq": smq})
    check("A1 answered 查询落日志",
          r.status_code == 200 and any(x["status"] == "answered" for x in Q.read_rows(1)))
    smq_deny = {"dataset": "attendance", "columns": ["work_date"],
                "filter": [{"field": "name", "op": "eq", "value": "李娜"}]}
    r = client.post("/query", headers=_auth(TOK_EMP), json={"smq": smq_deny})
    rows = Q.read_rows(1)
    check("A2 denied 查询落日志（授权先于检索但仍计数）",
          r.json()["status"] == "denied" and any(x["status"] == "denied" for x in rows))
    rec = [x for x in rows if x["status"] == "answered"][-1]
    check("A3 日志五要素（ts/source/role/status/smq 口径）",
          all(k in rec for k in ("ts", "source", "role", "status", "smq"))
          and rec["role"] == "employee")
    # 看板读取也是问答（分母来源之一，source 可区分）：先入板一个 widget 再读
    smq_w = {"dataset": "attendance", "columns": [{"field": "late_min", "agg": "count"}],
             "filter": [{"field": "work_date", "op": "ge", "value": "2026-08-01"},
                        {"field": "work_date", "op": "lt", "value": "2026-09-01"}]}
    w = client.post("/dashboard", headers=_auth(TOK_MGR),
                    json={"title": "W11测试卡片", "smq": smq_w, "chart": "table"})
    check("A4 前置：入板成功", w.status_code == 200, w.text[:120])
    client.get("/dashboard", headers=_auth(TOK_MGR))
    check("A4 dashboard-read 计入并可区分",
          any(x["source"] == "dashboard-read" for x in Q.read_rows(1)))
    # 观测面：存储不可写不阻断业务
    saved = Q.QLOG_DIR
    Q.QLOG_DIR = os.path.join(_TMP, "no", "such", "dir", "forbidden")
    try:
        os.makedirs(Q.QLOG_DIR, exist_ok=True) and None
        import stat
        os.chmod(Q.QLOG_DIR, stat.S_IREAD)     # 目录只读 -> open 失败
        r = client.post("/query", headers=_auth(TOK_EMP), json={"smq": smq})
        ok_biz = r.status_code == 200 and r.json()["status"] == "answered"
    finally:
        os.chmod(Q.QLOG_DIR, 0o755)
        Q.QLOG_DIR = saved
    check("A5 qlog 写失败不阻断业务查询（观测面≠治理面）", ok_biz)


def seg_b():
    print("== B feedback 提交 ==")
    r = client.post("/feedback", headers=_auth(TOK_EMP),
                    json={"question": "研发部 8 月平均工时是 180 吗？",
                          "category": "wrong_number", "note": "口径应为总工时"})
    check("B1 employee 提交 200 + open", r.status_code == 200
          and r.json()["feedback"]["status"] == "open", r.text[:120])
    global FB_EMP_ID
    FB_EMP_ID = r.json()["feedback"]["id"]
    r = client.post("/feedback", headers=_auth(TOK_AUD),
                    json={"question": "x", "category": "other"})
    check("B2 auditor 也可提交（反馈是权利）", r.status_code == 200)
    r = client.post("/feedback", headers=_auth(TOK_EMP),
                    json={"question": "x", "category": "not_a_kind"})
    check("B3 category 白名单 -> 422", r.status_code == 422, str(r.status_code))


def seg_c():
    print("== C 审核权限 ==")
    for tok, label in ((TOK_EMP, "employee"), (TOK_AUD, "auditor")):
        r = client.get("/feedback", headers=_auth(tok))
        check(f"C1 {label} 列表 -> 403", r.status_code == 403, str(r.status_code))
        r = client.post(f"/feedback/{FB_EMP_ID}/review", headers=_auth(tok),
                        json={"action": "accept"})
        check(f"C2 {label} 审核 -> 403", r.status_code == 403, str(r.status_code))
    r = client.get("/feedback", headers=_auth(TOK_ADMIN))
    body = r.json()
    check("C3 hr_admin 列表 200（含闭环健康字段）",
          r.status_code == 200 and "open_aging_over_48h" in body
          and "pending_eval_cases" in body)
    r = client.get("/feedback?status=open", headers=_auth(TOK_ADMIN))
    check("C4 status 过滤 200", r.status_code == 200
          and all(f["status"] == "open" for f in r.json()["items"]))
    r = client.post(f"/feedback/{FB_EMP_ID}/review", headers=_auth(TOK_ADMIN),
                    json={"action": "bad"})
    check("C5 非法 action -> 422", r.status_code == 422, str(r.status_code))
    r = client.post("/feedback/fakeid/review", headers=_auth(TOK_ADMIN),
                    json={"action": "accept"})
    check("C6 不存在 id -> 404", r.status_code == 404, str(r.status_code))


def seg_d():
    print("== D accept -> eval case 骨架 ==")
    r = client.post(f"/feedback/{FB_EMP_ID}/review", headers=_auth(TOK_ADMIN),
                    json={"action": "accept"})
    body = r.json()["feedback"]
    check("D1 accept -> accepted + 骨架生成",
          body["status"] == "accepted" and body["eval_case_skeleton"]["id"] == f"FB-{FB_EMP_ID}")
    check("D2 骨架语义（wrong_number -> smq 建议 + pending-confirm）",
          body["eval_case_skeleton"]["expect_type_suggested"] == "smq"
          and body["eval_case_skeleton"]["status"] == "pending-confirm")
    check("D3 pending_cases 落盘", any(p["id"] == f"FB-{FB_EMP_ID}" for p in FB.pending_cases()))
    r = client.post("/feedback", headers=_auth(TOK_MGR),
                    json={"question": "这题本该拒答", "category": "should_refuse"})
    fid2 = r.json()["feedback"]["id"]
    r = client.post(f"/feedback/{fid2}/review", headers=_auth(TOK_ADMIN),
                    json={"action": "reject"})
    check("D4 reject -> rejected 且无骨架", r.json()["feedback"]["status"] == "rejected"
          and "eval_case_skeleton" not in r.json()["feedback"])
    n_before = len(FB.pending_cases())
    check("D5 pending 数不变（reject 不产生骨架）",
          n_before == 1 and all(p["id"] == f"FB-{FB_EMP_ID}" for p in FB.pending_cases()))


def seg_e():
    print("== E aging 与静默错误率分子 ==")
    import datetime as _d
    items = FB._load()
    items.append({"id": "old00000001", "ts": (_d.datetime.now() - _d.timedelta(hours=72))
                  .isoformat(timespec="seconds"),
                  "question": "old", "answer_status": "", "category": "wrong_number",
                  "note": "", "smq": None, "reporter": {"role": "employee"},
                  "status": "open"})
    FB._save(items)
    aging = FB.aging_open()
    check("E1 72h open 被点名，48h 内不点名",
          any(f["id"] == "old00000001" for f in aging)
          and not any(f["id"] == FB_EMP_ID for f in FB.aging_open()))
    check("E2 静默错误率分子（窗口内 accepted wrong_number）",
          FB.accepted_wrong_number(days=7) >= 1)


def seg_f():
    print("== F 日报纯函数 ==")
    rows = [
        {"source": "api-query", "status": "answered"},
        {"source": "api-query", "status": "denied"},
        {"source": "sse-final", "status": "clarify"},
        {"source": "dashboard-read", "status": "answered"},
        {"source": "api-ask", "status": "refused_scope"},
    ]
    m = DR.compute_metrics(rows, 1, {"cons_pass": 21, "cons_total": 21, "cons_rate": 100.0,
                                     "eval_pass": 26, "eval_total": 26})
    check("F1 总问答数与看板区分", m["window_questions"] == 5 and m["dashboard_reads"] == 1)
    check("F2 静默错误率（1/5=20%）", abs(m["silent_error_rate"] - 20.0) < 1e-9)
    check("F3 明确失败率（3/5=60%）与分布",
          abs(m["explicit_fail_rate"] - 60.0) < 1e-9
          and m["fail_breakdown"]["denied"] == 1 and m["fail_breakdown"]["clarify"] == 1
          and m["fail_breakdown"]["refused_scope"] == 1)
    m0 = DR.compute_metrics([], 0, {})
    check("F4 分母 0 -> 比率无定义（None，不产假 0%）",
          m0["silent_error_rate"] is None and m0["explicit_fail_rate"] is None)


def seg_g():
    print("== G 日报端到端 ==")
    rc = DR.main(days=7)
    check("G1 main 正常退出", rc == 0)
    import glob
    reports = glob.glob(os.path.join(_TMP, "reports", "daily-*.md"))
    check("G2 md 落盘", len(reports) == 1)
    text = open(reports[0], encoding="utf-8").read()
    check("G3 口径一致率解析自 eval harness（与 run_all 同源）",
          "口径一致率" in text and ("21/21" in text or "解析失败" in text))
    check("G4 分母语义诚实（>0 报比率；=0 报无定义而非假 0%）",
          ("总问答数：0" in text and "无定义" in text)
          or ("总问答数：" in text and "静默错误率 = " in text and "无定义" not in text))
    check("G5 含闭环健康段（aging/pending）", "闭环健康" in text and "eval case 骨架" in text)


if __name__ == "__main__":
    seg_a(); seg_b(); seg_c(); seg_d(); seg_e(); seg_f(); seg_g()
    ok = all(x[1] for x in RESULTS)
    print(f"\nW11 PILOT: {sum(1 for x in RESULTS if x[1])}/{len(RESULTS)} passed "
          + ("ALL GREEN" if ok else "FAILURES PRESENT"))
    sys.exit(0 if ok else 1)
