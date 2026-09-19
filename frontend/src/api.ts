// InsightLite API 客户端：token 管理 + SSE（POST fetch 流式解析）
import type { QueryResult, Stage, StreamEvent } from "./types";

const API = "/api";
const TOKEN_KEY = "insightlite.token";
const ROLE_KEY = "insightlite.role";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function getRole(): string | null {
  return localStorage.getItem(ROLE_KEY);
}
export function saveSession(token: string, role: string) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(ROLE_KEY, role);
}
export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ROLE_KEY);
}

function authHeaders(): HeadersInit {
  const tok = getToken();
  return tok ? { Authorization: `Bearer ${tok}` } : {};
}

export async function devLogin(role: string): Promise<void> {
  const r = await fetch(`${API}/dev/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
  if (!r.ok) throw new Error(`登录失败（${r.status}）`);
  const body = await r.json();
  saveSession(body.token, role);
}

export async function fetchHealth(): Promise<{ model_version: string }> {
  const r = await fetch(`${API}/healthz`);
  return r.json();
}

export async function fetchDatasets(): Promise<Record<string, any>> {
  const r = await fetch(`${API}/datasets`, { headers: authHeaders() });
  if (r.status === 401 || r.status === 403) throw new Error("会话失效，请重新登录");
  return (await r.json()).datasets;
}

/**
 * SSE 对话流：POST /ask/stream，逐事件回调。
 * 浏览器 EventSource 不支持自定义 Header/POST，故用 fetch ReadableStream 手工解析
 * `data: {json}\n\n` 帧——SSE 协议本身不变，仅传输载体换 POST。
 */
export async function askStream(
  question: string,
  onStage: (stage: Stage, ev: Partial<StreamEvent>) => void,
  onFinal: (result: QueryResult) => void,
): Promise<void> {
  let r: Response;
  try {
    r = await fetch(`${API}/ask/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ question }),
    });
  } catch (e) {
    throw new Error("无法连接服务，请确认后端已启动（uvicorn main:app --port 8000）");
  }
  if (r.status === 503) throw new Error("LLM_API_KEY 未配置，/ask/stream 不可用（可用 /query 确定性路径）");
  if (r.status === 401 || r.status === 403) throw new Error("会话失效，请重新登录");
  if (!r.ok || !r.body) throw new Error(`请求失败（${r.status}）`);

  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.stage === "final") {
          const { stage: _s, ...result } = ev;
          onFinal(result as QueryResult);
        } else {
          onStage(ev.stage as Stage, ev);
        }
      }
    }
  }
}

/** 确定性查询（SMQ 直查，调试/回放用） */
export async function postQuery(smq: Record<string, unknown>): Promise<QueryResult> {
  const r = await fetch(`${API}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ smq }),
  });
  if (r.status === 401 || r.status === 403) throw new Error("会话失效，请重新登录");
  if (r.status === 422) {
    const b = await r.json();
    throw new Error(`非法 SMQ：${b.detail}`);
  }
  return r.json();
}
