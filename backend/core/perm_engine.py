# -*- coding: utf-8 -*-
"""
ChatBI-Lite W4 · 权限策略引擎 v1（dev-plan.html C5）
三态决策：ALLOW（明文）/ MASK（列 null 化）/ DENY（整查询拒绝）。缺省 DENY——矩阵外的格子一律拒绝。

输入：合法 SMQ（调用方必须先过 smq_schema.validate）+ securityContext（role 必填）。
策略来源：semantic_model/hr_domain_v1.yaml 的 roles / permission_matrix / 字段 sensitivity（dsl-v1.html 第 5 节）。

v1 硬规则（dsl-v1.html 6.5 + 2026-09-19 裁决，W7 权限矩阵验收直接引用）：
  R1 critical 字段不开放 sum —— 已由 smq_schema allowed_aggs 收口（这里不重复）
  R2 critical 字段出现在 order_by 且角色非 hr_admin -> DENY
  R3 critical 字段出现在 filter   且角色非 hr_admin -> DENY
  R5 critical/sensitive 字段出现在 group_by 且角色非 hr_admin -> DENY（裁决新增：
     group_by 取值本身即输出，独立于「决策==遮蔽」判定）
scope=none 角色（auditor）：可访问纯 public 引用查询（裁决：matrix.public.auditor=allow 生效），
  任何非 public 字段引用整查询 DENY；个人行数据集 row_cond 显式空集。
D4 裁决（2026-09-19）：hr_admin 默认豁免——通过角色/数据集 fail-closed 校验后短路 ALLOW，
  verdict.exempt 标记 + 调用方逐次审计（audit.record_if_exempt）；豁免是唯一管理员语义，
  未来新增约束默认对 hr_admin 无效，除非显式写在豁免之前。
列级 MASK：sensitive/critical 字段对 dept_manager 角色以 NULL 输出（Cube Spike T2 语义，升级为生成器保证）。
employee 角色：行级策略已限定本人行，本人数据明文（OQ2 决策）；不额外 MASK。
"""
import sqlite3

import dsl_loader

_SENS = dsl_loader.SENSITIVITY_LEVELS
_HIDDEN = ("sensitive", "critical")     # 需 MASK 的敏感级
_ADMIN = "hr_admin"


class PermError(Exception):
    """权限引擎内部输入不合法（如 SMQ 未过校验）。显式失败。"""


def _sens_of(ds, field):
    fm = dsl_loader.REG.own[ds].get(field) or dsl_loader.REG.inherited[ds].get(field) or {}
    return fm.get("sensitivity", "internal")


def _fields_in(smq, section):
    out = []
    for item in smq.get(section) or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            out.append(item.get("field"))
    return out


def dept_subtree(conn: sqlite3.Connection, dept_id: int):
    """department.parent_id 闭包（启动物化思想的运行时等价物，仅测试/演示用）。"""
    ids, frontier = [], [dept_id]
    while frontier:
        placeholders = ",".join("?" * len(frontier))
        rows = conn.execute(
            f"SELECT id FROM department WHERE parent_id IN ({placeholders})", frontier
        ).fetchall()
        frontier = [r[0] for r in rows if r[0] not in ids]
        ids.extend(frontier)
    return sorted(set(ids) | {dept_id})


_ROW_ANCHOR = {          # 行锚点别名（纪律 8：单一权威，行级策略与哨兵共用）
    "employee": "t",     # 主表自身行
    "attendance": "e",   # 事实表经 join 的 employee 别名
    "leave_balance": "e",
    "payroll": "e",
    "department": None,  # 无个人行的数据集：不适用行级条件（注入即「不越权但 500」缺口）
}


def row_policy(conn: sqlite3.Connection, ds: str, ctx: dict):
    """行级策略 -> SQL 条件文本（含参数值）或 None（无约束）。
    行锚点别名按数据集取自 _ROW_ANCHOR（employee 主表是 t，事实表是 e，department 不注入）。
    ctx.role 非法时抛 PermError（fail-closed 由 decide 统一处理为 DENY）。"""
    role = ctx.get("role")
    if role not in dsl_loader.REG.roles:
        raise PermError(f"unknown role: {role!r}")
    anchor = _ROW_ANCHOR.get(ds)
    if anchor is None:
        return None                                # 无个人行数据集：不注入（跳过）
    if role == "employee":
        emp = ctx.get("emp_id")
        if emp is None:
            raise PermError("employee role requires ctx.emp_id")
        return (f'{anchor}."id" = ?', [emp])
    if role == "dept_manager":
        dept = ctx.get("dept_id")
        if dept is None:
            raise PermError("dept_manager role requires ctx.dept_id")
        ids = dept_subtree(conn, dept)
        return (f'{anchor}."dept_id" IN ({",".join("?" * len(ids))})', ids)
    if role == "hr_admin":
        return None
    return ("1 = 0", [])          # auditor / scope none：显式空集


def mask_fields(ds: str, role: str, fields):
    """需要 MASK（NULL 化）的输出字段列表。"""
    if role == _ADMIN:
        return []
    return [f for f in fields if _sens_of(ds, f) in _HIDDEN and role != "employee"]


def decide(smq: dict, ctx: dict, conn: sqlite3.Connection = None) -> dict:
    """三态决策主入口。smq 必须已通过 smq_schema.validate（内部不重复校验，缺 dataset 即 DENY）。
    conn 可选：R4 需要把 filter 里的姓名与本人姓名比对；不传则按需自开连接。
    2026-09-19 裁决落地：① scope=none 角色（auditor）可访问纯 public 引用查询；
    ② 新增 R5 硬规则（critical/sensitive 进 group_by 且非 hr_admin -> 整查询 DENY）；
    ③ D4 裁决：hr_admin 默认豁免——通过角色/数据集 fail-closed 校验后短路 ALLOW，
    verdict 带 exempt 标记，调用方必须逐次写审计（audit.record_if_exempt）。
    豁免不越过：未知角色/未知数据集仍 DENY；字段级合法性由编译层 S.validate 把守。"""
    version = dsl_loader.REG.version
    role = ctx.get("role")
    if role not in dsl_loader.REG.roles:                       # 未登录 / 伪造角色：主验证面
        return {"decision": "DENY", "reason": f"unknown role: {role!r}", "model_version": version}
    ds = smq.get("dataset")
    if ds not in dsl_loader.REG.tables:
        return {"decision": "DENY", "reason": f"unknown dataset: {ds!r}", "model_version": version}

    # D4（2026-09-19 裁决）：管理员默认豁免 + 逐次审计标记。
    # 语义：hr_admin 不再逐条走矩阵/硬规则（R2/R3/R5 本就豁免，D4 把豁免显式化为唯一语义，
    # 未来新增约束默认对 hr_admin 无效，除非显式写在豁免之前）；每次豁免查询由调用方强制审计。
    if role == _ADMIN:
        return {"decision": "ALLOW",
                "reason": "D4: hr_admin default exemption (audited)",
                "exempt": True,
                "policy": {"row_cond": None, "masked": []},
                "model_version": version}

    order_fields = _fields_in(smq, "order_by")
    filter_fields = [f.get("field") for f in smq.get("filter") or []]
    group_fields = [g for g in smq.get("group_by") or []]
    out_fields = _fields_in(smq, "columns") + group_fields

    # scope=none（auditor）：public 档可看（矩阵 public.auditor=allow，2026-09-19 裁决生效）；
    # 任何非 public 字段引用（含 filter/order_by/group_by 间接引用）整查询 DENY（fail-closed）。
    # 未知字段 _sens_of 缺省 internal -> DENY，同样 fail-closed。
    if dsl_loader.REG.roles[role].get("scope") == "none":
        referenced = set(order_fields) | set(filter_fields) | set(out_fields)
        non_public = [f for f in sorted(referenced) if _sens_of(ds, f) != "public"]
        if non_public:
            return {"decision": "DENY",
                    "reason": f"scope=none role {role!r}: non-public fields {non_public}",
                    "model_version": version}

    # R2/R3：critical 的排序/过滤对非 hr_admin 整查询拒绝
    if role != _ADMIN:
        for f in order_fields:
            if _sens_of(ds, f) == "critical":
                return {"decision": "DENY", "reason": f"R2: critical field {f!r} in order_by",
                        "model_version": version}
        for f in filter_fields:
            if _sens_of(ds, f) == "critical":
                return {"decision": "DENY", "reason": f"R3: critical field {f!r} in filter",
                        "model_version": version}
        # R5（2026-09-19 裁决）：critical/sensitive 进 group_by 对非 hr_admin 整查询拒绝。
        # 独立于「本轮是否有列被遮蔽」判定——group_by 取值本身即输出（纪律 13），
        # 若挂在 MASK 决策之下，「聚合放行」类分级会让该格静默变为可利用面。
        for f in group_fields:
            if _sens_of(ds, f) in ("critical", "sensitive"):
                return {"decision": "DENY",
                        "reason": f"R5: sensitive field {f!r} in group_by",
                        "model_version": version}

    # R4（W4 落地，OQ2 配套）：employee 角色指名道姓查「他人」-> DENY。
    # 行级策略虽会静默过滤成空集，但「查李娜的工资得到空」是信息泄漏面（存在性推断），
    # 且掩盖 LLM 误解析——显式拒绝才是矩阵承诺的语义（dev-plan 第 6 节「他人薪资 = DENY」）。
    if role == "employee":
        name_filters = [f for f in smq.get("filter") or []
                        if f.get("field") == "name" and f.get("op") == "eq"]
        if name_filters:
            own_conn = conn or _fallback_conn()
            try:
                row = own_conn.execute("SELECT name FROM employee WHERE id = ?",
                                       (ctx.get("emp_id"),)).fetchone()
            finally:
                if conn is None:
                    own_conn.close()
            own_name = row[0] if row else None
            for f in name_filters:
                if own_name is None or f["value"] != own_name:
                    return {"decision": "DENY",
                            "reason": "R4: 指名道姓查询他人数据（本人请用「我的」表述）",
                            "model_version": version}

    masked = mask_fields(ds, role, out_fields)

    # ---- 策略产物（纪律 8 架构约束）：决策产出可执行产物，编译层只能消费、不得自行判定
    row_cond = None
    if ds in _ROW_ANCHOR and _ROW_ANCHOR[ds] is not None:
        try:
            row_cond = row_policy(conn if conn is not None else _fallback_conn(), ds, ctx)
        except PermError as e:                     # 双 fail-closed：缺主体上下文 -> 显式 DENY
            return {"decision": "DENY", "reason": f"row policy: {e}", "model_version": version}

    policy = {"row_cond": row_cond, "masked": masked}
    if masked:
        return {"decision": "MASK", "reason": f"masked columns: {masked}",
                "policy": policy, "model_version": version}
    return {"decision": "ALLOW", "reason": "no restricted access",
            "policy": policy, "model_version": version}


def _fallback_conn():
    """调用方未传 conn 时的降级连接（R4 姓名比对 / dept_subtree 闭包用）。"""
    return sqlite3.connect(
        __import__("os").path.join(
            __import__("os").path.dirname(__import__("os").path.dirname(
                __import__("os").path.dirname(
                    __import__("os").path.abspath(__file__)))),
            "data", "hr_demo.db"))


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"perm_engine smoke: model={dsl_loader.REG.version}")
    print("run test_dsl_v1.py for the full matrix.")
