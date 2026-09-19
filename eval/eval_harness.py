# -*- coding: utf-8 -*-
"""
ChatBI-Lite W4/W5 · eval harness（dev-plan W5 M1 口径一致率基线的承载脚本）
评测对象是**全链路**而非单模型：LLM 意图 + 校验归一化 + 权限三态 + 编译执行。

两种模式：
  python eval_harness.py            离线模式：回放 eval_fixtures.json 里录制的 LLM 原始输出，
                                    跑全部确定性断言（run_all.py 用这个，零 API 依赖）。
  python eval_harness.py --live     实测模式：调 DeepSeek 跑全部 case，断言通过后**更新 fixtures**。

case 语义（expect.type）：
  out_of_scope —— 期望设计内拒答（越界题答不上来 = PASS）
  smq          —— 期望产出合法 SMQ 且满足结构断言（dataset / has_col / has_filter / group_by / limit）
  flexible     —— 多可接受结果（W5 clarify 契约：歧义题 clarify / 缺省 / 拒答均可能合格），任一满足即 PASS
  denied       —— 期望权限引擎 DENY（R4 等）
  zero_trap_caught —— 守门自评（评测器自身有效性）
口径一致率（W5 M1，dev-plan 8.2）：
  分母 = 全部 smq / flexible 型 LLM 意图题（应有明确正确行为的可回答/该澄清题）；
  分子 = 语义判定 PASS 的题数。拒答面、安全面、守门自评不计入。
  门槛 ≥80% 达标；未达标切「基线 +10pp」替代标准（dev-plan 8.2 已预置）。
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "core"))
import dsl_loader
import smq_schema as S
import perm_engine as P
import compiler as C

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "eval_fixtures.json")
DB = os.path.join(os.path.dirname(HERE), "data", "hr_demo.db")

# ------------------------------------------------------------------ case 定义
# has_col:  (field, agg or None)   agg="*" 表示任意聚合均可
# has_filter: (field, op)         只断言「存在该过滤」，不断言值（值由 LLM 自由解析）
CASES = [
    {"id": "Q1-detail",      "q": "王伟 2026 年 8 月的考勤记录？",
     "expect": {"type": "smq", "dataset": "attendance",
                "has_col": [("work_date", None), ("late_min", "*")],
                "has_filter": [("name", "eq"), ("work_date", "ge")]}},
    {"id": "Q2-agg-join",    "q": "各部门 8 月平均工时，从高到低？",
     "expect": {"type": "smq", "dataset": "attendance",
                "has_col": [("hours", "avg")], "group_by_any": True,
                "has_filter": [("work_date", "ge")]}},
    {"id": "Q3-topN",        "q": "8 月迟到总分钟数最多的 10 名员工？",
     "expect": {"type": "smq", "dataset": "attendance",
                "has_col": [("late_min", "sum")], "limit": 10,
                "has_filter": [("work_date", "ge")]}},
    {"id": "Q4-out-export",  "q": "帮我把工资最高的员工名单导出成 Excel",
     "expect": {"type": "out_of_scope"}},
    {"id": "Q5-out-domain",  "q": "帮我查一下今天公司的股价多少？",
     "expect": {"type": "out_of_scope"}},
    {"id": "Q6-newhires",    "q": "2026 年 8 月入职的员工有哪些？",
     "expect": {"type": "smq", "dataset": "employee",
                "has_col": [("hire_date", None)],
                "has_filter": [("hire_date", "ge")]}},
    {"id": "Q7-leavebal",    "q": "8 月请年假的员工，他们的假期余额还剩多少？",
     "expect": {"type": "smq", "dataset": "leave_balance",
                "has_col": [("quota", None), ("used", None)],
                "has_filter": [("leave_type", "eq")]}},
    {"id": "Q8-late-lastmo", "q": "上个月谁老迟到？",
     "expect": {"type": "smq", "dataset": "attendance",
                "has_col": [("late_min", "*")],
                "has_filter": [("work_date", "ge")]}},
    {"id": "Q9-count-late",  "q": "李娜 8 月迟到几次？",
     "expect": {"type": "smq", "dataset": "attendance", "zero_trap_guard": True,
                # 两种合法形态：count(emp_id)（迟到天数）或 count(late_min)+late_min>0（同义）
                "or_col": [[("emp_id", "count")], [("late_min", "count")]],
                "has_filter": [("name", "eq"), ("late_min", "gt")]}},
    {"id": "Q10-two-agg",    "q": "全公司 8 月总工时和总迟到分钟数？",
     "expect": {"type": "smq", "dataset": "attendance",
                "has_col": [("hours", "sum"), ("late_min", "sum")]}},
    # ---------------- W5 口径题集（M1 口径一致率基线的分母构成） ----------------
    # 口径题 1：总工时 vs 平均工时——与 Q2 同数据不同聚合，专测口径混淆
    {"id": "C1-total-hours", "q": "各部门 2026 年 8 月总工时排名？",
     "expect": {"type": "smq", "dataset": "attendance",
                "has_col": [("hours", "sum")], "group_by_any": True,
                "has_filter": [("work_date", "ge")]}},
    # 口径题 2：实发工资 = payroll.net_pay（不是 employee.salary 当前时点口径）
    {"id": "C2-netpay",      "q": "张伟 2026 年 8 月的实发工资是多少？",
     "expect": {"type": "smq", "dataset": "payroll",
                "has_col": [("net_pay", None)],
                "has_filter": [("name", "eq")],
                "any_filter": [("pay_month", "eq"), ("pay_month", "ge")]}},
    # 口径题 3：部门平均实发——critical 字段 avg 开放（sum 封闭），group_by 部门
    # pay_month 的 eq 与 ge/lt 区间写法均合法（any_filter）
    {"id": "C3-dept-netpay", "q": "2026 年 8 月研发部平均实发工资？",
     "expect": {"type": "smq", "dataset": "payroll",
                "has_col": [("net_pay", "avg")],
                "any_filter": [("pay_month", "eq"), ("pay_month", "ge")]}},
    # 口径题 4：剩余假期 = quota − used（OQ3 派生口径，v1 映射规则：两列都要出）
    {"id": "C4-remain",      "q": "李娜年假还剩几天？",
     "expect": {"type": "smq", "dataset": "leave_balance",
                "has_col": [("quota", None), ("used", None)],
                "has_filter": [("name", "eq"), ("leave_type", "eq")]}},
    # 口径题 5：人数 = count(id)（employee 主键），维度过滤走虚拟列 dept_name
    # （W5 实测发现：模型若用 dept_id 会因不知 id 映射而填 null——已加提示词规则 11 纠偏）
    {"id": "C5-hc",          "q": "研发部有多少名员工？",
     "expect": {"type": "smq", "dataset": "employee",
                "has_col": [("id", "count")],
                "has_filter": [("dept_name", "eq")]}},
    # 口径题 6：阈值明细——迟到超过 30 分钟
    {"id": "C6-late30",      "q": "8 月有迟到超过 30 分钟的员工有哪些？",
     "expect": {"type": "smq", "dataset": "attendance",
                "has_col": [("name", None)],
                "has_filter": [("late_min", "gt"), ("work_date", "ge")]}},
    # 口径题 7：critical 基本工资 avg 开放——顺带验证模型不会选被封的 sum
    {"id": "C7-base-avg",    "q": "全公司 2026 年 8 月平均基本工资？",
     "expect": {"type": "smq", "dataset": "payroll",
                "has_col": [("base_salary", "avg")],
                "any_filter": [("pay_month", "eq"), ("pay_month", "ge")]}},
    # 口径题 8：职级维度 + 平均迟到分钟
    {"id": "C8-level-late",  "q": "各职级 8 月平均迟到分钟数？",
     "expect": {"type": "smq", "dataset": "attendance",
                "has_col": [("late_min", "avg")], "group_by_any": True,
                "has_filter": [("work_date", "ge")]}},
    # ---------------- W5 澄清反问 v1（clarify 契约行为题） ----------------
    # 澄清题 1：「休假情况」时间未指明——可全量查余额（合理缺省），也可反问，两者都算合格
    {"id": "CL1-leave-vague", "q": "王伟的休假情况怎么样？",
     "expect": {"type": "flexible", "accept": [
         {"type": "smq", "dataset": "leave_balance", "has_filter": [("name", "eq")]},
         {"type": "clarify"}]}},
    # 澄清题 2：「出勤情况好吗」指标不明——瞎猜聚合口径会误导，clarify 或拒答都合格，产出具体 SMQ 反而不合格
    {"id": "CL2-att-vague",  "q": "公司的出勤情况好吗？",
     "expect": {"type": "flexible", "accept": [
         {"type": "clarify"}, {"type": "out_of_scope"}]}},
    # 澄清题 3：跨实体对比不可表达（W6 后仅时间环比可表达，见 CP1）——必须澄清或拒答，
    # 或使用 compare_to 环比形式；拿单区间冒充对比仍不合格
    {"id": "CL3-compare",    "q": "对比一下 8 月和 9 月的迟到情况？",
     "expect": {"type": "flexible", "accept": [
         {"type": "out_of_scope"}, {"type": "clarify"},
         {"type": "smq", "dataset": "attendance", "has_compare": 1}]}},
    # ---------------- W6 时间窗口 + 环比（编译器 v2） ----------------
    # TW1：相对时间优先 time_window 预设；回退到自算绝对区间也算合格（规则 13 允许两种形态）
    {"id": "TW1-lastmonth",  "q": "上个月各部门平均工时，从高到低？",
     "expect": {"type": "flexible", "accept": [
         {"type": "smq", "dataset": "attendance", "has_col": [("hours", "avg")],
          "has_tw": "last_month", "group_by_any": True},
         {"type": "smq", "dataset": "attendance", "has_col": [("hours", "avg")],
          "has_filter": [("work_date", "ge")], "group_by_any": True}]}},
    # CP1：环比 = time_window + compare_to（8 月相对锚点 = last_month，较上月 = months_back 1）
    {"id": "CP1-mom",        "q": "各部门 8 月平均实发工资，环比 7 月变化多少？",
     "expect": {"type": "smq", "dataset": "payroll",
                "has_col": [("net_pay", "avg")],
                "has_tw": "last_month", "has_compare": 1}},
    # 权限 case：普通员工指名道姓查他人（李娜）考勤 -> R4 DENY（不走 LLM，直接构造 SMQ）
    # 注意 emp_id=1001 本人就叫王伟——查王伟对 1001 是查本人，必须换他人名才能测 R4。
    {"id": "X1-r4-other",    "q": "[权限case] employee 查「李娜 8 月考勤」",
     "smq": {"dataset": "attendance", "columns": ["work_date", "hours", "name"],
             "filter": [{"field": "name", "op": "eq", "value": "李娜"},
                        {"field": "work_date", "op": "ge", "value": "2026-08-01"},
                        {"field": "work_date", "op": "lt", "value": "2026-09-01"}]},
     "ctx": {"role": "employee", "emp_id": 1001},
     "expect": {"type": "denied"}},
    # zero-trap 守门有效性：一个故意不带 late_min>0 的 count(late_min) 查询，
    # 必须被守门断言抓住（守门规则自身也要被测——评测器不能是不受评的裁判）。
    {"id": "X2-zero-trap",   "q": "[语义case] count(late_min) 缺 late_min>0 过滤必须被守门抓住",
     "smq": {"dataset": "attendance", "columns": ["name", {"field": "late_min", "agg": "count"}],
             "filter": [{"field": "work_date", "op": "ge", "value": "2026-08-01"},
                        {"field": "work_date", "op": "lt", "value": "2026-09-01"}],
             "group_by": ["name"]},
     "ctx": {"role": "hr_admin"},
     "expect": {"type": "zero_trap_caught"}},
]


# ------------------------------------------------------------------ 断言器
def assert_smq(expect, smq):
    """结构断言。返回 (ok, note)。"""
    ds = smq.get("dataset")
    if expect.get("dataset") and ds != expect["dataset"]:
        return False, f"dataset={ds!r} != {expect['dataset']!r}"
    cols = []
    for c in smq.get("columns", []):
        if isinstance(c, str):
            cols.append((c, None))
        else:
            cols.append((c["field"], c.get("agg") or None))
    for field, agg in expect.get("has_col", []):
        hit = [a for f, a in cols if f == field and (agg == "*" or a == agg)]
        if not hit:
            return False, f"missing col ({field}, {agg}) in {cols}"
    if expect.get("or_col"):
        combos = expect["or_col"]
        satisfied = []
        for combo in combos:
            if all(any(f == cf and (a == "*" or ca == a) for f, ca in cols) for cf, a in combo):
                satisfied.append(combo)
        if not satisfied:
            return False, f"no or_col combo {combos} satisfied by {cols}"
    filters = [(f["field"], f["op"]) for f in smq.get("filter", [])]
    for field, op in expect.get("has_filter", []):
        if (field, op) not in filters:
            return False, f"missing filter ({field},{op}) in {filters}"
    if expect.get("any_filter"):
        # 任一 (field, op) 出现即可——month 等字段 eq 与 ge/lt 区间均为合法写法
        if not any((f, o) in filters for f, o in expect["any_filter"]):
            return False, f"none of any_filter {expect['any_filter']} in {filters}"
    if expect.get("group_by_any") and not smq.get("group_by"):
        return False, "expected non-empty group_by"
    if expect.get("has_tw"):
        tw = smq.get("time_window") or {}
        if tw.get("preset") != expect["has_tw"]:
            return False, f"time_window preset={tw.get('preset')!r} != {expect['has_tw']!r}"
    if expect.get("has_compare") is not None:
        ct = smq.get("compare_to") or {}
        if ct.get("months_back") != expect["has_compare"]:
            return False, f"compare_to={ct!r} != months_back {expect['has_compare']}"
    if expect.get("limit") is not None and smq.get("limit") != expect["limit"]:
        return False, f"limit={smq.get('limit')!r} != {expect['limit']}"
    if expect.get("zero_trap_guard"):
        # count(late_min) 类查询必须带 late_min>0 过滤，否则把迟到次数算成出勤天数
        fset = {(f["field"], f["op"]) for f in smq.get("filter", [])}
        has_count_trap = any(f == "late_min" and a == "count" for f, a in cols)
        if has_count_trap and ("late_min", "gt") not in fset:
            return False, "ZERO-TRAP: count(late_min) without late_min>0 filter"
    return True, "OK"


def _check_accept(acc, smq):
    """flexible accept 单项检查。返回 (ok, note)。"""
    t = acc.get("type")
    if t == "clarify":
        return ("PASS", "clarify 反问") if smq.get("clarify") is True and str(smq.get("question") or "").strip() \
            else (False, "expected clarify contract")
    if t == "out_of_scope":
        return ("PASS", "正确拒答") if smq.get("error") == "OUT_OF_SCOPE" \
            else (False, f"expected OUT_OF_SCOPE, got {smq.get('dataset') or smq.get('clarify') and 'clarify'}")
    # type == smq
    if smq.get("error") == "OUT_OF_SCOPE" or smq.get("clarify") is True:
        return False, "expected a concrete SMQ"
    try:
        S.validate(smq)
    except S.SmqError as e:
        return False, f"SmqError: {e}"
    return assert_smq(acc, smq)


def run_case(case, raw_or_smq, conn):
    """对单个 case 跑全链路。返回 (verdict, note)。verdict: PASS/FAIL。"""
    expect = case["expect"]
    if expect["type"] == "denied":
        smq = dict(raw_or_smq)
        try:
            S.validate(smq)
        except S.SmqError as e:
            return "FAIL", f"SMQ invalid: {e}"
        v = P.decide(smq, case["ctx"], conn=conn)
        return ("PASS", f"{v['decision']}: {v['reason']}") if v["decision"] == "DENY" \
            else ("FAIL", f"expected DENY, got {v['decision']}")
    # LLM 产物（或回放的 fixture）：先解析
    if isinstance(raw_or_smq, str):
        try:
            smq = json.loads(raw_or_smq)
        except json.JSONDecodeError as e:
            return "FAIL", f"not JSON: {e}"
    else:
        smq = raw_or_smq
    if expect["type"] == "out_of_scope":
        if smq.get("error") == "OUT_OF_SCOPE":
            return "PASS", "正确拒答"
        return "FAIL", f"expected OUT_OF_SCOPE, got SMQ for {smq.get('dataset')}"
    # flexible：多可接受结果（clarify 契约引入后，部分歧义题存在多种合格行为）
    if expect["type"] == "flexible":
        if smq.get("error") == "OUT_OF_SCOPE":
            smq = {"error": "OUT_OF_SCOPE"}
        notes = []
        for acc in expect["accept"]:
            ok, note = _check_accept(acc, smq)
            if ok:
                return "PASS", note
            notes.append(note)
        return "FAIL", "no acceptable behavior: " + "; ".join(notes)
    # type == smq
    if smq.get("error") == "OUT_OF_SCOPE":
        return "FAIL", "unexpectedly refused (should be answerable)"
    if smq.get("clarify") is True:
        return "FAIL", "unexpectedly asked clarification (should be answerable)"
    try:
        S.validate(smq)                                   # 归一化发生在这里（group_by 度量剔除等）
    except S.SmqError as e:
        return "FAIL", f"SmqError: {e}"
    if expect["type"] == "zero_trap_caught":
        # 守门有效性：坏 SMQ 必须被 zero_trap 断言抓住（评测器自评）
        ok, note = assert_smq({"zero_trap_guard": True}, smq)
        return ("PASS", "zero-trap 守门生效（坏查询被抓住）") if not ok and "ZERO-TRAP" in note \
            else ("FAIL", f"guard did not catch: ok={ok}, note={note}")
    ok, note = assert_smq(expect, smq)
    if not ok:
        return "FAIL", note
    # 语义级验证：编译必须成功（SQL 层可执行）
    try:
        C.compile_smq(smq)
    except Exception as e:
        return "FAIL", f"compile failed: {type(e).__name__}: {str(e)[:100]}"
    return "PASS", note


def main():
    live = "--live" in sys.argv
    conn = sqlite3.connect(DB)
    results = []
    if live:
        import llm_intent as L
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            print("ERROR: DEEPSEEK_API_KEY not set (check chatbi-lite-sprint0/.env).")
            return 1
        fixtures = {}
    else:
        if not os.path.exists(FIXTURES):
            print(f"ERROR: {FIXTURES} not found. Run `python eval_harness.py --live` once to record.")
            return 1
        with open(FIXTURES, encoding="utf-8") as f:
            fixtures = json.load(f)

    for case in CASES:
        cid = case["id"]
        if "smq" in case:                                  # 权限/语义构造 case：不走 LLM
            raw = case["smq"]
        elif live:
            try:
                raw = L.call_deepseek(case["q"], api_key)
            except Exception as e:                         # API 抖动不吞结果：显式 FAIL
                raw = None
                print(f"  ❌ {cid:<16} [FAIL] API error: {type(e).__name__}: {str(e)[:80]}")
                results.append((cid, "FAIL", "api error"))
                continue
            fixtures[cid] = raw
        else:
            raw = fixtures.get(cid)
            if raw is None:
                print(f"[{cid}] FAIL: no fixture recorded (run --live)")
                results.append((cid, "FAIL", "no fixture"))
                continue
        v, note = run_case(case, raw, conn)
        mark = "✅" if v == "PASS" else "❌"
        print(f"  {mark} {cid:<16} [{v}] {note}")
        results.append((cid, v, note))
    conn.close()

    npass = sum(1 for _, v, _ in results if v == "PASS")
    total = len(results)
    print("\n" + "=" * 72)
    print(f"EVAL RESULT: {npass}/{total} cases passed "
          f"(mode={'live+recorded' if live else 'fixture replay'}; model={dsl_loader.REG.version})")
    # ---- W5 M1：口径一致率基线（dev-plan 8.2） ----
    # 口径：分母 = 全部「应有明确正确行为」的 LLM 意图题（smq / flexible 型，即能回答或该澄清/拒答的题）；
    #       分子 = 其中语义判定 PASS 的题数。
    # out_of_scope 型（拒答面）、denied / zero_trap_caught（安全面/守门自评，构造 case）不计入。
    baseline_ids = [c["id"] for c in CASES if c["expect"]["type"] in ("smq", "flexible") and "smq" not in c]
    b_pass = sum(1 for cid, v, _ in results if cid in baseline_ids and v == "PASS")
    b_total = len(baseline_ids)
    rate = b_pass / b_total * 100 if b_total else 0.0
    verdict_m1 = "达标 ✅" if rate >= 80 else "未达标（切「基线 +10pp」替代标准）⚠"
    print(f"口径一致率基线（W5 M1）: {b_pass}/{b_total} = {rate:.1f}%   [门槛 80% -> {verdict_m1}]")
    if live:
        with open(FIXTURES, "w", encoding="utf-8") as f:
            json.dump(fixtures, f, ensure_ascii=False, indent=1)
        print(f"fixtures updated: {FIXTURES}")
    print("=" * 72)
    return 0 if npass == total else 1


if __name__ == "__main__":
    sys.exit(main())
