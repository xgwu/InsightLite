// 结果卡片：中文表格 + 单维聚合自动图表（ECharts）+ SQL/SMQ 折叠 + 一键固化入板
import { Modal } from "antd";
import { PushpinOutlined } from "@ant-design/icons";
import { Collapse, Tag, Typography } from "antd";
import type { ChartType, QueryResult } from "./types";
import { Chart } from "./Chart";
import { Markdown } from "./Markdown";

export function ResultCard({ result, question, onAddToDashboard }: {
  result: QueryResult;
  question?: string;
  onAddToDashboard?: (title: string, smq: Record<string, unknown>, chart: ChartType) => void;
}) {
  const { data } = result;
  // 图表条件：二维单数值（分组 + 单聚合）且行数 1..50
  const chartable = !!data && data.columns.length === 2 && data.rows.length > 0
    && data.rows.length <= 50 && data.rows.every((r) => typeof r[1] === "number");

  const decisionTag = result.decision && (
    <Tag color={result.decision === "DENY" ? "red" : result.decision === "MASK" ? "orange" : "green"}>
      {result.decision}
    </Tag>
  );

  // FR-24/27：一键固化带完整口径；入板前强制「数值人工比对一次」的补偿提示
  const pin = () => {
    const chart: ChartType = chartable ? "bar" : "table";
    const title = (question || "未命名查询").slice(0, 40);
    Modal.confirm({
      title: "加入看板前，请先完成一次数值比对",
      content: `FR-27 补偿动作：自动化数值验证缺位期间，加入看板的数字必须与对话结果人工核对一次（当前共 ${result.total ?? data?.rows.length ?? 0} 组）。确认无误后入板。`,
      okText: "已核对，加入看板",
      cancelText: "取消",
      onOk: () => onAddToDashboard?.(title, result.smq!, chart),
    });
  };

  const items = [];
  if (result.sql) {
    items.push({
      key: "sql", label: "SQL（策略注入后）",
      children: <pre className="code-block">{result.sql}</pre>,
    });
  }
  if (result.smq) {
    items.push({
      key: "smq", label: "SMQ 查询契约",
      children: <pre className="code-block">{JSON.stringify(result.smq, null, 2)}</pre>,
    });
  }
  if (data && !chartable) {
    items.push({
      key: "data", label: `结构化数据（${data.rows.length} 行）`,
      children: (
        <table className="md-table">
          <thead><tr>{data.columns.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
          <tbody>
            {data.rows.map((r, i) => (
              <tr key={i}>{r.map((v, j) => <td key={j}>{v === null ? "—" : String(v)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      ),
    });
  }

  return (
    <div className="result-card">
      <div className="result-meta">
        {decisionTag}
        {result.total != null && <Typography.Text type="secondary">{result.total.toLocaleString()} 组</Typography.Text>}
        {onAddToDashboard && result.status === "answered" && result.smq && (
          <a onClick={pin} style={{ marginLeft: "auto", fontSize: 12 }}>
            <PushpinOutlined /> 加入看板
          </a>
        )}
      </div>
      <Markdown text={result.text ?? ""} />
      {chartable && <Chart data={data!} />}
      {items.length > 0 && <Collapse ghost size="small" items={items} style={{ marginTop: 8 }} />}
    </div>
  );
}
