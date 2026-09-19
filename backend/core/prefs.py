# -*- coding: utf-8 -*-
"""W13 · 个人级默认时间偏好（FR-17 修订版）。

语义：用户可设置一个「未指明时间时的缺省时间范围」。仅当问题本身没说时间时，
编排层把它注入提示词作为偏好提示（不是硬约束——LLM 不可信，偏好只影响时间口径，
不触碰权限向量；权限仍由策略引擎独立裁定）。

隐私语义：偏好是**个人 UI 级数据**——只能读写本人那条（emp_id 键），
hr_admin 也没有读他人偏好的端点（试点期不需要，少一个面就少一个风险）。
值走白名单枚举（防提示词注入：值会被拼进系统提示词，枚举外一律 422）。

存储：data/preferences.json（{emp_id: {"default_time_scope": "..."}},
单文件 + 原子替换；v1 容错：文件损坏按空处理并在 stderr 留痕）。
"""
import datetime as _dt
import json
import os
import sys
import threading

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORE = os.environ.get("INSIGHTLITE_PREFS_STORE", os.path.join(BASE, "data", "preferences.json"))

# 白名单（与 SMQ time_window.preset 同源口径；空串 = 清除偏好）
SCOPES = ("", "this_week", "this_month", "last_month", "last_3_months")
LABELS = {"this_week": "本周", "this_month": "本月", "last_month": "上个月",
          "last_3_months": "最近三个月", "": "（未设置）"}
_LOCK = threading.Lock()


def load_all() -> dict:
    if not os.path.exists(STORE):
        return {}
    try:
        with open(STORE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[prefs] WARN: store unreadable, treated as empty: {e}", file=sys.stderr)
        return {}


def get(emp_id) -> dict:
    return (load_all().get(str(emp_id)) or {}).copy()


def set_scope(emp_id, scope: str) -> dict:
    """设置/清除偏好。scope 必须在白名单内（调用方先校验亦可，此处兜底）。"""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    with _LOCK:
        allp = load_all()
        if scope == "":
            allp.pop(str(emp_id), None)          # 清除 = 删键，不留空壳
        else:
            allp[str(emp_id)] = {"default_time_scope": scope,
                                 "updated_at": _dt.datetime.now().isoformat(timespec="seconds")}
        os.makedirs(os.path.dirname(STORE), exist_ok=True)
        tmp = STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(allp, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STORE)
    return get(emp_id)


def hint(emp_id) -> str:
    """编排层提示词注入行。未设置或值非法 -> 空串（不注入，绝不猜测）。"""
    scope = (get(emp_id) or {}).get("default_time_scope", "")
    if scope not in LABELS or scope == "":
        return ""
    return (f"【用户个人偏好】该用户设置了默认时间范围：{LABELS[scope]}（{scope}）。"
            f"仅当问题完全未指明时间时，按此缺省理解并在回答中说明；"
            f"问题指明了时间则以问题为准。")
