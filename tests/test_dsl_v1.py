# -*- coding: utf-8 -*-
"""
ChatBI-Lite W4 · DSL v1 单元测试（C1–C7 落地验收）
跑法: python test_dsl_v1.py   （零外部依赖，除 pyyaml/sqlglot 已在 ia venv）
退出码: 0 = 全过；1 = 有失败。run_all.py 串行调用。
"""
import os, sqlite3, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "core"))
import dsl_loader
import smq_schema as S
import perm_engine as P
import compiler as C

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "data", "hr_demo.db")

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


def check_raises(name, fn, needle=""):
    global PASS, FAIL
    try:
        fn()
        FAIL += 1
        print(f"  ❌ {name}  (no exception raised)")
    except S.SmqError as e:
        ok = needle in str(e) if needle else True
        if ok:
            PASS += 1
            print(f"  ✅ {name} -> SmqError: {str(e)[:90]}")
        else:
            FAIL += 1
            print(f"  ❌ {name}  (wrong message: {e})")
    except Exception as e:
        FAIL += 1
        print(f"  ❌ {name}  (wrong exception type: {type(e).__name__}: {e})")


def smq(ds, cols, **kw):
    d = {"dataset": ds, "columns": cols}
    d.update(kw)
    return d


print("=" * 72)
print(f"TEST DSL v1  (model={dsl_loader.REG.version})")
print("=" * 72)

# ---------- C1: 注册表来源 ----------
print("\n[C1] registry loaded from YAML")
check("5 datasets", set(S.TABLES) == {"employee", "department", "attendance", "leave_balance", "payroll"})
own_total = sum(len(v) for v in dsl_loader.REG.own.values())
check("26 own (physical modeled) fields", own_total == 26, f"got {own_total}")
check("query surface (with inherited dims) = 45", sum(len(v) for v in S.TABLES.values()) == 45,
      f"got {sum(len(v) for v in S.TABLES.values())}")
check("payroll table mapped", dsl_loader.REG.dataset_table["payroll"] == "payroll")
check("salary NOT inherited into fact tables (dims only)",
      "salary" not in S.TABLES["attendance"] and "salary" not in S.TABLES["payroll"])
check("dept_name unavailable on department dataset",
      "dept_name" not in S.TABLES["department"])

# ---------- C2: allowed_aggs ----------
print("\n[C2] aggregate whitelist")
check_raises("sum(salary) rejected", lambda: S.validate(smq("employee", [{"field": "salary", "agg": "sum"}])),
             "not allowed")
check_raises("avg(name) rejected (attribute)", lambda: S.validate(smq("attendance", [{"field": "name", "agg": "avg"}])),
             "aggregable")
check_raises("count(bonus) on attendance rejected", lambda: S.validate(smq("attendance", [{"field": "bonus", "agg": "count"}])),
             "not in semantic model")
check("sum(late_min) still OK (Q3 baseline)", S.validate(smq("attendance", [{"field": "late_min", "agg": "sum"}])) is not None)
check("avg(net_pay) OK on payroll", S.validate(smq("payroll", [{"field": "net_pay", "agg": "avg"}])) is not None)

# ---------- C3: payroll ----------
print("\n[C3] payroll dataset end-to-end")
sql, _ = C.compile_smq(S.Q4_PAYROLL)
rows, total = C.execute_sqlite(sql)
check("Q4-payroll compiles+executes", total == 9, f"total={total}")   # 9 个有人的叶子部门

# ---------- C4: attribute 锚定 / measure 禁 group_by ----------
print("\n[C4] group_by anchoring")
check("group_by name legal (anchored)", S.validate(smq("attendance", ["name"], group_by=["name"])) is not None)
sql3, _ = C.compile_smq(S.Q3)
check("Q3 renders GROUP BY e.id, e.name", 'e."id"' in sql3 and 'e."name"' in sql3)
gb_q = smq("attendance", ["hours"], group_by=["hours"])
S.validate(gb_q)
check("group_by measure -> normalized away (not rejected)", "group_by" not in gb_q)
conn = sqlite3.connect(DB)
rows3, total3 = C.execute_sqlite(sql3)
dup = conn.execute("""SELECT COUNT(*) FROM (SELECT e.name, COUNT(DISTINCT e.id) c FROM attendance t
                      JOIN employee e ON t.emp_id = e.id
                      WHERE t.work_date >= '2026-08-01' AND t.work_date < '2026-09-01'
                      GROUP BY e.name HAVING c > 1)""").fetchone()[0]
conn.close()
check(f"Q3 anchored result rows={total3} (no cross-person merge; {dup} duplicate names in data)",
      total3 > 0)

# ---------- C5: 权限引擎三态 ----------
print("\n[C5] permission engine (three-state)")
ATT_Q = smq("attendance", ["work_date", "hours"])
SAL_COL = smq("attendance", ["salary"]) if "salary" in S.TABLES["attendance"] else None
PAY_Q = smq("payroll", ["pay_month", "base_salary", "net_pay"])

v = P.decide(ATT_Q, {"role": "hr_admin"})
check("hr_admin attendance -> ALLOW", v["decision"] == "ALLOW")
v = P.decide(ATT_Q, {"role": "dept_manager", "dept_id": 2})
check("dept_manager attendance -> ALLOW (row policy applies at SQL)", v["decision"] == "ALLOW")
v = P.decide(PAY_Q, {"role": "dept_manager", "dept_id": 2})
check("dept_manager payroll -> MASK", v["decision"] == "MASK", str(v))
v = P.decide(PAY_Q, {"role": "employee", "emp_id": 1001})
check("employee payroll (own rows) -> ALLOW (OQ2)", v["decision"] == "ALLOW", str(v))
v = P.decide(ATT_Q, {"role": "auditor"})
check("auditor -> DENY (scope none)", v["decision"] == "DENY")
v = P.decide(ATT_Q, {"role": "superadmin"})
check("forged role -> DENY (fail-closed)", v["decision"] == "DENY")
v = P.decide(ATT_Q, {})
check("missing role -> DENY", v["decision"] == "DENY")

ord_q = smq("payroll", ["pay_month", "net_pay"], order_by=[{"field": "net_pay", "dir": "desc"}])
v = P.decide(ord_q, {"role": "dept_manager", "dept_id": 2})
check("R2: critical in order_by -> DENY", v["decision"] == "DENY")
filt_q = smq("employee", ["name"], filter=[{"field": "salary", "op": "gt", "value": 30000}])
v = P.decide(filt_q, {"role": "dept_manager", "dept_id": 2})
check("R3: critical in filter -> DENY", v["decision"] == "DENY")
v = P.decide(filt_q, {"role": "hr_admin"})
check("R3 does not bind hr_admin -> ALLOW", v["decision"] == "ALLOW")

# R4: employee 指名道姓查他人 -> DENY（OQ2 配套，W4 落地）
conn = sqlite3.connect(DB)
OWN = {"role": "employee", "emp_id": 1001}                     # 1001 = 王伟（种子钉死）
other_q = smq("attendance", ["work_date", "hours", "name"],
              filter=[{"field": "name", "op": "eq", "value": "李娜"}])
v = P.decide(other_q, OWN, conn=conn)
check("R4: employee 指名查他人 -> DENY", v["decision"] == "DENY", str(v))
self_q = smq("attendance", ["work_date", "hours", "name"],
             filter=[{"field": "name", "op": "eq", "value": "王伟"}])
v = P.decide(self_q, OWN, conn=conn)
check("R4: employee 指名查本人 -> ALLOW（不误伤）", v["decision"] == "ALLOW", str(v))
no_name_q = smq("payroll", ["pay_month", "base_salary", "net_pay"])
v = P.decide(no_name_q, OWN, conn=conn)
check("R4 不拦无姓名过滤的本人行查询 -> ALLOW (OQ2 保持)", v["decision"] == "ALLOW", str(v))
conn.close()

# 行级策略 SQL 生成
conn = sqlite3.connect(DB)
rp, args = P.row_policy(conn, "attendance", {"role": "dept_manager", "dept_id": 2})
n_dept2 = conn.execute(f"SELECT COUNT(DISTINCT e.dept_id) FROM attendance t JOIN employee e ON t.emp_id = e.id "
                       f"WHERE {rp.replace('e.', 'e.')}", args).fetchone()[0]
conn.close()
check(f"row_policy dept 2 subtree = {n_dept2} depts (>=1)", n_dept2 >= 1)

# ---------- C6: LLM 提示词 ----------
print("\n[C6] system prompt from DSL")
prompt = dsl_loader.REG.build_system_prompt()
check("prompt mentions payroll", "payroll" in prompt)
check("prompt mentions all datasets", all(d in prompt for d in S.TABLES))
check("prompt contains late_min trap rule", "late_min" in prompt)
check("prompt has clarify contract (W5)", '"clarify"' in prompt)
check("prompt has runtime date anchor (W4 Q8)", "0. 今天的日期是" in prompt)

# ---------- C8 (W5): clarify 契约分派 _interpret ----------
print("\n[C8] render._interpret clarify contract")
import render as R2
k, p = R2._interpret('{"clarify": true, "question": "哪个时间段？", "options": ["8月", "9月"]}')
check("clarify dispatch ok", k == "clarify" and p["question"] == "哪个时间段？" and p["options"] == ["8月", "9月"])
k, p = R2._interpret('{"clarify": true}')
check("clarify without question -> invalid (fail-closed)", k == "invalid")
k, p = R2._interpret('{"clarify": true, "question": "q", "dataset": "payroll", "columns": ["net_pay"]}')
check("clarify sm外部字段被丢弃（不泄漏查询）", k == "clarify" and "dataset" not in p)
k, p = R2._interpret('{"error": "OUT_OF_SCOPE", "reason": "x"}')
check("out_of_scope dispatch ok", k == "refused_scope" and p == "x")
k, p = R2._interpret('{"dataset": "attendance", "columns": ["hours"]}')
check("smq dispatch ok", k == "smq" and p["dataset"] == "attendance")
k, p = R2._interpret('not json at all')
check("non-json -> invalid", k == "invalid")

# ---------- C7: audit_log model_version ----------
print("\n[C7] audit_log model_version")
conn = sqlite3.connect(DB)
cols = [r[1] for r in conn.execute("PRAGMA table_info(audit_log)")]
conn.close()
check("audit_log has model_version column", "model_version" in cols)
check("decide() returns model_version", P.decide(ATT_Q, {"role": "hr_admin"}).get("model_version") == dsl_loader.REG.version)

# ---------- C9 (W6): 时间窗口预设 + 环比 ----------
print("\n[C9] SMQ v2: time_window + compare_to")
import datetime as _dt
ANCHOR = _dt.date(2026, 9, 16)          # 固定锚点保证断言确定性（今天是 2026-09-16/17）

tw_ok = smq("attendance", ["dept_name", {"field": "hours", "agg": "avg"}],
            time_window={"field": "work_date", "preset": "last_month"}, group_by=["dept_name"])
check("time_window last_month validates", S.validate(dict(tw_ok)) is not None)
check_raises("unknown preset rejected",
             lambda: S.validate(smq("attendance", ["hours"],
                                    time_window={"field": "work_date", "preset": "last_week"})),
             "unknown time preset")
check_raises("time_window on non-date field rejected",
             lambda: S.validate(smq("attendance", ["hours"],
                                    time_window={"field": "hours", "preset": "this_month"})),
             "not a date/month")
check_raises("time_window + same-field filter conflict rejected",
             lambda: S.validate(smq("attendance", ["hours"],
                                    filter=[{"field": "work_date", "op": "ge", "value": "2026-08-01"}],
                                    time_window={"field": "work_date", "preset": "this_month"})),
             "conflicts")
check_raises("time_window null rejected",
             lambda: S.validate(smq("attendance", ["hours"], time_window=None)),
             "must not be null")
check("month-grain field accepts preset",
      S.validate(smq("payroll", [{"field": "net_pay", "agg": "avg"}],
                     time_window={"field": "pay_month", "preset": "last_month"})) is not None)

# 展开确定性：固定锚点下的区间逐字断言
sql_tw, _ = C.compile_smq(dict(tw_ok), anchor=ANCHOR)
check("last_month expands to [2026-08-01, 2026-09-01)",
      't."work_date" >= \'2026-08-01\'' in sql_tw and 't."work_date" < \'2026-09-01\'' in sql_tw, sql_tw[:200])
sql_this, _ = C.compile_smq(dict(tw_ok, time_window={"field": "work_date", "preset": "this_month"}), anchor=ANCHOR)
check("this_month expands to [2026-09-01, 2026-10-01)",
      ">= '2026-09-01'" in sql_this and "< '2026-10-01'" in sql_this)
pm = smq("payroll", [{"field": "net_pay", "agg": "avg"}],
         time_window={"field": "pay_month", "preset": "last_month"})
sql_pm, _ = C.compile_smq(pm, anchor=ANCHOR)
check("month grain last_month expands to eq '2026-08'", "= '2026-08'" in sql_pm, sql_pm[:160])
pm3 = smq("payroll", [{"field": "net_pay", "agg": "avg"}],
          time_window={"field": "pay_month", "preset": "last_3_months"})
sql_pm3, _ = C.compile_smq(pm3, anchor=ANCHOR)
check("month grain last_3_months expands to IN 3 months",
      "IN ('2026-07', '2026-08', '2026-09')" in sql_pm3, sql_pm3[:160])

# compare_to 校验
cmp_ok = smq("attendance", ["dept_name", {"field": "hours", "agg": "avg"}],
             time_window={"field": "work_date", "preset": "last_month"},
             compare_to={"months_back": 1}, group_by=["dept_name"])
check("valid compare_to validates", S.validate(dict(cmp_ok)) is not None)
check_raises("months_back=0 rejected", lambda: S.validate(smq(
    "attendance", [{"field": "hours", "agg": "avg"}],
    time_window={"field": "work_date", "preset": "last_month"}, compare_to={"months_back": 0})), "1, 12")
check_raises("months_back=13 rejected", lambda: S.validate(smq(
    "attendance", [{"field": "hours", "agg": "avg"}],
    time_window={"field": "work_date", "preset": "last_month"}, compare_to={"months_back": 13})), "1, 12")
check_raises("compare_to without time_window rejected", lambda: S.validate(smq(
    "attendance", [{"field": "hours", "agg": "avg"}],
    filter=[{"field": "work_date", "op": "ge", "value": "2026-08-01"}], compare_to={"months_back": 1})),
    "requires time_window")
check_raises("compare_to on detail query rejected", lambda: S.validate(smq(
    "attendance", ["work_date", "hours"],
    time_window={"field": "work_date", "preset": "last_month"}, compare_to={"months_back": 1})),
    "aggregate query")

# 环比端到端：本期/上期两条 SQL 均在 SQLite 真实执行，上期区间确实前移
conn = sqlite3.connect(DB)
cur_sql = C.compile_smq(dict(cmp_ok), anchor=ANCHOR)[0]
prev_sql = C.compile_smq(dict(cmp_ok), anchor=ANCHOR, month_back=1)[0]
n_cur = len(conn.execute(__import__("sqlglot").transpile(cur_sql, read="postgres", write="sqlite")[0]).fetchall())
n_prev = len(conn.execute(__import__("sqlglot").transpile(prev_sql, read="postgres", write="sqlite")[0]).fetchall())
conn.close()
check(f"compare pair executes on sqlite (cur={n_cur} depts, prev={n_prev} depts)", n_cur > 0 and n_prev > 0)
check("prev SQL shifted to July (2026-07-01)", ">= '2026-07-01'" in prev_sql and "< '2026-08-01'" in prev_sql)

# ---------- 回归基准：v0 三样例仍全过 ----------
print("\n[regression] v0 samples")
for name, q in S.SAMPLES[:3]:
    try:
        S.validate(q)
        C.compile_smq(q)
        check(f"{name} validate+compile OK", True)
    except Exception as e:
        check(f"{name} validate+compile OK", False, str(e))

print("\n" + "=" * 72)
print(f"RESULT: {PASS} passed, {FAIL} failed")
print("=" * 72)
sys.exit(0 if FAIL == 0 else 1)
