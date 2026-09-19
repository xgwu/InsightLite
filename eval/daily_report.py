# -*- coding: utf-8 -*-
"""W11 · 三指标日报（dev-plan W11–12：口径一致率 / 静默错误率 / 明确失败率）。

口径声明（分母分层纪律：一个集合不承载两个分母，逐项显式标注）：
  1) 口径一致率 —— 分母 = eval harness 语义覆盖集合（smq/flexible 型 LLM 意图题）；
     分子 = 语义判定 PASS。数值直接解析 eval_harness.py 离线回放输出（与 run_all 同源）。
  2) 静默错误率 —— v1 生产口径：分母 = 窗口内总问答数（qlog，含 dashboard-read 计数
     并单独列出），分子 = accepted 且 category=wrong_number 的纠正数。
     dev-plan 原文括注「分母 = 语义覆盖集合」如指 eval 集合口径，需反馈关联 eval case
     的数据面（二期）；v1 不把两个分母混进一个比值——此处显式声明，宁注明不假装。
  3) 明确失败率 —— 分母 = 窗口内总问答数；分子 = refused_scope / denied / clarify / error。
     明确失败是诚实行为，看趋势不看绝对值。

输出：stdout 摘要 + data/reports/daily-YYYYMMDD.md（可归档/贴群）。
用法：python eval/daily_report.py [--days 7]
"""
import datetime as _dt
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend", "core"))

import feedback as FB  # noqa: E402
import qlog as Q  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPORT_DIR = os.environ.get("INSIGHTLITE_REPORT_DIR", os.path.join(ROOT, "data", "reports"))

FAIL_STATUSES = ("refused_scope", "denied", "clarify", "error")


def _parse_eval_output(text: str) -> dict:
    """从 eval_harness.py 输出解析两项比值。"""
    out = {}
    m = re.search(r"EVAL RESULT: (\d+)/(\d+)", text)
    if m:
        out["eval_pass"], out["eval_total"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"口径一致率基线（W5 M1）: (\d+)/(\d+) = ([\d.]+)%", text)
    if m:
        out["cons_pass"], out["cons_total"] = int(m.group(1)), int(m.group(2))
        out["cons_rate"] = float(m.group(3))
    return out


def compute_metrics(qlog_rows: list, accepted_wrong: int, eval_out: dict) -> dict:
    """三项指标计算（纯函数，可测）。qlog_rows 为窗口内日志。"""
    total = len(qlog_rows)
    dash = sum(1 for r in qlog_rows if r.get("source") == "dashboard-read")
    fails = sum(1 for r in qlog_rows if r.get("status") in FAIL_STATUSES)
    m = {
        "window_questions": total,
        "dashboard_reads": dash,
        "silent_error_rate": (accepted_wrong / total * 100) if total else None,
        "accepted_wrong_number": accepted_wrong,
        "explicit_failures": fails,
        "explicit_fail_rate": (fails / total * 100) if total else None,
        "fail_breakdown": {s: sum(1 for r in qlog_rows if r.get("status") == s)
                           for s in FAIL_STATUSES},
    }
    m.update(eval_out)
    return m


def main(days: int = 7) -> int:
    today = _dt.date.today()
    # 1) eval harness 离线回放（与 run_all 同源，保证口径一致率不另起炉灶）
    r = subprocess.run([sys.executable, os.path.join(HERE, "eval_harness.py")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT)
    eval_out = _parse_eval_output(r.stdout or "")
    if not eval_out:
        eval_out = {"eval_error": (r.stderr or r.stdout or "")[-200:]}

    # 2) qlog + feedback
    rows = Q.read_rows(days=days)
    accepted_wrong = FB.accepted_wrong_number(days=days)
    m = compute_metrics(rows, accepted_wrong, eval_out)

    # 3) 闭环状态
    aging = FB.aging_open()
    pend = FB.pending_cases()

    lines = [
        f"# InsightLite 三指标日报 · {today:%Y-%m-%d}（窗口近 {days} 天）",
        "",
        f"## 1) 口径一致率（分母 = 语义覆盖集合，eval 离线回放）",
        (f"- **{m['cons_pass']}/{m['cons_total']} = {m['cons_rate']:.1f}%**（门槛 80%）"
         if "cons_rate" in m else f"- ⚠ eval 输出解析失败：{m.get('eval_error', '')}"),
        f"- eval 全量：{m.get('eval_pass', '?')}/{m.get('eval_total', '?')}",
        "",
        f"## 2) 静默错误率（v1 生产口径：分母 = 窗口内总问答数）",
        f"- 总问答数：{m['window_questions']}（其中看板读取 {m['dashboard_reads']}，"
        f"对话/查询 {m['window_questions'] - m['dashboard_reads']}）",
        f"- accepted wrong_number 纠正：{m['accepted_wrong_number']}",
        (f"- **静默错误率 = {m['silent_error_rate']:.2f}%**"
         if m["silent_error_rate"] is not None else "- 窗口内无问答，比率无定义（分母 0——不报 0%，报无定义）"),
        "- 口径注记：dev-plan 括注「分母 = 语义覆盖集合」如指 eval 集合，需反馈关联 eval case "
        "的数据面（二期）；v1 显式采用生产问答数口径，两口径不混算。",
        "",
        f"## 3) 明确失败率（分母 = 窗口内总问答数）",
        (f"- **{m['explicit_failures']}/{m['window_questions']} = {m['explicit_fail_rate']:.1f}%** "
         if m["explicit_fail_rate"] is not None else "- 窗口内无问答"),
        f"- 分布：{json.dumps(m['fail_breakdown'], ensure_ascii=False)}（明确失败是诚实行为，看趋势）",
        "",
        "## 闭环健康",
        f"- 超 48h 未处理的 open 反馈：{len(aging)}" + (f"（{', '.join(f['id'] for f in aging[:5])}）" if aging else " ✅"),
        f"- 待人工补断言的 eval case 骨架：{len(pend)}（eval/pending_cases.json）",
        "",
    ]
    report = "\n".join(lines)
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"daily-{today:%Y%m%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print(f"report written: {path}")
    return 0


if __name__ == "__main__":
    days = 7
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    sys.exit(main(days))
