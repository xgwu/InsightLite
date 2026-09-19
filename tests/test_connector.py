# -*- coding: utf-8 -*-
"""
W10 连接器测试（离线：transport 注入 + 临时目录隔离，零网络依赖）。
覆盖：
  A  配置校验 fail-closed（表名白名单 / 凭据不入配置 / 回显脱敏）
  B  分页拉取（page / offset / cursor / max_pages 兜底 / data_path 缺失）
  C  重试与告警（指数退避后成功；耗尽 -> 告警 JSONL + 状态 error）
  D  FR-2 推断链路 + staging 写入（推断建议 / 类型推断 / 行数 >0 门 / 映射确认闭环）
  E  SQL 注入防线（非 stg_ 表名拒绝；write_staging 直接调用同样拒绝）
  F  API 权限（employee/manager/auditor 403；hr_admin 全流程 create->sync->confirm）
  G  FR-12 审计可操作入口（/audit/recent：hr_admin 200 / 其余 403）
  H  定时调度纯函数（due()：enabled / 间隔 / 到期）
"""
import json
import os
import sqlite3
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="insightlite-conn-test-")
os.environ["INSIGHTLITE_DATA_DIR"] = _TMP
os.environ["INSIGHTLITE_ALERT_DIR"] = os.path.join(_TMP, "alerts")
os.environ["INSIGHTLITE_AUDIT_DIR"] = os.path.join(_TMP, "audit")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend", "api"))
sys.path.insert(0, os.path.join(BASE, "backend", "core"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient  # noqa: E402

import connector as CC  # noqa: E402
import sso_mock  # noqa: E402
from main import app  # noqa: E402  # 先 main 再 connectors（规避循环导入，同 test_api_smoke）
import connectors as CA  # noqa: E402

client = TestClient(app)
TOK_EMP = sso_mock.issue("1001", "employee", 6, [1, 2, 6])
TOK_MGR = sso_mock.issue("2001", "manager", 2, [1, 2])
TOK_ADMIN = sso_mock.issue("9001", "hrbp", 1, [1])          # hrbp -> hr_admin
TOK_AUD = sso_mock.issue("9003", "auditor", 1, [1])


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


RESULTS = []


def check(name, ok, note=""):
    RESULTS.append((name, bool(ok), note))
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  {note}" if note and not ok else ""))


def fake_transport(pages):
    """按调用序返回预置页；记录每次收到的 params 便于断言分页行为。"""
    calls = []

    def t(url, params, headers):
        calls.append(dict(params))
        idx = min(len(calls) - 1, len(pages) - 1)
        return pages[idx]

    t.calls = calls
    return t


def seg_a():
    print("== A 配置校验 fail-closed ==")
    base = {"id": "hris_att", "url": "https://hris.example.com/api/att",
            "target_table": "stg_att", "data_path": "data"}
    check("A1 非法 id 拒绝", CC.validate_config({**base, "id": "X!"}) != "")
    check("A2 非 http url 拒绝", CC.validate_config({**base, "url": "ftp://x"}) != "")
    check("A3 auth 无 token_env 拒绝（凭据不得入配置）",
          CC.validate_config({**base, "auth": {"type": "bearer", "token": "sk-xx"}}) != "")
    check("A4 非 stg_ 表名拒绝", CC.validate_config({**base, "target_table": "employee"}) != "")
    check("A5 目标列名注入拒绝",
          CC.validate_config({**base, "field_mapping": {"a": 'x"; DROP TABLE employee'}}) != "")
    check("A6 合法配置通过", CC.validate_config(base) == "")
    red = CC.redact({**base, "auth": {"type": "bearer", "token_env": "T_ENV", "token": "sk-secret"}})
    check("A7 回显脱敏（token 不回显，token_env 保留）",
          "token" not in red["auth"] and red["auth"]["token_env"] == "T_ENV")


def seg_b():
    print("== B 分页拉取 ==")
    cfg = {"id": "p", "url": "https://x/api", "target_table": "stg_p",
           "data_path": "data.items",
           "pagination": {"type": "page", "page_param": "page", "size_param": "size",
                          "size": 2, "max_pages": 10}}
    t = fake_transport([{"data": {"items": [{"a": 1}, {"a": 2}]}},
                        {"data": {"items": [{"a": 3}]}},
                        {"data": {"items": []}}])
    rows = CC.fetch_rows(cfg, transport=t)
    check("B1 page 分页聚合（2+1，空页停）", len(rows) == 3 and rows[-1]["a"] == 3)
    check("B2 分页参数正确传递", t.calls[0]["page"] == 1 and t.calls[1]["page"] == 2)

    cfg2 = {**cfg, "pagination": {"type": "offset", "size": 2, "max_pages": 10}}
    t2 = fake_transport([{"data": {"items": [{"a": 1}, {"a": 2}]}},
                         {"data": {"items": [{"a": 3}]}}])
    rows2 = CC.fetch_rows(cfg2, transport=t2)
    check("B3 offset 分页（短页停）", len(rows2) == 3 and t2.calls[1]["offset"] == 2)

    cfg3 = {**cfg, "pagination": {"type": "cursor", "cursor_param": "cur",
                                  "cursor_path": "meta.next"}}
    t3 = fake_transport([{"data": {"items": [{"a": 1}]}, "meta": {"next": "c2"}},
                         {"data": {"items": [{"a": 2}]}, "meta": {"next": None}}])
    rows3 = CC.fetch_rows(cfg3, transport=t3)
    check("B4 cursor 分页（游标推进 + 终止）", len(rows3) == 2 and t3.calls[1]["cur"] == "c2")

    cfg4 = {**cfg, "pagination": {"type": "page", "size": 2, "max_pages": 3}}
    t4 = fake_transport([{"data": {"items": [{"a": 1}, {"a": 2}]}}])   # 永远满页
    rows4 = CC.fetch_rows(cfg4, transport=t4)
    check("B5 max_pages 兜底防空转", len(rows4) == 6 and len(t4.calls) == 3)

    cfg5 = {**cfg, "data_path": "wrong.path"}
    try:
        CC.fetch_rows(cfg5, transport=fake_transport([{"data": {"items": []}}]))
        check("B6 data_path 缺失 -> 显式报错", False)
    except RuntimeError as e:
        check("B6 data_path 缺失 -> 显式报错", "data_path" in str(e))


def seg_c():
    print("== C 重试与告警 ==")
    cfg = {"id": "retry1", "url": "https://x/api", "target_table": "stg_r",
           "pagination": {"type": "none"}}
    n = {"calls": 0}

    def flaky(url, params, headers):
        n["calls"] += 1
        if n["calls"] < 3:
            raise RuntimeError("HTTP 500")
        return {"data": [{"a": 1}]}

    rows = CC.fetch_rows(cfg, transport=flaky)
    check("C1 重试后成功（指数退避）", len(rows) == 1 and n["calls"] == 3)

    def dead(url, params, headers):
        raise RuntimeError("HTTP 503")

    conn = sqlite3.connect(os.path.join(_TMP, "t.db"))
    cfg_dead = {**cfg, "id": "dead1"}
    try:
        CC.sync(cfg_dead, conn, transport=dead)
        check("C2 重试耗尽 -> 显式抛错", False)
    except RuntimeError:
        check("C2 重试耗尽 -> 显式抛错", True)
    alerts = []
    adir = CC.ALERT_DIR
    if os.path.isdir(adir):
        for fn in os.listdir(adir):
            with open(os.path.join(adir, fn), encoding="utf-8") as f:
                alerts += [json.loads(l) for l in f if l.strip()]
    check("C3 失败告警落盘（append-only JSONL）",
          any(a["connector"] == "dead1" and a["level"] == "error" for a in alerts))
    st = CC.load_state().get("dead1", {})
    check("C4 状态记录 error", st.get("status") == "error")
    conn.close()


def seg_d():
    print("== D 推断链路 + staging ==")
    rows = [{"employee_id": "1001", "late_min": 5, "score": 0.9, "name": "张三"},
            {"employee_id": "1002", "late_min": 0, "score": 1.0, "name": "李四"}]
    sch = CC.infer_schema(rows, {})
    check("D1 同名直映建议", sch["proposed_mapping"]["employee_id"] == "employee_id")
    check("D2 类型推断（int/real/text）",
          sch["inferred_types"]["late_min"] == "integer"
          and sch["inferred_types"]["score"] == "real"
          and sch["inferred_types"]["name"] == "text")

    conn = sqlite3.connect(":memory:")
    cfg = {"id": "att", "url": "https://x/api", "target_table": "stg_att",
           "pagination": {"type": "none"}}
    r = CC.sync(cfg, conn, transport=lambda u, p, h: {"data": rows})
    check("D3 未确认 -> 同步落 staging + 返回推断建议",
          r["status"] == "ok" and r["rows"] == 2 and r["confirmed"] is False
          and r["proposal"] is not None)
    cols = [c[1] for c in conn.execute('PRAGMA table_info("stg_att")')]
    check("D4 staging 表结构与推断类型落库",
          cols == ["employee_id", "late_min", "score", "name"]
          and [c[2] for c in conn.execute('PRAGMA table_info("stg_att")')][1] == "INTEGER")

    cfg2 = {**cfg, "id": "att2", "target_table": "stg_att2",
            "field_mapping": {"employee_id": "emp_id"}, "mapping_confirmed": True}
    r2 = CC.sync(cfg2, conn, transport=lambda u, p, h: {"data": rows})
    check("D5 已确认 -> 映射生效且不再返回建议",
          r2["confirmed"] is True and r2["proposal"] is None
          and [c[1] for c in conn.execute('PRAGMA table_info("stg_att2")')][0] == "emp_id")

    conn.execute('CREATE TABLE stg_keep (a INTEGER)')
    conn.execute('INSERT INTO stg_keep VALUES (7)')
    cfg3 = {**cfg, "id": "att3", "target_table": "stg_keep"}
    try:
        CC.sync(cfg3, conn, transport=lambda u, p, h: {"data": []})
        check("D6 空拉取拒刷（>0 门，旧表不破坏）", False)
    except RuntimeError:
        check("D6 空拉取拒刷（>0 门，旧表不破坏）",
              conn.execute("SELECT a FROM stg_keep").fetchone() == (7,))
    conn.close()


def seg_e():
    print("== E SQL 注入防线 ==")
    conn = sqlite3.connect(":memory:")
    for bad in ("employee", 'stg_x"; DROP TABLE employee', "stg-x"):
        try:
            CC.write_staging(conn, bad, [{"a": 1}], {}, {})
            check(f"E1 非法表名拒绝: {bad!r}", False)
        except RuntimeError:
            check(f"E1 非法表名拒绝: {bad!r}", True)
    check("E2 employee 表未被破坏", conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE name='employee'").fetchone()[0] == 0)
    conn.close()


CFG = {"id": "api_att", "name": "考勤拉取", "url": "https://x/api/att",
       "data_path": "data", "target_table": "stg_api_att",
       "pagination": {"type": "none"}}


def seg_f():
    print("== F API 权限与管理流 ==")
    CA.DB_PATH = os.path.join(_TMP, "api.db")          # 隔离：不写演示库
    for tok, label in ((TOK_EMP, "employee"), (TOK_MGR, "manager"), (TOK_AUD, "auditor")):
        r = client.get("/connectors", headers=_auth(tok))
        check(f"F1 {label} 访问连接器 -> 403", r.status_code == 403, str(r.status_code))

    r = client.get("/connectors", headers=_auth(TOK_ADMIN))
    check("F2 hr_admin 列表 200", r.status_code == 200, str(r.status_code))

    r = client.post("/connectors", headers=_auth(TOK_ADMIN), json=CFG)
    check("F3 登记配置 200", r.status_code == 200, r.text[:120])

    r = client.post("/connectors", headers=_auth(TOK_ADMIN), json=CFG)
    check("F4 重复 id -> 409", r.status_code == 409, str(r.status_code))

    r = client.post("/connectors", headers=_auth(TOK_ADMIN),
                    json={**CFG, "id": "bad1", "target_table": "employee"})
    check("F5 非法配置 -> 422", r.status_code == 422, str(r.status_code))

    CC._http_json, saved = (lambda u, p, h: {"data": [
        {"employee_id": "1001", "late_min": 5}, {"employee_id": "1002", "late_min": 0}]}), CC._http_json
    try:
        r = client.post("/connectors/api_att/sync", headers=_auth(TOK_ADMIN))
        body = r.json()
        check("F6 手动同步 200：落 staging + 推断建议",
              r.status_code == 200 and body["rows"] == 2 and body["proposal"] is not None,
              r.text[:150])
        check("F7 staging 行真实可查",
              sqlite3.connect(CA.DB_PATH).execute(
                  "SELECT count(*) FROM stg_api_att").fetchone()[0] == 2)
        r = client.post("/connectors/api_att/confirm", headers=_auth(TOK_ADMIN),
                        json={"field_mapping": {"employee_id": "emp_id", "late_min": "late_min"}})
        check("F8 人工确认映射 -> confirmed", r.status_code == 200
              and r.json()["connector"]["mapping_confirmed"] is True, r.text[:150])
        r = client.post("/connectors/api_att/sync", headers=_auth(TOK_ADMIN))
        check("F9 确认后再同步：不再返回建议", r.json().get("proposal") is None)
    finally:
        CC._http_json = saved

    CC._http_json, saved = (lambda u, p, h: (_ for _ in ()).throw(RuntimeError("HTTP 503"))), CC._http_json
    try:
        r = client.post("/connectors/api_att/sync", headers=_auth(TOK_ADMIN))
        check("F10 上游失败 -> 502（告警已在 core 层落盘）", r.status_code == 502, str(r.status_code))
    finally:
        CC._http_json = saved


def seg_g():
    print("== G FR-12 审计可操作入口 ==")
    r = client.get("/audit/recent", headers=_auth(TOK_EMP))
    check("G1 employee 查审计 -> 403", r.status_code == 403, str(r.status_code))
    r = client.get("/audit/recent", headers=_auth(TOK_AUD))
    check("G2 auditor 查审计 -> 403（scope=none 不含审计面）", r.status_code == 403, str(r.status_code))
    r = client.get("/audit/recent?limit=10", headers=_auth(TOK_ADMIN))
    body = r.json()
    check("G3 hr_admin 查审计 -> 200 结构化（total_shown + entries）",
          r.status_code == 200 and "total_shown" in body and "entries" in body, r.text[:120])
    # 写一条豁免审计再读（D4 全链路闭环）
    os.makedirs(CC.AUDIT_DIR if hasattr(CC, "AUDIT_DIR") else os.environ["INSIGHTLITE_AUDIT_DIR"],
                exist_ok=True)
    import audit as A
    p = os.path.join(os.environ["INSIGHTLITE_AUDIT_DIR"], "audit-test.jsonl")
    A.record_if_exempt({"exempt": True, "decision": "ALLOW", "reason": "t", "model_version": "x"},
                       {"emp_id": 9001, "role": "hr_admin"}, {"dataset": "payroll"},
                       source="test", path=p)
    r = client.get("/audit/recent?limit=50", headers=_auth(TOK_ADMIN))
    check("G4 豁免审计写入后可经 API 读出（补偿措施可操作）",
          any(e.get("source") == "test" for e in r.json()["entries"]), r.text[:150])


def seg_h():
    print("== H 定时调度 ==")
    cfg = {"id": "sched", "enabled": True, "schedule_minutes": 60}
    check("H1 从未运行 -> 到期", CC.due(cfg, {}) is True)
    now = 1_000_000.0
    st = {"sched": {"_ts": now - 30 * 60}}
    check("H2 未满间隔 -> 不到期", CC.due(cfg, st, now=now) is False)
    st2 = {"sched": {"_ts": now - 61 * 60}}
    check("H3 超过间隔 -> 到期", CC.due(cfg, st2, now=now) is True)
    check("H4 disabled / 无调度 -> 永不到期",
          CC.due({**cfg, "enabled": False}, {}) is False
          and CC.due({**cfg, "schedule_minutes": 0}, {}) is False)


if __name__ == "__main__":
    seg_a(); seg_b(); seg_c(); seg_d(); seg_e(); seg_f(); seg_g(); seg_h()
    ok = all(x[1] for x in RESULTS)
    print(f"\nW10 CONNECTOR: {sum(1 for x in RESULTS if x[1])}/{len(RESULTS)} passed "
          + ("ALL GREEN" if ok else "FAILURES PRESENT"))
    sys.exit(0 if ok else 1)
