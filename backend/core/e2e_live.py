# -*- coding: utf-8 -*-
"""
Sprint 0 · M0 端到端验证（W4 升级版）：NL -> LLM意图 -> 权限 -> SMQ校验 -> 编译SQL -> 执行 -> 中文渲染
这是 M0「最小链路跑通」的一锤定音脚本：六个环节在同一进程内真实串联，
不 mock 任何一环（LLM 走真实 DeepSeek API，数据查真实种子库）。
渲染与对话层统一走 render.ask()——与未来 API 服务同一条代码路径。

用法（需 .env 或环境变量 DEEPSEEK_API_KEY）:
    python e2e_live.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render as R                     # W4: 渲染 + 对话层（ask() 五环节封装）
import llm_intent as L           # 触发 _load_dotenv()，含提示词契约与 API 调用

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QUESTIONS = L.TEST_QUESTIONS          # 10 问（8 可回答 + 2 越界题）
DEMO_CTX = {"role": "hr_admin", "emp_id": 1001, "dept_id": 2}   # 演示身份：hr_admin 全量


def main():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not set (check InsightLite/.env).")
        return 1
    conn = sqlite3.connect(R.DB)
    ok = refused = failed = 0
    for q in QUESTIONS:
        print("\n" + "=" * 72)
        print(f"Q: {q}")
        res = R.ask(q, api_key, DEMO_CTX, conn=conn)
        print(res["text"][:1200])
        if res["status"] == "answered":
            ok += 1
        elif res["status"] in ("refused_scope", "denied"):
            refused += 1
        else:
            failed += 1
    conn.close()
    print("\n" + "=" * 72)
    print(f"M0 E2E RESULT: {ok}/{len(QUESTIONS)} answered end-to-end; "
          f"{refused} correctly refused; {failed} failed.")
    print("=" * 72)
    return 0 if failed == 0 and (ok + refused) == len(QUESTIONS) else 2


if __name__ == "__main__":
    sys.exit(main())
