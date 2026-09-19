# -*- coding: utf-8 -*-
"""
Sprint 0 · Spike A：确定性 SQL 编译器 v0 + sqlglot 验证
dev-plan.html W2 Spike 验收物：「用 sqlglot 把 3 个代表性 SMQ 编译为 PostgreSQL SQL
（含 join + 时间窗口），验证 AST 操作可行性」；附加 M0 前置验证——编译产物在示例库上真实执行。

流程（对每条 SMQ）：
  1. smq_schema.validate()   —— 非法输入显式失败（SmqError）
  2. render_sql()            —— 确定性渲染（纯函数：同输入同输出，无 LLM 参与）
  3. sqlglot.parse_one()     —— AST 合法性验证（read=postgres）
  4. sqlglot.transpile       —— 产出目标方言（display=postgres / execute=sqlite）
  5. 对 data/hr_demo.db 真实执行，打印前 5 行结果

另附 4 个「非法 SMQ」用例，验证编译层明确失败率 = 100%。
用法: python compiler.py
"""
import sqlite3, sys, os
import datetime as _dt
import sqlglot
from sqlglot import exp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smq_schema as S

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(BASE, "data", "hr_demo.db")


class CompileError(Exception):
    """编译期显式失败（区别于 SmqError：SMQ 合法但渲染约束不满足）。"""


def _lit(v):
    """值 -> SQL 字面量。只接受受控类型；字符串单引号转义。"""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    raise CompileError(f"unsupported literal type: {type(v).__name__}")


def _month_shift(anchor: _dt.date, delta: int) -> _dt.date:
    """锚点当月 1 号平移 delta 个月（delta 可负）。delta>12 直接 CompileError（防线：偏移必须有界）。"""
    if not -240 <= delta <= 240:
        raise CompileError(f"month shift out of bounds: {delta}")
    y, m = anchor.year, anchor.month + delta
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return _dt.date(y, m, 1)


def expand_time_window(ds: str, tw: dict, anchor: _dt.date, month_back: int = 0):
    """time_window 预设 -> WHERE 条件片段列表（确定性展开，month_back 用于环比平移）。
    date  字段：ge 起始 / lt 结束（闭开区间，与 DSL time_semantics 一致）
    month 字段：单月 eq，多月 IN
    调用方需已 validate（field/preset/冲突均已把关）。"""
    start_d, end_d = S.PRESET_MONTH_SPAN[tw["preset"]]
    start = _month_shift(anchor, start_d - month_back)
    end = _month_shift(anchor, end_d - month_back)
    col = S.colref(ds, tw["field"])
    if S._field_meta(ds, tw["field"]).get("type") == "month":
        months = []
        cur = start
        while cur < end:
            months.append(cur.strftime("%Y-%m"))
            cur = _month_shift(cur, 1)
        if len(months) == 1:
            return [f"{col} = {_lit(months[0])}"]
        return [f"{col} IN ({', '.join(_lit(m) for m in months)})"]
    return [f"{col} >= {_lit(start.isoformat())}", f"{col} < {_lit(end.isoformat())}"]


def _col_expr(ds, spec):
    """列规格 -> (表达式文本, 输出别名)。"""
    if isinstance(spec, str):
        return S.colref(ds, spec), spec
    field, agg = spec["field"], spec["agg"]
    if agg is None:
        return S.colref(ds, field), field
    return f"{agg.upper()}({S.colref(ds, field)})", f"{agg}_{field}"


def render_sql(smq: dict, anchor: _dt.date = None, month_back: int = 0) -> str:
    """SMQ -> PostgreSQL 文本（确定性、纯函数）。调用方需先 validate。
    anchor：相对时间锚点（缺省 = 今天）；time_window 预设由它展开，保证同输入同输出。
    month_back：环比平移（>0 时 time_window 整体前移 N 个月），仅由对话层 compare 逻辑使用。"""
    if anchor is None:
        anchor = _dt.date.today()
    ds = smq["dataset"]
    cols, aliases = [], []
    for spec in smq["columns"]:
        e, a = _col_expr(ds, spec)
        cols.append(f'{e} AS "{a}"')
        aliases.append(a)

    sql = f"SELECT {', '.join(cols)} FROM {ds} AS t"
    for tbl, alias, on in S.JOIN_PATHS[ds]:
        sql += f" JOIN {tbl} AS {alias} ON {on}"

    conds = []
    for f in smq.get("filter", []):
        col = S.colref(ds, f["field"])
        op = S.OPS[f["op"]]
        if f["op"] == "in":
            vals = ", ".join(_lit(x) for x in f["value"])
            conds.append(f"{col} IN ({vals})")
        else:
            conds.append(f"{col} {op} {_lit(f['value'])}")
    if smq.get("time_window"):
        conds.extend(expand_time_window(ds, smq["time_window"], anchor, month_back=month_back))
    if conds:
        sql += " WHERE " + " AND ".join(conds)

    if smq.get("group_by"):
        # C4: attribute 字段锚定实体主键（防同名不同人），普通字段单表达式
        gb = ", ".join(expr for g in smq["group_by"] for expr in S.group_exprs(ds, g))
        sql += f" GROUP BY {gb}"

    if smq.get("order_by"):
        ob = []
        for o in smq["order_by"]:
            e, _ = _col_expr(ds, {"field": o["field"], "agg": o.get("agg")})
            ob.append(f"{e} {o.get('dir', 'asc').upper()}")
        sql += " ORDER BY " + ", ".join(ob)

    if smq.get("limit"):
        sql += f" LIMIT {smq['limit']}"
    return sql


def compile_smq(smq: dict, read: str = "postgres", anchor: _dt.date = None, month_back: int = 0):
    """validate -> render -> AST 验证。返回 (sql_text, ast)。任何失败=显式异常。
    compare_to 不在本层展开——环比由对话层跑两条平移 SQL（month_back=0 / N）后
    在结果侧计算差值（见 render.py），权限只需对基础 SMQ 决策一次。"""
    S.validate(smq)
    sql = render_sql(smq, anchor, month_back=month_back)
    ast = sqlglot.parse_one(sql, read=read)          # 语法/AST 验证，失败抛 ParseError
    return sql, ast


def execute_sqlite(sql_pg: str, limit_print: int = 5):
    sql_lite = sqlglot.transpile(sql_pg, read="postgres", write="sqlite")[0]
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(sql_lite).fetchall()
    finally:
        conn.close()
    return rows[:limit_print], len(rows)


def negative_cases():
    """4 个非法 SMQ：全部必须显式失败（SmqError/CompileError/ParseError）。"""
    return [
        ("unknown dataset", {"dataset": " payroll", "columns": ["id"]}),
        ("unknown field",   {"dataset": "attendance", "columns": ["bonus"]}),
        ("bad op",          {"dataset": "attendance", "columns": ["hours"],
                             "filter": [{"field": "hours", "op": "LIKE", "value": "%8%"}]}),
        ("bad limit type",  {"dataset": "attendance", "columns": ["hours"], "limit": "10; DROP TABLE employee"}),
    ]


def injection_demo():
    """注入中和演示：恶意值经转义后编译为无害字面量——防注入靠转义+AST 单语句，不靠拒绝。"""
    evil = {"dataset": "attendance", "columns": ["hours"],
            "filter": [{"field": "name", "op": "eq", "value": "x'; DROP TABLE employee;--"}]}
    sql, _ = compile_smq(evil)
    conn = sqlite3.connect(DB)
    rows = conn.execute(sqlglot.transpile(sql, read="postgres", write="sqlite")[0]).fetchall()
    still = conn.execute("SELECT COUNT(*) FROM employee").fetchone()[0]
    conn.close()
    return sql, len(rows), still


def main():
    print("=" * 72)
    print("SPIKE A: SMQ -> SQL deterministic compiler (sqlglot %s)" % sqlglot.__version__)
    print("=" * 72)
    conn_check = sqlite3.connect(DB)
    n = conn_check.execute("SELECT COUNT(*) FROM employee").fetchone()[0]
    conn_check.close()
    print(f"[setup] hr_demo.db loaded, employees={n}")

    compiled = {}
    for name, smq in S.SAMPLES:
        try:
            sql, ast = compile_smq(smq)
        except Exception as e:
            print(f"\n[{name}] *** UNEXPECTED FAIL: {type(e).__name__}: {e}")
            continue
        pg = ast.sql(dialect="postgres", pretty=True)
        compiled[name] = pg
        rows, total = execute_sqlite(pg)
        print(f"\n[{name}] PG SQL (validated AST):\n{pg}")
        print(f"[{name}] executed on sqlite: {total} rows, first {len(rows)}:")
        for r in rows:
            print("   ", r)

    print("\n" + "=" * 72)
    print("NEGATIVE CASES (must all fail explicitly -> 明确失败率 100%)")
    print("=" * 72)
    fails = 0
    for name, bad in negative_cases():
        try:
            compile_smq(bad)
            print(f"[{name}] *** LEAKED THROUGH (BAD!)")
        except Exception as e:
            fails += 1
            print(f"[{name}] explicit fail OK -> {type(e).__name__}: {str(e)[:80]}")

    print("\n" + "=" * 72)
    print("INJECTION NEUTRALIZATION (anti-injection = escaping + single-statement AST)")
    print("=" * 72)
    sql, rows_n, emp_n = injection_demo()
    print(f"compiled SQL: {sql}")
    print(f"executed harmlessly: {rows_n} rows; employee table intact: {emp_n} rows")

    print(f"\nDONE. {len(compiled)}/{len(S.SAMPLES)} positive compiled+executed; {fails}/{len(negative_cases())} negative rejected; injection neutralized (table intact).")


if __name__ == "__main__":
    main()
