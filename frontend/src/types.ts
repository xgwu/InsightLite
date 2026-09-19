// InsightLite 前端类型（与 backend/api/schemas.py 对齐）

export interface QueryData {
  columns: string[];
  rows: (string | number | null)[][];
}

export interface QueryResult {
  status: "answered" | "denied" | "refused_scope" | "clarify" | "error";
  decision?: "ALLOW" | "MASK" | "DENY";
  reason?: string;
  text?: string;
  sql?: string;
  smq?: Record<string, unknown>;
  total?: number;
  data?: QueryData;
}

export type Stage = "intent" | "permission" | "execute" | "final";

export interface StreamEvent {
  stage: Stage;
  smq?: Record<string, unknown>;
}  // final 事件携带 QueryResult 全部字段

export interface Msg {
  role: "user" | "assistant";
  text: string;
  result?: QueryResult;
  stage?: Stage;        // 进行中的阶段（仅 assistant 流式期间）
  error?: string;
}
