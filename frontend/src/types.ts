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
  default?: string | null;   // W13: 澄清缺省建议（一键采纳）
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
  question?: string;    // 触发本回答的用户问题（加入看板默认标题）
}

// W9 看板
export type ChartType = "bar" | "line" | "pie" | "table";

export interface WidgetDef {
  id: string;
  title: string;
  smq: Record<string, unknown>;
  chart: ChartType;
  layout: { x: number; y: number; w: number; h: number };
  created_by?: string;
  created_at?: number;
  result?: QueryResult;   // 读取时以当前用户身份重执行的结果（权限读取时重裁）
}
