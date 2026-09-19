# -*- coding: utf-8 -*-
"""W10 · 轻量 REST API 拉取连接器 v1（dev-plan W10：配置式 / 定时同步 -> staging /
复用 FR-2 推断链路做字段映射人工确认 / 失败告警 + 重试；OAuth / 增量同步留二期）。

安全与纪律（与全项目 fail-closed 同源）：
  - 配置存 data/connectors.json；**凭据绝不入库**——配置只存 token_env（环境变量名），
    运行时读取，日志/接口回显一律脱敏。
  - 目标表名强校验 ^stg_[a-z0-9_]+$（连接器只能写 staging 区，禁止触正式表——
    正式消费必须经语义建模 + 权限引擎，连接器不存在绕过权限的写路径）。
  - 字段映射人工确认（FR-2 推断链路）：未确认时同步仍落 staging（安全区），
    但响应携带推断建议（映射 + 类型），确认后才冻结 mapping；不确认可反复同步。
  - 失败重试（指数退避）耗尽后写告警 JSONL（data/alerts/，与 audit 同款 append-only 纪律）。
  - 同步状态 data/connector_state.json（last_run/status/rows/error）。

v1 分页：none / page（页码+页大小，空页或短页停）/ offset / cursor（响应路径取下一游标）。
零新增依赖：HTTP 用 urllib；transport 可注入（离线测试 monkeypatch _http_json）。
"""
import datetime as _dt
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DATA_DIR = os.environ.get("INSIGHTLITE_DATA_DIR", os.path.join(BASE, "data"))
STORE_PATH = os.path.join(DATA_DIR, "connectors.json")
STATE_PATH = os.path.join(DATA_DIR, "connector_state.json")
ALERT_DIR = os.environ.get("INSIGHTLITE_ALERT_DIR", os.path.join(DATA_DIR, "alerts"))

TABLE_RE = re.compile(r"^stg_[a-z0-9_]+$")
RETRIES = 2                       # 首次失败后的额外尝试次数（指数退避 0.2s*2^n）
HTTP_TIMEOUT = 30
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?$")


# ---------------------------------------------------------------- 配置存储
def load_configs() -> list:
    if not os.path.exists(STORE_PATH):
        return []
    with open(STORE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_configs(cfgs: list) -> None:
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfgs, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE_PATH)               # 原子替换


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def validate_config(cfg: dict) -> str:
    """配置合法性校验；返回错误信息或空串。凭据只允许 token_env 引用。"""
    cid = cfg.get("id") or ""
    if not re.match(r"^[a-z0-9_]{2,40}$", cid):
        return "id 须为 2-40 位小写字母/数字/下划线"
    url = cfg.get("url") or ""
    if not (url.startswith("http://") or url.startswith("https://")):
        return "url 必须是 http(s) 地址"
    auth = cfg.get("auth") or {}
    if auth.get("type") not in (None, "bearer", "api_key", "basic"):
        return "auth.type 仅支持 bearer/api_key/basic"
    if auth and not auth.get("token_env"):
        return "auth 必须引用环境变量名（token_env），凭据不得写入配置"
    if auth.get("type") == "basic" and not auth.get("username_env"):
        return "basic 鉴权还需 username_env"
    pg = cfg.get("pagination") or {"type": "none"}
    if pg.get("type") not in ("none", "page", "offset", "cursor"):
        return "pagination.type 仅支持 none/page/offset/cursor"
    if not TABLE_RE.match(cfg.get("target_table") or ""):
        return "target_table 必须形如 stg_xxx（连接器只能写 staging 区）"
    fm = cfg.get("field_mapping") or {}
    for k, v in fm.items():
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(v)):
            return f"field_mapping 目标列名不合法: {v!r}"
    return ""


def redact(cfg: dict) -> dict:
    """接口回显脱敏：去掉任何疑似凭据字段，只保留环境变量名。"""
    out = json.loads(json.dumps(cfg))
    auth = out.get("auth")
    if isinstance(auth, dict):
        for k in list(auth):
            if k not in ("type", "token_env", "username_env", "key_header"):
                auth.pop(k)
    return out


# ---------------------------------------------------------------- HTTP（可注入）
def _http_json(url: str, params: dict, headers: dict) -> dict:
    """真实传输（测试注入替身）。任何非 2xx 抛 RuntimeError。"""
    q = urllib.parse.urlencode(params or {})
    full = url + (("?" + q) if q else "")
    req = urllib.request.Request(full, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if not (200 <= resp.status < 300):
                raise RuntimeError(f"HTTP {resp.status}")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:       # noqa: 局部导入保持模块面干净
        raise RuntimeError(f"HTTP {e.code}") from None


def _auth_headers(cfg: dict) -> dict:
    auth = cfg.get("auth") or {}
    t = auth.get("type")
    if not t:
        return {}
    env = auth.get("token_env") or ""
    secret = os.environ.get(env, "")
    if not secret:
        raise RuntimeError(f"auth 环境变量 {env} 未设置（fail-closed，不做匿名降级）")
    if t == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    if t == "api_key":
        return {auth.get("key_header") or "X-API-Key": secret}
    if t == "basic":
        uenv = auth.get("username_env") or ""
        user = os.environ.get(uenv, "")
        import base64
        return {"Authorization": "Basic " + base64.b64encode(
            f"{user}:{secret}".encode()).decode()}
    return {}


def _dig(obj, path: str):
    """按点路径取嵌套值；缺失返回 None。"""
    cur = obj
    for part in (path.split(".") if path else []):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


# ---------------------------------------------------------------- 分页拉取
def _fetch_with_retry(cfg, params, headers, transport, retries=RETRIES):
    last = None
    for attempt in range(retries + 1):
        try:
            return transport(cfg["url"], params, headers)
        except Exception as e:                # noqa: 网络层异常统一重试
            last = e
            if attempt < retries:
                time.sleep(0.2 * (2 ** attempt))
    raise RuntimeError(f"拉取失败（重试 {retries} 次后放弃）: {last}")


def fetch_rows(cfg: dict, transport=None) -> list:
    """按分页配置聚合全量记录。max_pages 兜底防空转。"""
    transport = transport or _http_json
    pg = cfg.get("pagination") or {"type": "none"}
    headers = _auth_headers(cfg)
    base_params = dict(cfg.get("params") or {})
    max_pages = int(pg.get("max_pages") or 50)
    rows, page = [], 1
    cursor = None
    while page <= max_pages:
        params = dict(base_params)
        if pg.get("type") == "page":
            params[pg.get("page_param") or "page"] = page
            if pg.get("size_param"):
                params[pg.get("size_param")] = pg.get("size") or 200
        elif pg.get("type") == "offset":
            params[pg.get("offset_param") or "offset"] = (page - 1) * (pg.get("size") or 200)
            if pg.get("limit_param"):
                params[pg.get("limit_param")] = pg.get("size") or 200
        elif pg.get("type") == "cursor" and cursor is not None:
            params[pg.get("cursor_param") or "cursor"] = cursor
        data = _fetch_with_retry(cfg, params, headers, transport)
        batch = _dig(data, cfg.get("data_path") or "data")
        if batch is None:
            raise RuntimeError(f"响应中找不到 data_path={cfg.get('data_path') or 'data'!r}")
        if not isinstance(batch, list):
            raise RuntimeError("data_path 指向的不是数组")
        rows.extend(batch)
        if pg.get("type") == "none":
            break
        if pg.get("type") == "cursor":
            cursor = _dig(data, pg.get("cursor_path") or "meta.next_cursor")
            if not cursor:
                break
        else:
            size = pg.get("size") or 200
            if len(batch) < size:             # 短页/空页 -> 末页
                break
        page += 1
    return rows


# ---------------------------------------------------------------- FR-2 推断链路（字段映射建议 + 类型推断）
def infer_schema(rows: list, mapping: dict) -> dict:
    """从样本行推断目标列类型与映射建议（人工确认的输入，不自动生效为正式口径）。"""
    sample = rows[:50]
    keys = []
    for r in sample:
        for k in r.keys():
            if k not in keys:
                keys.append(k)
    proposed = {}
    types = {}
    for k in keys:
        target = mapping.get(k, k)             # 建议：同名直映，已有映射尊重之
        vals = [r.get(k) for r in sample if r.get(k) is not None]
        t = "text"
        if vals and all(isinstance(v, bool) for v in vals):
            t = "integer"
        elif vals and all(isinstance(v, int) for v in vals):
            t = "integer"
        elif vals and all(isinstance(v, (int, float)) for v in vals):
            t = "real"
        elif vals and all(isinstance(v, str) and ISO_DATE_RE.match(v) for v in vals):
            t = "text"                          # 日期以文本入 staging，语义层再定型
        proposed[k] = target
        types[target] = t
    return {"proposed_mapping": proposed, "inferred_types": types}


# ---------------------------------------------------------------- staging 写入
def write_staging(conn: sqlite3.Connection, table: str, rows: list,
                  mapping: dict, types: dict) -> int:
    """全量刷新 staging 表（drop+create+insert）。表名已在 validate 层锁死 stg_ 前缀。"""
    if not TABLE_RE.match(table):
        raise RuntimeError(f"illegal target table: {table!r}")
    if not rows:
        raise RuntimeError("拉取行数为 0（须 >0 才允许刷新 staging——空拉取视为异常）")
    aff = {"integer": "INTEGER", "real": "REAL"}
    srcs = list(dict.fromkeys(k for r in rows[:1] for k in r.keys()))
    targets = []
    for s in srcs:
        t = mapping.get(s, s)
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", t):
            raise RuntimeError(f"目标列名不合法: {t!r}")
        if t in targets:
            raise RuntimeError(f"多个源字段映射到同一目标列: {t!r}")
        targets.append(t)
    col_defs = ", ".join(f'"{c}" {aff.get(types.get(c, "text"), "TEXT")}' for c in targets)
    cur = conn.cursor()
    cur.execute(f'DROP TABLE IF EXISTS "{table}"')
    cur.execute(f'CREATE TABLE "{table}" ({col_defs})')
    quoted = ", ".join(f'"{c}"' for c in targets)
    placeholders = ", ".join("?" for _ in targets)
    sql = f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})'
    payload = [[_cast(r.get(s), types.get(mapping.get(s, s))) for s in srcs]
               for r in rows]
    cur.executemany(sql, payload)
    conn.commit()
    return len(rows)


def _cast(v, t):
    if v is None:
        return None
    try:
        if t == "integer":
            return int(v)
        if t == "real":
            return float(v)
    except (TypeError, ValueError):
        return v
    return v


# ---------------------------------------------------------------- 告警 + 状态
def raise_alert(cid: str, error: str, attempts_note: str = "") -> None:
    os.makedirs(ALERT_DIR, exist_ok=True)
    p = os.path.join(ALERT_DIR, f"alerts-{_dt.datetime.now():%Y%m}.jsonl")
    entry = {"ts": _dt.datetime.now().isoformat(timespec="seconds"),
             "connector": cid, "level": "error", "error": error[:300],
             "note": attempts_note}
    with open(p, "a", encoding="utf-8") as f:   # append-only
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def sync(cfg: dict, conn: sqlite3.Connection, transport=None) -> dict:
    """一次同步：拉取 -> 映射 -> staging -> 状态与告警。失败写告警并抛出。"""
    cid = cfg["id"]
    try:
        rows = fetch_rows(cfg, transport=transport)
        mapping = cfg.get("field_mapping") or {}
        if not mapping:
            mapping = {k: k for r in rows[:1] for k in r.keys()}   # 未配置 -> 同名直映
        schema = infer_schema(rows, mapping)
        n = write_staging(conn, cfg["target_table"], rows, schema["proposed_mapping"],
                          schema["inferred_types"])
        state = load_state()
        state[cid] = {"last_run": _dt.datetime.now().isoformat(timespec="seconds"),
                      "status": "ok", "rows": n,
                      "confirmed": bool(cfg.get("mapping_confirmed"))}
        save_state(state)
        return {"status": "ok", "rows": n, "table": cfg["target_table"],
                "confirmed": bool(cfg.get("mapping_confirmed")),
                "proposal": None if cfg.get("mapping_confirmed") else schema}
    except Exception as e:                    # noqa: 同步失败 -> 告警 + 状态落盘 + 上抛
        state = load_state()
        state[cid] = {"last_run": _dt.datetime.now().isoformat(timespec="seconds"),
                      "status": "error", "rows": 0, "error": str(e)[:300]}
        save_state(state)
        raise_alert(cid, str(e))
        raise


# ---------------------------------------------------------------- 定时调度（v1 轻量线程）
def due(cfg: dict, st: dict, now: float = None) -> bool:
    """是否到点：enabled + schedule_minutes>0 + 距上次运行已满间隔。纯函数可测。"""
    if not cfg.get("enabled") or not cfg.get("schedule_minutes"):
        return False
    last = (st or {}).get(cfg["id"], {}).get("_ts", 0)
    now = now if now is not None else time.time()
    return now - last >= cfg["schedule_minutes"] * 60


class Scheduler(threading.Thread):
    """每 30s 扫描一次到期连接器（daemon；uvicorn 启动时按需拉起）。
    v1 串行执行：试点规模下简单正确优先于并发。"""
    daemon = True

    def __init__(self, get_conn):
        super().__init__(name="connector-scheduler")
        self.get_conn = get_conn
        self._stop = threading.Event()

    def run(self):
        while not self._stop.wait(30):
            for cfg in load_configs():
                state = load_state()
                st = state.get(cfg["id"], {})
                now = time.time()
                if not due(cfg, {**st, "_ts": st.get("_ts", 0)}, now):
                    continue
                try:
                    conn = self.get_conn()
                    try:
                        sync(cfg, conn)
                    finally:
                        conn.close()
                except Exception:
                    pass                      # sync 内已告警+落状态；调度循环不中断
                s = load_state()
                s.setdefault(cfg["id"], {})["_ts"] = now
                save_state(s)


_scheduler = None


def start_scheduler(get_conn):
    """幂等启动（env CONNECTOR_SCHEDULER=1 才生效——CI/测试默认关闭）。"""
    global _scheduler
    if os.environ.get("CONNECTOR_SCHEDULER") != "1":
        return None
    if _scheduler is None or not _scheduler.is_alive():
        _scheduler = Scheduler(get_conn)
        _scheduler.start()
    return _scheduler


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfgs = load_configs()
    print(f"connector module ok -> {len(cfgs)} config(s), store={STORE_PATH}")
    for c in cfgs:
        err = validate_config(c)
        print(f"  - {c.get('id')}: {'OK' if not err else 'INVALID: ' + err}")
