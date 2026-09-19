// 通用图表：ECharts 封装（对话结果卡与看板 widget 共用）
import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { ChartType, QueryData } from "./types";

const COLORS = { bar: "#1677ff", line: "#1677ff", pie: ["#1677ff", "#36cfc9", "#9254de", "#f759ab", "#52c41a", "#faad14"] };

export function Chart({ data, chart = "bar", height = 300 }:
  { data: QueryData; chart?: ChartType; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || chart === "table") return;
    const el = echarts.init(ref.current);
    const numIdx = data.columns.findIndex((_, i) => i > 0 && typeof data.rows[0]?.[i] === "number");
    const valueIdx = numIdx > 0 ? numIdx : 1;
    const cats = data.rows.map((r) => String(r[0] ?? "—"));
    const vals = data.rows.map((r) => r[valueIdx]);
    const seriesName = data.columns[valueIdx] ?? data.columns[0];

    let series: any;
    let xAxis: any = undefined;
    if (chart === "pie") {
      series = [{
        type: "pie", radius: "62%",
        data: data.rows.map((r, i) => ({
          name: cats[i],
          value: typeof r[valueIdx] === "number" ? r[valueIdx] : 1,
        })),
        label: { formatter: "{b}: {c}" },
      }];
    } else {
      xAxis = { type: "category", data: cats, axisLabel: { rotate: cats.length > 8 ? 30 : 0 } };
      series = [{ type: chart, name: seriesName, data: vals, itemStyle: { color: COLORS[chart] } }];
    }
    el.setOption({
      grid: { left: 56, right: 16, top: 32, bottom: cats.length > 8 ? 72 : 48 },
      tooltip: { trigger: "axis" },
      xAxis, yAxis: xAxis ? { type: "value" } : undefined,
      series,
    });
    const onResize = () => el.resize();
    window.addEventListener("resize", onResize);
    const t = setTimeout(onResize, 60);           // react-grid-layout 拖拽后容器尺寸变化
    return () => { clearTimeout(t); window.removeEventListener("resize", onResize); el.dispose(); };
  }, [data, chart]);
  if (chart === "table") {
    return (
      <table className="md-table">
        <thead><tr>{data.columns.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
        <tbody>
          {data.rows.map((r, i) => (
            <tr key={i}>{r.map((v, j) => <td key={j}>{v === null ? "—" : String(v)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    );
  }
  return <div ref={ref} style={{ width: "100%", height }} />;
}
