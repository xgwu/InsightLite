# -*- coding: utf-8 -*-
"""
ChatBI-Lite W6 · M2 端到端 Demo 转录脚本
对一组覆盖「六状态」的问题跑真实全链路（ask()：LLM→权限→校验→编译→执行→渲染），
把转录落盘 scripts/demo_transcript.json，供 make_demo_page.py 生成 chatbi-lite-site/demo.html。
这是给非技术人演示的物料源：每个状态都有真实例子，不摆拍、不 mock。

用法: python demo_transcript.py   （需要 .env 里的 DEEPSEEK_API_KEY）
"""
import json, os, sqlite3, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "core"))
import llm_intent   # noqa: F401  导入即加载 .env（DEEPSEEK_API_KEY）
import render as R

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "demo_transcript.json")

HR = {"role": "hr_admin"}
EMP = {"role": "employee", "emp_id": 1001}   # 王伟本人（R4 演示用）

# (问题, ctx, 展示意图)
SCRIPT = [
    ("王伟 2026 年 8 月的考勤记录？", HR, "明细查询"),
    ("各部门 8 月平均工时，从高到低？", HR, "聚合 + 部门 join"),
    ("8 月迟到总分钟数最多的 10 名员工？", HR, "TopN"),
    ("上个月谁老迟到？", HR, "相对时间解析"),
    ("李娜 8 月迟到几次？", HR, "口径：几次 = 迟到天数"),
    ("各部门 8 月平均实发工资，环比 7 月变化多少？", HR, "环比（W6 新能力）"),
    ("公司的出勤情况好吗？", HR, "歧义澄清"),
    ("对比一下 8 月和 9 月的迟到情况？", HR, "对比类（环比或澄清）"),
    ("李娜 8 月的考勤记录？", EMP, "越权拦截（员工查他人）"),
    ("帮我把工资最高的员工名单导出成 Excel", HR, "越界拒答"),
]


def main():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not set (chatbi-lite-sprint0/.env)")
        return 1
    items = []
    for q, ctx, intent in SCRIPT:
        res = R.ask(q, api_key, ctx)
        item = {"q": q, "intent": intent, "status": res["status"], "text": res["text"],
                "role": "hr_admin" if ctx is HR else "employee(王伟本人)"}
        if "smq" in res:
            item["smq"] = res["smq"]
        print(f"[{res['status']:<13}] {q}")
        items.append(item)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"date": "2026-09-17", "model": __import__("dsl_loader").REG.version,
                   "items": items}, f, ensure_ascii=False, indent=1)
    n = {s: sum(1 for i in items if i["status"] == s) for s in set(i["status"] for i in items)}
    print(f"\ntranscript saved: {OUT}")
    print("status counts:", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
