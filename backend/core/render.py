# -*- coding: utf-8 -*-
"""
ChatBI-Lite W4 · 结果渲染与对话层（render.py）
职责：把「权限决策 + SQL 执行结果」渲染为中文自然语言答案（Markdown 表格），
并把 LLM 意图 -> 权限 -> 编译 -> 执行 -> 渲染五环节封装为 ask()——
这是对话层的唯一入口，e2e 与未来 API 服务共用，杜绝各处重复拼链路。

纪律：
  - 渲染是纯展示层：绝不改写查询结果，MASK 列如实显示 None。
  - 空结果如实说「没有符合条件的记录」，不编造（W4 已知边界：结果为空 ≠ 出错）。
  - ask() 每一步失败都归入显式状态（refused_scope / denied / error），无静默降级。
"""
import json
import sqlite3
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dsl_loader
import smq_schema as S
import compiler as C
import perm_engine as P

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(BASE, "data", "hr_demo.db")

AGG_CN = {"sum": "总和", "avg": "平均", "count": "次数", "min": "最小", "max": "最大"}


def _display_name(ds, field):
    fm = dsl_loader.REG.own[ds].get(field) or dsl_loader.REG.inherited[ds].get(field) or {}
    return fm.get("display_name", field)


def _col_header(ds, spec):
    """SMQ 列规格 -> 中文表头（与编译器输出别名一一对应）。"""
    if isinstance(spec, str):
        return _display_name(ds, spec)
    field, agg = spec["field"], spec.get("agg")
    if agg is None:
        return _display_name(ds, field)
    return f"{_display_name(ds, field)}（{AGG_CN.get(agg, agg)}）"


def fmt_num(v):
    """数字格式化：整数千分位；浮点保留 ≤2 位并去尾零。"""
    if isinstance(v, float):
        if v == int(v):
            return f"{int(v):,}"
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    return str(v) if v is not None else "—"


def render_result(smq, rows, total, shown, verdict=None):
    """查询结果 -> 中文 Markdown 答案。rows 为元组列表（列序 = smq['columns']）。"""
    ds = smq["dataset"]
    headers = [_col_header(ds, c) for c in smq["columns"]]
    lines = []
    if total == 0:
        lines.append("查询成功，但没有符合条件的记录。")
        if smq.get("filter"):
            lines.append("\n> 💡 可以换个时间段或条件再试试；若涉及「本月/上个月」等说法，也可以直接指定月份。")
    else:
        if len(headers) == 2 and smq.get("group_by") and isinstance(smq["columns"][-1], dict):
            # 单维度 + 单聚合：一句话 + 表格（TopN 场景最常见）
            agg_spec = smq["columns"][-1]
            verb = {"sum": "合计", "avg": "平均", "count": "共"}.get(agg_spec.get("agg"), "")
            lines.append(f"按{headers[0]}汇总（{headers[1]}，共 {total:,} 组）：\n")
        else:
            lines.append(f"共 {total:,} 条记录：\n" if total > shown else f"共 {total:,} 条记录（显示前 {shown} 条）：\n")
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "---|" * len(headers))
        for r in rows:
            cells = [fmt_num(v) for v in r]
            lines.append("| " + " | ".join(cells) + " |")
    if verdict and verdict.get("decision") == "MASK":
        lines.append(f"\n> 🔒 敏感列已按权限脱敏（{verdict.get('reason', '')}）。")
    return "\n".join(lines)


def _interpret(raw):
    """LLM 原始输出 -> (kind, payload)。kind: smq / refused_scope / clarify / invalid。
    纯函数（不碰 LLM/DB），test_dsl_v1.py 直接单测。
    clarify 是 W5 新契约：LLM 对歧义问题反问用户，绝不带任何查询字段——
    即使带了也会被丢弃（fail-closed：澄清分支永远不执行查询）。"""
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as e:
        return "invalid", f"输出不是合法 JSON：{e}"
    if not isinstance(obj, dict):
        return "invalid", "输出不是 JSON 对象"
    if obj.get("error") == "OUT_OF_SCOPE":
        return "refused_scope", obj.get("reason", "")
    if obj.get("clarify") is True:
        q = str(obj.get("question") or "").strip()
        if not q:
            return "invalid", "clarify=true 但缺 question"
        return "clarify", {"question": q, "options": [str(o) for o in (obj.get("options") or [])]}
    return "smq", obj


# ---------------------------------------------------------------- W6: 环比（compare_to）
def _pct(cur, prev):
    """环比百分比文本。prev 缺失/为 0 时如实显示「—」（绝不编造增长率）。"""
    if prev is None or prev == 0:
        return "—"
    p = (cur - prev) / prev * 100.0
    return f"+{p:.1f}%" if p >= 0 else f"{p:.1f}%"


def _answer_compare(smq, conn, verdict=None):
    """环比查询执行：本期/上期两条平移 SQL + 结果侧合并计算。
    安全性：权限只对基础 SMQ 决策一次；平移仅改变时间字面量，字段/角色面完全不变，
    因此不存在「环比绕过权限」的新攻击面（上期查询 = 本期查询的平移副本）。"""
    import copy
    n = smq["compare_to"]["months_back"]
    base_smq = {k: v for k, v in smq.items() if k != "compare_to"}
    base_smq.pop("limit", None)                       # 合并后统一按原 limit 截断
    prev_smq = copy.deepcopy(base_smq)
    sql_cur, _ = C.compile_smq(base_smq, month_back=0)
    sql_prev, _ = C.compile_smq(prev_smq, month_back=n)
    lite_cur = __import__("sqlglot").transpile(sql_cur, read="postgres", write="sqlite")[0]
    lite_prev = __import__("sqlglot").transpile(sql_prev, read="postgres", write="sqlite")[0]
    rows_cur = conn.execute(lite_cur).fetchall()
    rows_prev = conn.execute(lite_prev).fetchall()

    ds = smq["dataset"]
    specs = smq["columns"]
    gcols = sum(1 for c in specs if isinstance(c, str) or not c.get("agg"))   # 分组列数
    agg_specs = [(i, c) for i, c in enumerate(specs) if isinstance(c, dict) and c.get("agg")]

    prev_map = {tuple(r[:gcols]): r for r in rows_prev}
    merged, missing_prev = [], 0
    for r in rows_cur:
        key = tuple(r[:gcols])
        pr = prev_map.get(key)
        out = list(r)
        for i, _spec in agg_specs:
            pv = pr[i] if pr else None
            if pr is None:
                missing_prev += 1
            out.append(pv)
            out.append(_pct(r[i], pv))
        merged.append(tuple(out))
    if smq.get("limit"):
        merged = merged[:smq["limit"]]

    headers = [_col_header(ds, c) for c in specs[:gcols]]
    for i, spec in agg_specs:
        h = _col_header(ds, spec)
        headers += [f"{h}·本期", f"{h}·上期", f"{h}·环比"]
    lines = [f"环比对照（本期 = {smq['time_window']['preset']}，上期 = 前推 {n} 个月，共 {len(rows_cur):,} 组）：\n"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "---|" * len(headers))
    for row in merged:
        lines.append("| " + " | ".join(fmt_num(v) for v in row) + " |")
    if missing_prev:
        lines.append(f"\n> ℹ️ 有 {missing_prev:,} 个分组在上期无数据，环比显示「—」（上期为 0 或缺失时不计算增长率，避免误导）。")
    if not rows_cur:
        lines.insert(0, "查询成功，但本期没有符合条件的记录，无法计算环比。")
        del lines[2:]
    if verdict and verdict.get("decision") == "MASK":
        lines.append(f"\n> 🔒 敏感列已按权限脱敏（{verdict.get('reason', '')}）。")
    return {"status": "answered", "text": "\n".join(lines), "smq": smq, "sql": sql_cur}


def ask(question: str, api_key: str, ctx: dict, conn: sqlite3.Connection = None):
    """对话层唯一入口：NL -> 六状态 -> {status, text, smq?, sql?}。
    status: answered / refused_scope / clarify / denied / error
    conn 可选传入（R4 需要查员工姓名；不传则跳过 R4 的名字比对，保守拒绝）。"""
    own_conn = conn is None
    c = conn or sqlite3.connect(DB)
    try:
        import llm_intent as L
        raw = L.call_deepseek(question, api_key)                       # 环节1: LLM 意图
        kind, payload = _interpret(raw)                                # 环节1b: 契约分派
        if kind == "refused_scope":
            return {"status": "refused_scope",
                    "text": f"该问题超出了当前数据范围，无法回答：{payload}"}
        if kind == "clarify":
            opts = "".join(f"\n- {o}" for o in payload["options"])
            return {"status": "clarify",
                    "text": f"为了给您准确的答案，请先确认：{payload['question']}{opts}"}
        if kind == "invalid":
            return {"status": "error", "text": f"查询失败（意图解析异常）：{payload}"}
        smq = payload
        verdict = P.decide(smq, ctx, conn=c)                            # 环节2: 权限三态（授权先于检索）
        if verdict["decision"] == "DENY":
            return {"status": "denied",
                    "text": f"没有权限执行该查询（{verdict['reason']}）。"}
        if smq.get("compare_to"):                                       # 环节3-5: 环比双查询路径
            return _answer_compare(smq, c, verdict)
        sql, _ast = C.compile_smq(smq)                                  # 环节3+4: 校验+确定性编译
        sql_lite = __import__("sqlglot").transpile(sql, read="postgres", write="sqlite")[0]
        rows = c.execute(sql_lite).fetchall()                           # 环节5: 真实执行
        total = len(rows)
        shown = min(total, 20)
        text = render_result(smq, rows[:shown], total, shown, verdict)
        return {"status": "answered", "text": text, "smq": smq, "sql": sql}
    except Exception as e:
        return {"status": "error", "text": f"查询失败（{type(e).__name__}）：{str(e)[:150]}"}
    finally:
        if own_conn:
            c.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"render smoke: model={dsl_loader.REG.version}")
    # 三形态渲染冒烟：明细 / 聚合 TopN / 空结果
    q_detail = {"dataset": "employee", "columns": ["name", "level", "hire_date"],
                "filter": [{"field": "hire_date", "op": "ge", "value": "2026-08-01"},
                           {"field": "hire_date", "op": "lt", "value": "2026-09-01"}],
                "order_by": [{"field": "hire_date", "dir": "asc"}]}
    rows, total = C.execute_sqlite(C.compile_smq(q_detail)[0], limit_print=3)
    print(render_result(q_detail, rows, total, len(rows)))
    print()
    q_empty = {"dataset": "employee", "columns": ["name"],
               "filter": [{"field": "hire_date", "op": "ge", "value": "2030-01-01"}]}
    print(render_result(q_empty, [], 0, 0))
    print("\nrun test_dsl_v1.py + eval_harness.py for the full checks.")
