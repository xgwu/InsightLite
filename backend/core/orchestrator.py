# -*- coding: utf-8 -*-
"""D3（2026-09-19 裁决）· PydanticAI 编排层。

裁决背景：技术基线 D2 原定「自研轻量编排，PydanticAI 留作 W13 观察项」；
发起人裁决「现在就引入 PydanticAI」——编排层基于 pydantic_ai.Agent 构建，
SMQ 契约改为 Pydantic 模型结构化输出（D2 反对重型框架 LangChain/LangGraph 的
理由不变：单触点、无多 Agent 图，PydanticAI 只提供「契约化输出 + 运行时校验」）。

职责边界（与技术基线一致，编排层只做意图这一件事）：
  NL -> 三选一结构化输出（SMQ / 超范围拒绝 / 澄清反问）-> (kind, payload) 契约。
  产物永远视为不可信输入：下游 compile_smq 仍强制 S.validate()（fail-closed 不因框架而放松）。
  本层不做权限、不碰 SQL、不碰数据库。

离线测试路径（CI 无 Key 可回归）：pydantic_ai TestModel + 定向 custom_output_args。
注意 TestModel 的两个机制约束（源码实证，2.45.0）：
  - custom_output_args 固定作用于 output_tools[0]——所以离线 agent 用「单成员 output_type」；
  - call_tools 只作用于 function_tools，对本仓无 function tool 的用法无影响。
线上 agent 用三成员 Union（SmqOutput / RefuseScope / ClarifyOutput），模型自行选择分支。
"""
import os
import sys
from typing import Literal, Optional, Union

from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dsl_loader                                    # noqa: E402  C6: 提示词由 DSL 自动拼装

SYSTEM_PROMPT = dsl_loader.REG.build_system_prompt()

# W13 澄清规则附录：缺省建议必须给（一键默认项的数据契约），且缺省必须带 ★ 标记
CLARIFY_RULE = (
    "\n\n【澄清规则（W13）】当需要澄清反问时：options 给出 2-4 个候选，"
    "并从中指定一个最可能的缺省建议放入 default 字段（语言与选项一致，"
    "例如「上个月」）；不要在 question 里重复 options。"
)


def build_instructions(prefs_hint: str = "") -> str:
    """系统提示词 = DSL 自动拼装（C6）+ 澄清缺省规则 + 可选的个人时间偏好注入。
    prefs_hint 来自 prefs.hint()（白名单枚举内插值，无自由文本 -> 无注入面）。"""
    return SYSTEM_PROMPT + CLARIFY_RULE + (("\n\n" + prefs_hint) if prefs_hint else "")
MODEL_NAME = os.environ.get("LLM_MODEL", "deepseek-chat")
BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")


# ---------------------------------------------------------------- 输出契约（Pydantic 模型）
class SmqOutput(BaseModel):
    """与 smq_schema.validate 契约同构；字段合法性由下游 S.validate 把守（LLM 不可信）。"""
    dataset: str
    columns: list                                    # str | {"field": str, "agg": str|None}
    filter: Optional[list] = None                    # [{"field","op","value"}]
    group_by: Optional[list] = None
    time_window: Optional[dict] = None               # {"preset","field"}
    compare_to: Optional[dict] = None                # {"months_back": N}
    order_by: Optional[list] = None                  # [{"field","agg"?,"dir"?}]
    limit: Optional[int] = None


class RefuseScope(BaseModel):
    """超范围拒绝（对应 llm_intent 契约 error=OUT_OF_SCOPE）。"""
    error: Literal["OUT_OF_SCOPE"]
    reason: str = ""


class ClarifyOutput(BaseModel):
    """澄清反问——绝不携带查询字段（fail-closed：澄清分支永远不执行查询）。
    W13 一键默认项：default 是给缺省建议（用户可一键采纳），不是查询参数。"""
    clarify: Literal[True]
    question: str
    options: list[str] = []
    default: Optional[str] = None                   # ★缺省建议（一键采纳）


OutputSpec = Union[SmqOutput, RefuseScope, ClarifyOutput]


# ---------------------------------------------------------------- Agent 构建
def build_model(api_key: str, model_name: str = None, base_url: str = None):
    """OpenAI 兼容模型（DeepSeek 缺省）。R6 防锁定：换供应商只改 env（LLM_MODEL/LLM_BASE_URL）。"""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
    return OpenAIChatModel(model_name or MODEL_NAME,
                           provider=OpenAIProvider(base_url=base_url or BASE_URL,
                                                   api_key=api_key))


def build_agent(api_key: str = None, output_type=OutputSpec, model=None,
                model_name: str = None, base_url: str = None,
                instructions: str = None):
    """Agent 工厂。model 可注入（TestModel 离线测试）；线上传 api_key 走 OpenAI 兼容端点。
    instructions 可注入（W13 个人偏好；缺省 = build_instructions()）。"""
    from pydantic_ai import Agent
    return Agent(model or build_model(api_key, model_name, base_url),
                 output_type=output_type,
                 instructions=instructions or build_instructions())


# ---------------------------------------------------------------- 契约分派（与 render._interpret 对齐）
def _to_contract(obj) -> tuple:
    """Agent 结构化输出 -> (kind, payload)。kind: smq / refused_scope / clarify / invalid。"""
    if isinstance(obj, RefuseScope):
        return ("refused_scope", obj.reason)
    if isinstance(obj, ClarifyOutput):
        return ("clarify", {"question": str(obj.question).strip(),
                            "options": [str(o) for o in obj.options],
                            "default": (str(obj.default).strip() if obj.default else None)})
    if isinstance(obj, SmqOutput):
        return ("smq", obj.model_dump(exclude_none=True))
    return ("invalid", f"unexpected output type {type(obj).__name__}")


def interpret_with(agent, question: str) -> tuple:
    """用给定 agent 跑意图解析（离线测试与线上共用同一条出口路径）。"""
    try:
        result = agent.run_sync(question)
    except Exception as e:                               # 框架层异常 -> invalid（不静默吞）
        return ("invalid", f"pydantic_ai run failed: {type(e).__name__}: {str(e)[:120]}")
    return _to_contract(result.output)


def interpret(question: str, api_key: str, prefs_hint: str = "") -> tuple:
    """线上入口：LLM 意图 -> (kind, payload)。prefs_hint 注入个人时间偏好（W13）。"""
    agent = build_agent(api_key, instructions=build_instructions(prefs_hint))
    return interpret_with(agent, question)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"orchestrator ok: model={MODEL_NAME} base={BASE_URL} "
          f"prompt={len(SYSTEM_PROMPT)} chars")
    print("offline checks live in tests/test_orchestrator.py (TestModel).")
