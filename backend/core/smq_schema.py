# -*- coding: utf-8 -*-
"""
ChatBI-Lite · SMQ（Semantic Model Query）schema —— W4 起注册表由 DSL 生成（C1）
接口与 SMQ v0 完全兼容（compiler / llm_intent / e2e_live 回归基准不变），
新增 v1 校验（dsl-v1.html 第 9 节 C2/C4）：
  - C2: 聚合受 allowed_aggs 白名单约束（sum(salary)、avg(name) 显式拒绝）
  - C4: attribute 字段 group_by 合法但编译期锚定实体主键；measure 进 group_by 时：
        非敏感度量归一化剔除（LLM 误放的确定性纠偏）；critical/sensitive 度量原样保留、
        交由权限层 R5 裁决（W9 安全修订——group_by 是权限向量，归一化不得吞掉它）
  - 虚拟列（dept_name）仅在含 department join 的数据集可用（v0 隐患显式化）

SMQ 是 LLM 意图解析的输出契约：LLM 只产 SMQ JSON，永不产 SQL（tech.html 架构纪律）。
任何不合法输入 -> 显式 SmqError，绝无静默降级（明确失败率 100% 是编译层义务）。
"""
import json, re

from dsl_loader import REG  # C1: 注册表来源 = semantic_model/hr_domain_v1.yaml

TABLES = {ds: set(fields) for ds, fields in REG.tables.items()}
JOIN_PATHS = {ds: list(paths) for ds, paths in REG.join_paths.items()}
VIRTUAL = {"dept_name": REG.virtual_alias["dept_name"] + ".name"}
OPS = {"eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<=", "in": "IN"}
AGGS = set(("sum", "avg", "count", "min", "max"))
DATE_HINT = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")   # 2026 / 2026-08 / 2026-08-15

# ---- W6 (SMQ v2)：时间窗口预设 + 环比 ----
# time_window 把「本月/上个月/近三个月」的日期算术从 LLM 收回到确定性编译层：
# LLM 只写预设名，展开成 ge/lt 闭开区间（或 month 字段的 eq/IN）由编译器按运行时锚点完成。
TIME_PRESETS = ("this_month", "last_month", "last_3_months")
TIME_WINDOW_KEYS = {"field", "preset"}
# 预设 -> (起始月偏移, 结束月偏移[exclusive])，以当前月为 0。last_3_months 含本月（近三个自然月）。
PRESET_MONTH_SPAN = {"this_month": (0, 1), "last_month": (-1, 0), "last_3_months": (-2, 1)}


class SmqError(Exception):
    """显式失败：SMQ 不合法。编译层保证「明确失败率 100%」——这是义务，不是 LLM 的。"""


def _field_meta(ds, field):
    return REG.own[ds].get(field) or REG.inherited[ds].get(field)


def _field_ok(ds, field):
    if field in TABLES[ds]:
        # dept_name 之类虚拟列：要求该数据集真的有对应 join 别名（department 数据集无 d）
        if field in VIRTUAL and REG.virtual_alias.get(field) not in {a for _, a, _ in JOIN_PATHS[ds]}:
            raise SmqError(f"virtual field {field!r} not available on dataset {ds!r}")
        return
    raise SmqError(f"field {field!r} not in semantic model of {ds!r}")


def _agg_ok(ds, field, agg):
    """C2: 聚合白名单。agg=None（纯展示）不检查。"""
    if agg is None:
        return
    if agg not in AGGS:
        raise SmqError(f"unknown agg: {agg!r}")
    fm = _field_meta(ds, field)
    if not fm.get("aggregable"):
        raise SmqError(f"agg not allowed on field {field!r} (aggregable=false, kind={fm.get('kind')})")
    if agg not in (fm.get("allowed_aggs") or []):
        raise SmqError(f"agg {agg!r} not allowed on field {field!r} (allowed: {fm.get('allowed_aggs')})")


def colref(ds: str, field: str) -> str:
    """字段 -> 带引号的物理列引用（虚拟列解析为真实物理列）。"""
    return REG.colref(ds, field)


def group_exprs(ds: str, field: str) -> list:
    """C4: group_by 展开表达式（attribute 锚定实体主键）。供编译器使用。"""
    return REG.group_exprs(ds, field)


def validate(smq: dict) -> dict:
    """确定性校验。通过则原样返回；否则抛 SmqError。"""
    if not isinstance(smq, dict):
        raise SmqError("SMQ must be an object")
    ds = smq.get("dataset")
    if ds not in TABLES:
        raise SmqError(f"unknown dataset: {ds!r}")
    if not isinstance(smq.get("columns"), list) or not smq["columns"]:
        raise SmqError("columns must be a non-empty list")

    for c in smq["columns"]:
        if isinstance(c, str):
            _field_ok(ds, c)
        elif isinstance(c, dict) and "field" in c and set(c) <= {"field", "agg"}:
            # 契约允许 agg 为 null；LLM 常省略该键（语义等价），此处归一化而非拒绝。
            _field_ok(ds, c["field"])
            _agg_ok(ds, c["field"], c.get("agg"))
        else:
            raise SmqError(f"bad column spec: {c!r}")

    for f in smq.get("filter", []):
        if not isinstance(f, dict) or set(f) != {"field", "op", "value"}:
            raise SmqError(f"bad filter: {f!r}")
        _field_ok(ds, f["field"])
        if f["op"] not in OPS:
            raise SmqError(f"unknown op: {f['op']!r}")
        v = f["value"]
        if f["op"] == "in":
            if not isinstance(v, list) or not v:
                raise SmqError("IN needs non-empty list")
        elif isinstance(v, str) and f["field"] in REG.date_fields(ds) and not (
                DATE_HINT.match(v) or (REG.own[ds].get(f["field"], {}).get("type") == "month"
                                       and re.match(r"^\d{4}-\d{2}$", v))):
            raise SmqError(f"bad date literal: {v!r}")
        elif not isinstance(v, (str, int, float)):
            raise SmqError(f"bad value type: {v!r}")

    new_gb = []
    for g in smq.get("group_by", []):
        _field_ok(ds, g)
        fm = _field_meta(ds, g)
        if fm.get("kind") == "measure":
            # LLM 常把度量误放进 group_by（意图=明细或按维度聚合）——确定性归一化：剔除度量、保留维度。
            # 【W9 安全修订】归一化只适用于非敏感度量：R5 裁决（2026-09-19）后 group_by 是
            # 权限向量（取值本身即输出，纪律 13），critical/sensitive 度量必须原样保留、
            # 交由权限层裁决（非 hr_admin -> R5 DENY；hr_admin 经 D4 豁免后按度量分组合法，
            # 编译期 group_exprs 对度量返回裸列）。修复前：归一化先于 decide() 静默剥掉
            # salary -> R5 不可达（fail-open），矩阵 A6 直调 decide 未覆盖此管线缝隙。
            if fm.get("sensitivity") in ("critical", "sensitive"):
                new_gb.append(g)
            continue
        # attribute 允许 group_by：编译期经 group_exprs 锚定实体主键（C4）
        new_gb.append(g)
    if new_gb:
        smq["group_by"] = new_gb
    else:
        smq.pop("group_by", None)

    # ---- W6 v2: time_window（时间窗口预设） ----
    tw = smq.get("time_window")
    if tw is not None:
        if not isinstance(tw, dict) or set(tw) != TIME_WINDOW_KEYS:
            raise SmqError(f"time_window must be exactly {sorted(TIME_WINDOW_KEYS)}: {tw!r}")
        if tw["preset"] not in TIME_PRESETS:
            raise SmqError(f"unknown time preset: {tw['preset']!r} (allowed: {TIME_PRESETS})")
        _field_ok(ds, tw["field"])
        fm = _field_meta(ds, tw["field"])
        if fm.get("type") not in ("date", "month"):
            raise SmqError(f"time_window field {tw['field']!r} is not a date/month field")
        # 与同字段显式 filter 冲突 = 意图不清（AND 两个区间只会造成误导），显式拒绝
        clash = [f for f in smq.get("filter", []) if f["field"] == tw["field"]]
        if clash:
            raise SmqError(f"time_window on {tw['field']!r} conflicts with explicit filter on the same field")
    elif "time_window" in smq:
        raise SmqError("time_window must not be null (omit the key instead)")

    # ---- W6 v2: compare_to（环比；v2 纪律：仅支持 time_window 预设的时间平移） ----
    ct = smq.get("compare_to")
    if ct is not None:
        if not isinstance(ct, dict) or set(ct) != {"months_back"}:
            raise SmqError("compare_to must be exactly {'months_back': N}")
        n = ct["months_back"]
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 12:
            raise SmqError("compare_to.months_back must be an integer in [1, 12]")
        if tw is None:
            raise SmqError("compare_to requires time_window preset (explicit date filters cannot be shifted safely)")
        has_agg_col = any(isinstance(c, dict) and c.get("agg") for c in smq["columns"])
        if not smq.get("group_by") and not has_agg_col:
            raise SmqError("compare_to requires an aggregate query (group_by or agg column) — detail lists have no meaningful MoM")
    elif "compare_to" in smq:
        raise SmqError("compare_to must not be null (omit the key instead)")

    for o in smq.get("order_by", []):
        if not isinstance(o, dict) or "field" not in o:
            raise SmqError(f"bad order_by: {o!r}")
        _field_ok(ds, o["field"])
        _agg_ok(ds, o["field"], o.get("agg"))
        if o.get("dir", "asc") not in ("asc", "desc"):
            raise SmqError(f"bad order dir: {o.get('dir')!r}")
    lim = smq.get("limit")
    if lim is not None and (not isinstance(lim, int) or not 0 < lim <= 10000):
        raise SmqError(f"bad limit: {lim!r}")
    return smq


def to_json(smq: dict) -> str:
    return json.dumps(smq, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- 代表性 SMQ（回归基准，与 v0 逐字一致）
# Q1 明细 + 过滤：「王伟 2026 年 8 月的考勤记录」
Q1 = {
    "dataset": "attendance",
    "columns": ["work_date", "hours", "late_min", "early_leave", "name"],
    "filter": [{"field": "name", "op": "eq", "value": "王伟"},
               {"field": "work_date", "op": "ge", "value": "2026-08-01"},
               {"field": "work_date", "op": "lt", "value": "2026-09-01"}],
    "order_by": [{"field": "work_date", "dir": "asc"}],
    "limit": 100,
}
# Q2 聚合 + join + group：「各部门 8 月平均工时」（dept_name = 虚拟列，join 展开）
Q2 = {
    "dataset": "attendance",
    "columns": ["dept_name", {"field": "hours", "agg": "avg"}],
    "filter": [{"field": "work_date", "op": "ge", "value": "2026-08-01"},
               {"field": "work_date", "op": "lt", "value": "2026-09-01"}],
    "group_by": ["dept_name"],
    "order_by": [{"field": "hours", "agg": "avg", "dir": "desc"}],
}
# Q3 TopN 聚合：「8 月迟到总分钟数最多的 10 名员工」（group_by name -> 编译期锚定 e.id）
Q3 = {
    "dataset": "attendance",
    "columns": ["name", {"field": "late_min", "agg": "sum"}],
    "filter": [{"field": "work_date", "op": "ge", "value": "2026-08-01"},
               {"field": "work_date", "op": "lt", "value": "2026-09-01"}],
    "group_by": ["name"],
    "order_by": [{"field": "late_min", "agg": "sum", "dir": "desc"}],
    "limit": 10,
}
# Q4（v1 新增）：payroll 数据集冒烟——「2026 年 8 月各部门平均实发工资」
Q4_PAYROLL = {
    "dataset": "payroll",
    "columns": ["dept_name", {"field": "net_pay", "agg": "avg"}],
    "filter": [{"field": "pay_month", "op": "eq", "value": "2026-08"}],
    "group_by": ["dept_name"],
    "order_by": [{"field": "net_pay", "agg": "avg", "dir": "desc"}],
}

SAMPLES = [("Q1-detail", Q1), ("Q2-agg-join", Q2), ("Q3-topN", Q3), ("Q4-payroll", Q4_PAYROLL)]
