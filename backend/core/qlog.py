# -*- coding: utf-8 -*-
"""W11 · 查询日志（试点三指标日报的「总问答数」分母来源）。

append-only JSONL 按日滚动（data/qlog/qlog-YYYYMMDD.jsonl），每次问答一条：
  ts / source / role / status / dataset / dur_ms / smq（口径全文，与审计同款先例）。

与 D4 豁免审计的纪律区别（刻意不同，须防止误混）：
  audit 是**治理面**——豁免审计写失败 = 查询失败（fail-closed）；
  qlog 是**观测面**——写失败不阻断业务查询，但必须打到 stderr 可见（不做无声降级）。
  静默错误率的分母靠它，因此丢一条就少一条分母；stderr 留痕便于发现存储问题。
"""
import datetime as _dt
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QLOG_DIR = os.environ.get("INSIGHTLITE_QLOG_DIR", os.path.join(BASE, "data", "qlog"))


def qlog_path(ts=None) -> str:
    ts = ts or _dt.datetime.now()
    return os.path.join(QLOG_DIR, f"qlog-{ts:%Y%m%d}.jsonl")


def record(source: str, ctx: dict, status: str, smq: dict = None,
           dur_ms: int = None, ts: str = None) -> None:
    """记录一条问答。观测面：失败打 stderr 不抛错（不阻断业务）。"""
    try:
        entry = {
            "ts": ts or _dt.datetime.now().isoformat(timespec="seconds"),
            "source": source,                 # api-query / api-ask / sse-final / dashboard-read
            "role": ctx.get("role"),
            "status": status,                 # answered / denied / refused_scope / clarify / error
            "dataset": (smq or {}).get("dataset"),
            "dur_ms": dur_ms,
            "smq": smq,
        }
        os.makedirs(QLOG_DIR, exist_ok=True)
        with open(qlog_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[qlog] WARN: write failed: {e}", file=sys.stderr)


def read_rows(days: int = 7) -> list:
    """读最近 N 天日志（含今天）。按 ts 升序返回。"""
    rows = []
    if not os.path.isdir(QLOG_DIR):
        return rows
    today = _dt.date.today()
    for i in range(days):
        d = today - _dt.timedelta(days=i)
        p = os.path.join(QLOG_DIR, f"qlog-{d:%Y%m%d}.jsonl")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue                  # 半写行跳过
    rows.sort(key=lambda r: r.get("ts", ""))
    return rows
