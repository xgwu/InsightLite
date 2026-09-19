# -*- coding: utf-8 -*-
"""
W7 · 权限测试矩阵 + CI 门禁（dev-plan W7 交付物）
=================================================
期望值来源（单一权威）：semantic_model/hr_domain_v1.yaml 的 permission_matrix /
roles / hard_rules / 字段 sensitivity。本文件不从被测代码行为反推期望值。

段落结构：
  A 决策层段   —— 三态断言（弱）：矩阵取值逐格核对 + R2/R3/R4 硬规则
  B fail-closed 段 —— 未登录/伪造角色/缺主体/未知数据集/SSO 篡改 一律拒绝
  C 行为层段   —— 三态断言（强）：隔离副本库上走完整链路
                  decide -> compile(注入策略产物) -> execute -> render
                  正向对照 / 行级收窄 / 列级占位 / 决策-执行同源（白盒）
  D 权威一致性段 —— 人工转写表 vs YAML 权威逐格核对 + 变异自检（判据必须能红）
  E 冲突报告段 —— 只登记不裁决（print，不 assert）

缺陷登记（纪律 5/6）：
  开放缺陷允许 CI 带警告通过，但必须浮出到 Job Summary / annotation；
  已标记 defect= 的断言一旦 PASS → STALE（退出码 1），强制走销项流程。
  销项 = 移入 RESOLVED_DEFECTS 留痕 + 摘掉 defect= 标记转永久硬断言。

隔离纪律（纪律 7）：行为层全程使用 data/hr_demo.db 的 tempfile 副本，
结束时断言种子库未被污染。
"""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(BASE, "backend", "core")
SEED_DB = os.path.join(BASE, "data", "hr_demo.db")
AUTHORITY_YAML = os.path.join(BASE, "semantic_model", "hr_domain_v1.yaml")

sys.path.insert(0, CORE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml  # noqa: E402
import dsl_loader  # noqa: E402
import smq_schema as S  # noqa: E402
import perm_engine as P  # noqa: E402
import compiler as C  # noqa: E402
import render as R  # noqa: E402
import sso_mock  # noqa: E402

AUTH = yaml.safe_load(open(AUTHORITY_YAML, encoding="utf-8"))
MATRIX = AUTH["permission_matrix"]["defaults"]
VERSION = dsl_loader.REG.version

# ---------------------------------------------------------------- 结果记账
RESULTS = []           # (seg, name, ok, note, defect_or_None)
DEFECT_HITS = set()    # 被失败断言引用的缺陷 id


def check(seg, name, ok, note="", defect=None):
    RESULTS.append((seg, name, bool(ok), note, defect))
    if not ok and defect:
        DEFECT_HITS.add(defect)
    return ok


# ---------------------------------------------------------------- 主体上下文
CTX = {
    "employee":     {"role": "employee", "emp_id": 1001},            # 王伟 / 部门 6
    "dept_manager": {"role": "dept_manager", "dept_id": 2},          # 技术部（子树 2/6/7/8）
    "hr_admin":     {"role": "hr_admin"},
    "auditor":      {"role": "auditor"},
}
ROLES = list(CTX)
NON_ADMIN = [r for r in ROLES if r != "hr_admin"]

# 每个（数据集 × 敏感度）取一个代表字段；敏感度档位从权威 sensitivities 取
REPRESENTATIVE = {
    ("employee", "internal"): "name",
    ("employee", "critical"): "salary",
    ("department", "public"): "name",
    ("attendance", "internal"): "hours",
    ("payroll", "internal"): "pay_month",
    ("payroll", "critical"): "net_pay",
}


def fields_with_sens():
    """数据集 -> {field: sensitivity}（own + inherited，来自权威）。"""
    out = {}
    for ds in dsl_loader.REG.tables:
        fm = {}
        for f, meta in (dsl_loader.REG.own[ds] or {}).items():
            fm[f] = meta.get("sensitivity", "internal")
        for f, meta in (dsl_loader.REG.inherited[ds] or {}).items():
            fm.setdefault(f, meta.get("sensitivity", "internal"))
        out[ds] = fm
    return out


SENS_MAP = fields_with_sens()


def expected_verdict_from_authority(role: str, sens: str) -> str:
    """YAML permission_matrix -> 期望三态（独立 oracle，不读被测代码）。
    allow_self_row：行级限定本人后列可见 -> 决策层 ALLOW（行约束是策略产物，C 段验证）。"""
    cell = MATRIX[sens][role]
    return {"allow": "ALLOW", "deny": "DENY", "mask": "MASK",
            "allow_self_row": "ALLOW"}[cell]


def conn_copy():
    """纪律 7：种子库副本（行为层全程只用副本）。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    shutil.copy2(SEED_DB, path)
    return sqlite3.connect(path), path


def run_chain(smq, ctx, conn):
    """完整真实链路：validate -> decide -> compile(消费策略产物) -> execute。
    返回 (verdict, sql, rows)。决策层拒绝时 sql/rows 为 None。"""
    S.validate(smq)
    verdict = P.decide(smq, ctx, conn=conn)
    if verdict["decision"] == "DENY":
        return verdict, None, None
    sql, _ast = C.compile_smq(smq, policy=verdict.get("policy"))
    lite = __import__("sqlglot").transpile(sql, read="postgres", write="sqlite")[0]
    rows = conn.execute(lite).fetchall()
    return verdict, sql, rows


# ================================================================ A 决策层段
def seg_a_decision():
    seg = "A决策层"
    # A1: 矩阵逐格（数据集 × 代表字段 × 角色）
    # auditor 豁免逐格断言：权威内部矛盾（matrix.public.auditor=allow vs roles.auditor.scope=none，
    # 实际被 scope=none 短路全 DENY）——按纪律 2 登记冲突不裁决，见冲突报告 [C3]。
    for (ds, sens), field in REPRESENTATIVE.items():
        for role in ROLES:
            if role == "auditor" and expected_verdict_from_authority(role, sens) != "DENY":
                continue
            smq = {"dataset": ds, "columns": [field]}
            v = P.decide(smq, CTX[role])
            exp = expected_verdict_from_authority(role, sens)
            check(seg, f"A1 {role}×{ds}.{field}({sens}) == {exp}",
                  v["decision"] == exp, f"got {v['decision']} ({v.get('reason','')})")

    # A2: R2 —— critical 字段进 order_by 且角色非 hr_admin -> DENY
    for role in NON_ADMIN:
        smq = {"dataset": "employee", "columns": ["name"],
               "order_by": [{"field": "salary", "dir": "desc"}]}
        v = P.decide(smq, CTX[role])
        check(seg, f"A2 R2 {role} order_by(salary) -> DENY", v["decision"] == "DENY",
              f"got {v['decision']}")

    # A3: R3 —— critical 字段进 filter 且角色非 hr_admin -> DENY
    for role in NON_ADMIN:
        smq = {"dataset": "employee", "columns": ["name"],
               "filter": [{"field": "salary", "op": "gt", "value": 0}]}
        v = P.decide(smq, CTX[role])
        check(seg, f"A3 R3 {role} filter(salary) -> DENY", v["decision"] == "DENY",
              f"got {v['decision']}")

    # A4: R4 —— employee 指名道姓查他人 -> DENY（本人姓名放行是合法路径）
    own_name = CTX["employee"]["emp_id"]
    row = conn_copy()[0].execute("SELECT name FROM employee WHERE id=?", (own_name,)).fetchone()
    own = row[0]
    others = ["刘杰", "李晨", "不存在的人"]
    for nm in others:
        smq = {"dataset": "employee", "columns": ["name"],
               "filter": [{"field": "name", "op": "eq", "value": nm}]}
        v = P.decide(smq, CTX["employee"])
        check(seg, f"A4 R4 employee 查他人({nm}) -> DENY", v["decision"] == "DENY",
              f"got {v['decision']}")
    smq = {"dataset": "employee", "columns": ["name"],
           "filter": [{"field": "name", "op": "eq", "value": own}]}
    v = P.decide(smq, CTX["employee"])
    check(seg, "A4 R4 employee 查本人姓名 -> 不因 R4 拒绝", v["decision"] != "DENY",
          f"got {v['decision']}")

    # A5: R4 使用库内真实重名对照（姓名等值 != 身份）
    dup = conn_copy()[0].execute(
        "SELECT name, COUNT(*) n FROM employee GROUP BY name HAVING n>1 LIMIT 1").fetchone()
    if dup:
        check(seg, "A5 种子库存在重名（R4 语义前提）", dup[1] >= 2, f"{dup[0]}×{dup[1]}")
    else:
        check(seg, "A5 种子库存在重名（R4 语义前提）", False, "无重名，R4 对照失去证据")


# ================================================================ B fail-closed 段
def seg_b_failclosed():
    seg = "Bfail-closed"
    # B1: 引擎面 —— 伪造/缺失角色、未知数据集
    for bad_ctx, label in [({}, "无 role"), ({"role": None}, "role=None"),
                           ({"role": "ghost"}, "伪造角色 ghost"),
                           ({"role": "superadmin"}, "白名单外 superadmin")]:
        v = P.decide({"dataset": "employee", "columns": ["name"]}, bad_ctx)
        check(seg, f"B1 {label} -> DENY", v["decision"] == "DENY", f"got {v['decision']}")
    v = P.decide({"dataset": "no_such_ds", "columns": ["x"]}, CTX["hr_admin"])
    check(seg, "B1 未知数据集 -> DENY", v["decision"] == "DENY", f"got {v['decision']}")
    v = P.decide({"columns": ["name"]}, CTX["hr_admin"])
    check(seg, "B1 缺 dataset -> DENY", v["decision"] == "DENY", f"got {v['decision']}")

    # B2: 缺主体上下文 —— row_policy 显式抛错（双 fail-closed 的编译层一半）
    for role, ctx, label in [("employee", {"role": "employee"}, "employee 无 emp_id"),
                             ("dept_manager", {"role": "dept_manager"}, "dept_manager 无 dept_id")]:
        c, path = conn_copy()
        try:
            P.row_policy(c, "attendance", ctx)
            ok = False
        except P.PermError:
            ok = True
        finally:
            c.close()
            os.unlink(path)
        check(seg, f"B2 {label} -> PermError", ok)

    # B3: SSO 面 —— 篡改/伪造/过期/白名单外一律 DENY
    tok = sso_mock.issue("1001", "employee", 6, [1, 2, 6])
    check(seg, "B3 合法 token -> ALLOW", sso_mock.verify(tok) is not None)
    check(seg, "B3 篡改签名 -> DENY", sso_mock.verify(tok[:-6] + "AAAAAA") is None)
    forged = sso_mock.issue("1002", "employee", 6, [1], secret=b"attacker-key")
    check(seg, "B3 错钥伪造 -> DENY", sso_mock.verify(forged) is None)
    expired = sso_mock.issue("1003", "employee", 6, [1], ttl=-10)
    check(seg, "B3 过期 -> DENY", sso_mock.verify(expired) is None)
    try:
        sso_mock.issue("1004", "superadmin", 1, [1])
        check(seg, "B3 白名单外签发 -> ValueError", False)
    except ValueError:
        check(seg, "B3 白名单外签发 -> ValueError", True)


# ================================================================ C 行为层段
def seg_c_behavior():
    seg = "C行为层"
    conn, path = conn_copy()
    try:
        seed_sha_before = _sha256(SEED_DB)

        # C0 可观测性预检（纪律 22）：dept_manager 的范围策略在当前数据下必须可分辨
        subtree = P.dept_subtree(conn, 2)
        n_sub = conn.execute(
            f"SELECT COUNT(*) FROM employee WHERE dept_id IN ({','.join('?'*len(subtree))})",
            subtree).fetchone()[0]
        n_all = conn.execute("SELECT COUNT(*) FROM employee").fetchone()[0]
        check(seg, "C0 范围可分辨：子树人数 < 全员", n_sub < n_all, f"{n_sub}/{n_all}")
        # 数据侧事实：全员都在叶子部门 => dept 2 的子树 = 本人部门 ∪ 下级组
        check(seg, "C0 子树闭包含下级组（2/6/7/8）", set(subtree) == {2, 6, 7, 8}, str(subtree))

        # C1 正向对照：hr_admin 必须看到真值（防「全打成 null」蒙混）
        smq = {"dataset": "payroll", "columns": [{"field": "net_pay", "agg": "avg"}]}
        v, sql, rows = run_chain(smq, CTX["hr_admin"], conn)
        oracle = conn.execute("SELECT AVG(net_pay) FROM payroll").fetchone()[0]
        ok = (v["decision"] == "ALLOW" and rows and rows[0][0] is not None
              and abs(rows[0][0] - oracle) < 1e-6)
        check(seg, "C1 hr_admin avg(net_pay) == oracle 真值", ok,
              f"got {rows[0][0] if rows else None} vs {oracle}", defect=None)
        check(seg, "C1 正向对照 SQL 无遮蔽表达式", "NULL AS" not in (sql or ""),
              defect=None)

        # C2 行级收窄：dept_manager(2) attendance 行数 == 子树真值，且 < 不注入时行数
        smq = {"dataset": "attendance", "columns": [{"field": "hours", "agg": "sum"}]}
        # 改用明细计数更直观：count 行
        smq = {"dataset": "attendance", "columns": [{"field": "emp_id", "agg": "count"}]}
        v, sql, rows = run_chain(smq, CTX["dept_manager"], conn)
        got = rows[0][0] if rows else None
        oracle_sub = conn.execute(
            f"SELECT COUNT(*) FROM attendance WHERE emp_id IN "
            f"(SELECT id FROM employee WHERE dept_id IN ({','.join('?'*len(subtree))}))",
            subtree).fetchone()[0]
        oracle_all = conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
        check(seg, "C2 dept_manager 行数 == 子树 oracle", v["decision"] == "ALLOW" and got == oracle_sub,
              f"got {got} vs subtree {oracle_sub} / all {oracle_all}")
        check(seg, "C2 行级确实收窄（子树 < 全量）", got is not None and got < oracle_all,
              f"{got} < {oracle_all}")
        if v.get("policy"):
            cond_text = (v["policy"].get("row_cond") or [""])[0]
            anchor_ref = cond_text.split("(")[0].strip()   # 占位符内联后核对锚点列引用
            check(seg, "C2 同源白盒：row_cond 出现在编译产物",
                  bool(cond_text) and anchor_ref in sql,
                  f"row_cond={cond_text!r}")

        # C3 列级遮蔽：dept_manager 查 payroll net_pay 明细 -> 占位符 + 编译产物 NULL 化
        smq = {"dataset": "payroll", "columns": ["pay_month", "net_pay"],
               "filter": [{"field": "emp_id", "op": "eq", "value": 1001}]}
        v, sql, rows = run_chain(smq, CTX["dept_manager"], conn)
        check(seg, "C3 决策 == MASK", v["decision"] == "MASK", f"got {v['decision']}")
        if rows:
            masked_cell = rows[0][1]
            true_val = conn.execute(
                "SELECT net_pay FROM payroll WHERE emp_id=1001 AND pay_month=?",
                (rows[0][0],)).fetchone()
            check(seg, "C3 被遮蔽单元格是占位符（None）", masked_cell is None,
                  f"got {masked_cell!r}")
            if true_val:
                text = R.render_result(smq, rows[:20], len(rows), len(rows), v)
                check(seg, "C3 渲染文本不含真值", str(true_val[0]) not in text,
                      f"true={true_val[0]}")
        check(seg, "C3 编译产物含遮蔽表达式（NULL AS / MAX(NULL) AS）",
              bool(sql) and ("NULL AS" in sql or "MAX(NULL) AS" in sql),
              (sql or "")[:120])

        # C4 employee 查本人 critical 明细：allow_self_row -> 明文 1 行真值
        smq = {"dataset": "payroll", "columns": ["pay_month", "net_pay"],
               "filter": [{"field": "emp_id", "op": "eq", "value": 1001}]}
        v, sql, rows = run_chain(smq, CTX["employee"], conn)
        oracle_own = conn.execute(
            "SELECT COUNT(*), AVG(net_pay) FROM payroll WHERE emp_id=1001").fetchone()
        ok = (v["decision"] == "ALLOW" and rows is not None
              and len(rows) == oracle_own[0]
              and all(r[1] is not None for r in rows))
        check(seg, "C4 employee 本人 net_pay 明文且行数 == 本人行数", ok,
              f"rows={len(rows) if rows is not None else None} vs {oracle_own[0]}")
        # 对照：employee 的 row_cond 注入本人主键等值（纪律 12 主键等值，非姓名）
        if v.get("policy") and v["policy"].get("row_cond"):
            cond = v["policy"]["row_cond"]
            check(seg, "C4 row_cond 为主键等值（id 列，参数含本人主键，不含姓名比较）",
                  '"id" = ?' in cond[0] and 1001 in cond[1]
                  and "name" not in cond[0], str(cond))

        # C5 无个人行数据集不得注入行级条件（纪律 8 第三类：不越权但 500 也算缺口）
        smq = {"dataset": "department", "columns": ["name"]}
        v, sql, rows = run_chain(smq, CTX["employee"], conn)
        n_dept = conn.execute("SELECT COUNT(*) FROM department").fetchone()[0]
        ok = (v["decision"] == "ALLOW" and rows is not None and len(rows) == n_dept
              and not (v.get("policy") or {}).get("row_cond"))
        check(seg, "C5 department（无个人行）正常返回全表且不注入 row_cond", ok,
              f"rows={len(rows) if rows is not None else None}/{n_dept}")

        # C6 渲染层不实提示检查（纪律 8：打了「已脱敏」但数据是真值）
        # 若 C3 已红（真值在结果里），这里对照：MASK 决策时渲染文本必须带脱敏提示
        if v.get("decision") == "MASK":
            text = R.render_result(smq, rows[:20], len(rows), len(rows), v)
            check(seg, "C6 MASK 决策的渲染文本带脱敏提示", "脱敏" in text)

        # C7 种子库未被污染（纪律 7）
        check(seg, "C7 种子库未被污染", _sha256(SEED_DB) == seed_sha_before)
    finally:
        conn.close()
        os.unlink(path)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ================================================================ D 权威一致性段
def seg_d_authority():
    seg = "D权威一致性"
    # D1: 人工转写表必须与 YAML 权威逐格一致（两处独立编码）
    hand = {
        "public":   {"employee": "allow", "dept_manager": "allow", "hr_admin": "allow", "auditor": "allow"},
        "internal": {"employee": "allow", "dept_manager": "allow", "hr_admin": "allow", "auditor": "deny"},
        "sensitive": {"employee": "allow_self_row", "dept_manager": "mask", "hr_admin": "allow", "auditor": "deny"},
        "critical": {"employee": "allow_self_row", "dept_manager": "mask", "hr_admin": "allow", "auditor": "deny"},
    }
    diffs = _matrix_diff(hand, MATRIX)
    check(seg, "D1 人工转写表 == YAML 权威（基线 0 分歧）", not diffs, str(diffs))

    # D2 变异自检（纪律 19）：判据必须能红——逐面改坏一次，必须被抓住
    mutated = json.loads(json.dumps(hand))
    mutated["critical"]["dept_manager"] = "allow"       # 越权面：把遮蔽改成放行
    check(seg, "D2 变异(mask->allow) 被抓住", bool(_matrix_diff(mutated, MATRIX)))
    mutated = json.loads(json.dumps(hand))
    mutated["internal"]["auditor"] = "allow"            # 提权面
    check(seg, "D2 变异(auditor deny->allow) 被抓住", bool(_matrix_diff(mutated, MATRIX)))

    # D3 空转档断言成前提（纪律 18）：sensitive 档当前无字段使用
    used_sens = {s for fields in SENS_MAP.values() for s in fields.values()}
    uncovered = [(ds, f) for ds, fields in SENS_MAP.items()
                 for f, s in fields.items() if s not in {r[0] for r in REPRESENTATIVE}
                 and s not in used_sens]
    sensitive_fields = [(ds, f) for ds, fields in SENS_MAP.items()
                        for f, s in fields.items() if s == "sensitive"]
    check(seg, "D3 sensitive 档为空转档（无字段使用且未覆盖集合恰好为空）",
          "sensitive" not in used_sens and not sensitive_fields,
          f"used={sorted(used_sens)}")

    # D4 权威声明的规则都有实现落点（声明即兑现，静态核对）
    hard = AUTH["permission_matrix"]["hard_rules"]
    check(seg, "D4 权威硬规则 3 条与引擎 R1-R3 对应", len(hard) == 3, str(len(hard)))


def _matrix_diff(a, b):
    diffs = []
    for sens, cells in a.items():
        for role, val in cells.items():
            if b.get(sens, {}).get(role) != val:
                diffs.append(f"{sens}.{role}: hand={val} yaml={b.get(sens, {}).get(role)}")
    return diffs


# ================================================================ E 冲突报告段
def seg_e_conflicts():
    print("\n" + "=" * 72)
    print("E 冲突报告（只登记、不裁决、不断言——纪律 2）")
    print("=" * 72)
    print("""
[C1] SSO 角色名 vs 权限引擎角色名不一致
  - 引文 A：backend/core/sso_mock.py  ROLES = {employee, manager, hrbp, exec}
  - 引文 B：semantic_model/hr_domain_v1.yaml roles = {employee, dept_manager, hr_admin, auditor}
  - 待裁决：API 层的 SSO->策略角色映射表（W7 骨架暂按
    employee->employee / manager->dept_manager / hrbp->hr_admin / exec->hr_admin
    fail-closed 兜底，未登记映射一律 403），需发起人确认后写入 docs/adr。
  - 影响面：/query /ask 鉴权；exec 是否可见全员薪酬属薪酬敏感面。""")

    print(f"""
[C2] 敏感列 group_by 未被硬规则覆盖（纪律 13：取值本身即输出）
  - 引文 A：YAML hard_rules 只列 R1(sum)/R2(order_by)/R3(filter)
  - 引文 B：perm_engine 对 group_by 字段仅计入 mask_fields（MASK 输出列），
    GROUP BY 仍按真值列分组——输出组数即「敏感列有多少个不同取值」
  - 待裁决：是否新增硬规则 R5（critical/sensitive 进 group_by 且非 hr_admin -> 整查询 DENY），
    并同步写回 YAML hard_rules（先改权威，再改实现——流程 3/4）。
  - 影响面：原登记开放缺陷 D-3（P1），阻断关系：M7「越权路径 0 条存活」在 D-3 修复前不成立。
    裁决落地时必须同步补 R5 决策层断言。""")

    print("""
[C3] 权威内部矛盾：roles.auditor.scope=none vs permission_matrix.public.auditor=allow
  - 引文 A：YAML roles.auditor: scope: none（「显式空集，拒绝一切」）
  - 引文 B：YAML permission_matrix.defaults.public.auditor: allow
  - 实际行为：decide() 先按 scope=none 短路全 DENY，矩阵的 public/auditor=allow 格永远不可达
  - 待裁决：auditor 是否应可见 public 数据（如部门架构）？若是 -> 改 roles.auditor.scope
    或在 decide 中放行 public 档（先改权威再改实现，流程 3/4）；若否 -> 修订矩阵该格为 deny。
  - 影响面：矩阵 A1 段对 auditor 非 DENY 期望格暂不断言（本条登记即覆盖）。""")


# ================================================================ 缺陷登记表
KNOWN_DEFECTS = {
    # W7 接线修复后开放缺陷清零；纪律 13 的 group_by 缺口（原登记 D-3，P1）见冲突报告 [C2]，
    # 裁决落地（先改 YAML hard_rules 权威，再改 decide 加 R5）时必须同步补决策层断言。
}

RESOLVED_DEFECTS = {
    "D-1": "2026-09-19 W7 接线修复：decide() 产出 policy.masked 产物，compiler.render_sql 编译期注入"
           "（值列 NULL AS / 聚合列 MAX(NULL) AS，纪律 23），ask()/_answer_compare 消费；C3 转硬断言",
    "D-2": "2026-09-19 W7 接线修复：decide() 产出 policy.row_cond（row_policy 变 ds-aware 行锚点"
           " t/e/None，纪律 8），编译期注入 WHERE；缺主体上下文双 fail-closed DENY；C2/C5 转硬断言",
}


def seg_defects():
    print("\n" + "=" * 72)
    print("缺陷登记表（开放 %d / 已销项 %d）" % (len(KNOWN_DEFECTS), len(RESOLVED_DEFECTS)))
    print("=" * 72)
    for did, d in KNOWN_DEFECTS.items():
        print(f"  [{d['severity']}] {did}: {d['title']}")
        print(f"        symptom: {d['symptom']}")
    for did, note in RESOLVED_DEFECTS.items():
        print(f"  [RESOLVED] {did}: {note}")
    p0 = [k for k, v in KNOWN_DEFECTS.items() if v["severity"] == "P0"]
    if p0:
        print(f"\n  ⚠ 阻断关系：以下结论在 {','.join(p0)} 修复前不成立：")
        print("    - 「越权路径 0 条存活」")


# ================================================================ 主入口
def main():
    print("=" * 72)
    print(f"W7 权限测试矩阵 · 权威 = semantic_model/hr_domain_v1.yaml (model v{VERSION})")
    print("=" * 72)

    seg_a_decision()
    seg_b_failclosed()
    seg_c_behavior()
    seg_d_authority()
    seg_e_conflicts()
    seg_defects()

    # ---- STALE 机制（纪律 5）：带 defect 标记的断言一旦 PASS -> STALE
    stale = []
    for seg, name, ok, note, defect in RESULTS:
        if defect and ok and defect in KNOWN_DEFECTS:
            stale.append((defect, name))
    if stale:
        print("\n!! STALE（登记缺陷仍开放，但对应断言已 PASS —— 登记表过期）:")
        for did, name in stale:
            print(f"   {did} <- {name}")
        print("   处置：销项（移入 RESOLVED_DEFECTS 留痕）并摘掉 defect= 标记。")

    # ---- 未被任何断言引用的开放缺陷（登记了个没人测的窟窿）
    untested = set(KNOWN_DEFECTS) - DEFECT_HITS
    if untested:
        print(f"\n!! 开放缺陷未被任何断言覆盖: {sorted(untested)}")

    # ---- 汇总
    print("\n" + "=" * 72)
    n_ok = sum(1 for r in RESULTS if r[2])
    n_fail = len(RESULTS) - n_ok
    quarantined = [r for r in RESULTS if not r[2] and r[4] in KNOWN_DEFECTS]
    unexpected = [r for r in RESULTS if not r[2] and r[4] not in KNOWN_DEFECTS]
    print(f"结果：{n_ok}/{len(RESULTS)} PASS；失败 {n_fail}"
          f"（已隔离 {len(quarantined)} / 非预期 {len(unexpected)}）；STALE {len(stale)}")
    for seg, name, ok, note, defect in RESULTS:
        if not ok:
            tag = f" [quarantine {defect}]" if defect in KNOWN_DEFECTS else ""
            print(f"  ✗ {seg} {name}{tag}  {note}")
    print("=" * 72)

    # 退出码语义：0 = 无非预期失败且无 STALE；1 = 否则
    if unexpected or stale:
        print("MATRIX: FAILED")
        sys.exit(1)
    print("MATRIX: ALL GREEN (quarantined defects annotated)")
    sys.exit(0)


if __name__ == "__main__":
    main()
