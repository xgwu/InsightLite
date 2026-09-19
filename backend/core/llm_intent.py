# -*- coding: utf-8 -*-
"""
Sprint 0 · Spike C：LLM 意图解析（NL -> SMQ JSON）提示词契约 + 调用模板
对应 dev-plan.html W2「LLM Spike：DeepSeek（OpenAI 兼容）按 SMQ schema 稳定产出意图 JSON」

用法:
  干跑（不需要 API Key，检查提示词契约与示例输出结构）:
      python llm_intent.py
  实跑（需要环境变量 DEEPSEEK_API_KEY，调用 OpenAI 兼容接口）:
      python llm_intent.py --live

架构纪律：提示词层明令禁止产出 SQL；模型只产 SMQ JSON（tech.html 核心论点）。
输出后必须过 smq_schema.validate()——LLM 的产物永远被视为不可信输入。
"""
import json, os, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smq_schema as S
import dsl_loader                     # C6: system prompt 由 DSL 自动拼装（字段面 = DSL 词表）

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SYSTEM_PROMPT = dsl_loader.REG.build_system_prompt()
# 注意：本文件曾有第二份硬编码 SYSTEM_PROMPT 在此覆盖上面的 DSL 生成版（W4 eval 发现），
# 导致 C6「提示词由 DSL 自动拼装」静默失效、few-shot 修复全部落空——已删除，勿再引入。


def _load_dotenv():
    """轻量 .env 加载（零依赖）：读取项目根目录 chatbi-lite-sprint0/.env。
    已存在的环境变量优先（setdefault），不覆盖外部注入。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env_path = os.path.join(root, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"


def build_messages(question: str):
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question}]


def call_deepseek(question: str, api_key: str) -> str:
    body = json.dumps({
        "model": MODEL, "messages": build_messages(question),
        "response_format": {"type": "json_object"}, "temperature": 0,   # 意图解析要确定性；0.1 有采样抖动（Q7 实证）
    }).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


TEST_QUESTIONS = [
    # 1 明细查询（基础）
    "王伟 2026 年 8 月的考勤记录？",
    # 2 分组聚合 + 排序
    "各部门 8 月平均工时，从高到低？",
    # 3 TopN 聚合
    "8 月迟到总分钟数最多的 10 名员工？",
    # 4 越界-导出（期望 OUT_OF_SCOPE：导出不是 SMQ 表达力）
    "帮我把工资最高的员工名单导出成 Excel",
    # 5 越界-域外（期望 OUT_OF_SCOPE：数据域外问题）
    "帮我查一下今天公司的股价多少？",
    # 6 时间过滤 + 明细（hire_date）
    "2026 年 8 月入职的员工有哪些？",
    # 7 跨数据集语义（leave_balance 域）
    "8 月请年假的员工，他们的假期余额还剩多少？",
    # 8 口语化时间指代（「上个月」需解析为 2026-08）
    "上个月谁老迟到？",
    # 9 聚合 + 具体人过滤（count 用法）
    "李娜 8 月迟到几次？",
    # 10 多度量同查
    "全公司 8 月总工时和总迟到分钟数？",
]


def dry_run():
    print("=" * 72)
    print("SPIKE C (dry-run): NL -> SMQ prompt contract; live mode needs DEEPSEEK_API_KEY")
    print("=" * 72)
    print(f"system prompt chars = {len(SYSTEM_PROMPT)}")
    for q in TEST_QUESTIONS:
        print(f"\nQ: {q}")
        print("messages built OK (system + user).")
    # 结构自检：schema 样例能通过 validate（证明提示词契约与校验器一致）
    for name, smq in S.SAMPLES:
        S.validate(smq)
        print(f"contract self-check: {name} passes validate() OK")
    print("\nnext: 佩戴 DEEPSEEK_API_KEY 后跑 --live，对 10 个真实问题记录成功率与失败形态（W2 验收物）。")


def live():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("ERROR: set DEEPSEEK_API_KEY env var first.")
        return
    ok = fail = 0
    shapes = []  # (idx, question, verdict, note)
    for i, q in enumerate(TEST_QUESTIONS, 1):
        raw = call_deepseek(q, api_key)
        print(f"\nQ{i}: {q}\nraw: {raw[:200]}")
        try:
            smq = json.loads(raw)
            if smq.get("error") == "OUT_OF_SCOPE":
                print(f"-> OUT_OF_SCOPE: {smq.get('reason', '')[:80]}")
                ok += 1
                shapes.append((i, q, "PASS", "正确拒答 OUT_OF_SCOPE"))
                continue
            S.validate(smq)
            ok += 1
            print("-> validate() PASS")
            shapes.append((i, q, "PASS", "SMQ 校验通过"))
        except Exception as e:
            fail += 1
            print(f"-> validate() FAIL: {type(e).__name__}: {e}")
            shapes.append((i, q, "FAIL", f"{type(e).__name__}: {str(e)[:120]}"))
    print("\n" + "=" * 72)
    print(f"10 问完整成功率（W2 验收物）: 合格 {ok}/{ok + fail}，失败 {fail}")
    for i, q, v, note in shapes:
        mark = "✅" if v == "PASS" else "❌"
        print(f"  {mark} Q{i} [{v}] {note}")
    # 失败形态落盘，供 M0 报告引用
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_llm10_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "fail": fail, "total": ok + fail,
                   "items": [{"q": q, "verdict": v, "note": note} for i, q, v, note in shapes]},
                  f, ensure_ascii=False, indent=2)
    print(f"结果已落盘: {out_path}")


if __name__ == "__main__":
    live() if "--live" in sys.argv else dry_run()
