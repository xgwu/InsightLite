// 结果卡片：中文表格 + 单维聚合自动图表（ECharts）+ SQL/SMQ 折叠
import { useEffect, useRef } from "react";
import { Collapse, Tag, Typography } from "antd";
import * as echarts from "echarts";
import type { QueryResult } from "./types";
import { Markdown } from "./Markdown";

function Chart({ data }: { data: NonNullable<QueryResult["data"]> }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const [dimCol] = data.columns;
    const numIdx = data.columns.findIndex((_, i) => i > 0 && typeof data.rows[0]?.[i] === "number");
    chart.setOption({
      grid: { left: 48, right: 16, top: 32, bottom: 64 },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: data.rows.map((r) => String(r[0] ?? "—")), axisLabel: { rotate: 30 } },
      yAxis: { type: "value" },
      series: [{
        type: "bar",
        name: numIdx > 0 ? data.columns[numIdx] : dimCol,
        data: data.rows.map((r) => (numIdx > 0 ? r[numIdx] : r[1])),
        itemStyle: { color: "#1677ff" },
      }],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("resize", onResize); chart.dispose(); };
  }, [data]);
  return <div ref={ref} style={{ width: "100%", height: 320, marginTop: 12 }} />;
}

export function ResultCard({ result }: { result: QueryResult }) {
  const { data } = result;
  // 图表条件：二维单数值（分组 + 单聚合）且行数 1..50
  const chartable = !!data && data.columns.length === 2 && data.rows.length > 0
    && data.rows.length <= 50 && data.rows.every((r) => typeof r[1] === "number");

  const decisionTag = result.decision && (
    <Tag color={result.decision === "DENY" ? "red" : result.decision === "MASK" ? "orange" : "green"}>
      {result.decision}
    </Tag>
  );

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
      </div>
      <Markdown text={result.text ?? ""} />
      {chartable && <Chart data={data!} />}
      {items.length > 0 && <Collapse ghost size="small" items={items} style={{ marginTop: 8 }} />}
    </div>
  );
}
