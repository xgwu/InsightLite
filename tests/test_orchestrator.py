# -*- coding: utf-8 -*-
"""D3 落地验证 · PydanticAI 编排层离线测试（CI 无 Key 可回归，pydantic-ai TestModel）。

覆盖：
  O1 三个输出分支的 agent -> (kind, payload) 契约（smq / refused_scope / clarify）
  O2 SmqOutput -> validate 通过（契约同构性：合法 SMQ 经模型往返后仍过 S.validate）
  O3 恶意输出 fail-closed（编造字段名 -> model 正常返回、下游 S.validate 拒绝）
  O4 提示词由 DSL 自动拼装（C6：SYSTEM_PROMPT 含数据集词表，杜绝硬编码复辟）
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend", "core"))

import orchestrator as O                 # noqa: E402
import smq_schema as S                   # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_PASS, _FAIL = [0], [0]


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    _PASS[0] += ok
    _FAIL[0] += (not ok)
    print(f"  [{tag}] {name}" + (f"  -- {detail}" if detail and not ok else ""))


def offline(output_type, args):
    """单成员 output_type + 定制 args 的离线 agent（TestModel 机制：args 作用于 output_tools[0]）。"""
    return O.build_agent(output_type=output_type, model=TestModel(custom_output_args=args))


def seg_o_contract():
    print("=" * 72)
    print("O 契约分派（D3 PydanticAI，TestModel 离线）")
    print("=" * 72)
    # O1a smq 分支
    smq_args = {"dataset": "payroll", "columns": [{"field": "net_pay", "agg": "avg"}],
                "group_by": ["dept_name"]}
    kind, payload = O.interpret_with(offline(O.SmqOutput, smq_args), "各部门平均工资")
    check("O1a smq 分支 kind==smq", kind == "smq", f"{kind}: {payload}")
    check("O1a payload 为 dict 且字段齐全", isinstance(payload, dict) and payload["dataset"] == "payroll")

    # O1b refused_scope 分支
    kind, payload = O.interpret_with(offline(O.RefuseScope, {"error": "OUT_OF_SCOPE", "reason": "股票行情"}),
                                     "明天买哪只股票")
    check("O1b refused_scope 分支", kind == "refused_scope" and "股票" in payload)

    # O1c clarify 分支
    kind, payload = O.interpret_with(offline(O.ClarifyOutput, {"clarify": True, "question": "哪个月?", "options": ["8月", "9月"]}),
                                     "上个月考勤")
    check("O1c clarify 分支", kind == "clarify" and payload["question"] == "哪个月?" and payload["options"] == ["8月", "9月"])

    # O2 契约同构：模型往返产物过确定性校验
    kind2, payload2 = O.interpret_with(offline(O.SmqOutput, smq_args), "q")
    ok, detail = True, ""
    try:
        S.validate(payload2)
    except Exception as e:
        ok, detail = False, f"roundtrip validate: {e}"
    check("O2 SmqOutput 往返后仍过 S.validate", ok, detail)

    # O3 恶意输出 fail-closed：编造字段名——框架层放行、schema 层拒绝
    kind3, payload3 = O.interpret_with(offline(O.SmqOutput, {"dataset": "employee", "columns": ["salary_evil"]}), "q")
    rejected = False
    try:
        S.validate(payload3)
    except Exception:
        rejected = True
    check("O3 编造字段 -> 框架放行但 S.validate 拒绝（不可信输入纪律）",
          kind3 == "smq" and rejected)


def seg_o_prompt():
    print("=" * 72)
    print("O4 提示词权威（C6 回归）")
    print("=" * 72)
    sp = O.SYSTEM_PROMPT
    check("O4 SYSTEM_PROMPT 来自 DSL（含数据集词表）",
          all(ds in sp for ds in ("employee", "attendance", "payroll")) and "SMQ" in sp,
          f"len={len(sp)}")
    check("O4 无硬编码复辟（与 dsl_loader 逐字一致）", sp == __import__("dsl_loader").REG.build_system_prompt())


if __name__ == "__main__":
    seg_o_contract()
    seg_o_prompt()
    print("=" * 72)
    total = _PASS[0] + _FAIL[0]
    print(f"ORCHESTRATOR: {_PASS[0]}/{total}" + (" ALL GREEN" if _FAIL[0] == 0 else f"  ({_FAIL[0]} FAILED)"))
    sys.exit(1 if _FAIL[0] else 0)
