# InsightLite Frontend（W8）

React 18 + TypeScript + Vite + AntD 5 + ECharts。

## 启动

```bash
# 1) 后端（InsightLite 根目录）
uvicorn main:app --port 8000 --app-dir backend/api
#    或：cd backend/api && python -m uvicorn main:app --port 8000
#    需要 LLM_API_KEY（.env 或环境变量）才有 /ask/stream；/query 无 Key 可用

# 2) 前端
npm install
npm run dev        # http://localhost:5173（/api 代理到 8000）
```

## 结构

- `src/api.ts` — token 管理 + **POST fetch 流式 SSE 解析**（`data: {json}\n\n` 帧解析，
  因浏览器 EventSource 不支持 POST/自定义 Header）
- `src/Chat.tsx` — 对话页：分阶段 Steps（意图解析 → 权限校验 → 执行查询），final 渲染结果
- `src/ResultCard.tsx` — 结果卡片：决策 Tag / Markdown 表格 / 二维单数值自动 ECharts 柱图 / SQL+SMQ 折叠
- `src/Markdown.tsx` — 服务端 render_result 输出（段落/表格/引用）的最小受控渲染子集
- `src/Login.tsx` — mock SSO 角色选择（/dev/login；真实 SSO 上线后整体替换）

## 权限演示（PRD 三层模型 + 2026-09-19 裁决）

| 角色 | 数据边界 |
|---|---|
| exec / hrbp | 全量（D4 默认豁免，逐次审计落盘 `data/audit/`） |
| manager | 本部门子树行级收窄；敏感列 MASK（NULL 占位） |
| employee | 仅本人行；查他人 → R4 显式拒绝 |
| auditor | 仅 public（部门架构）；任一非 public 引用整查询拒绝 |
