// 服务端 Markdown 渲染的最小子集（render_result 的输出形状固定：段落/表格/引用）
// 刻意不引 react-markdown：渲染面受控（W4 纪律：渲染是纯展示层），子集之外原样转义显示。
import React from "react";

function renderTable(lines: string[], key: number): React.ReactNode {
  const rows = lines.map((l) =>
    l.split("|").slice(1, -1).map((c) => c.trim()),
  );
  const [header, , ...body] = rows;   // 第二行是 |---| 分隔
  return (
    <table key={key} className="md-table">
      <thead>
        <tr>{header.map((h, i) => <th key={i}>{h}</th>)}</tr>
      </thead>
      <tbody>
        {body.map((r, i) => (
          <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
        ))}
      </tbody>
    </table>
  );
}

export function Markdown({ text }: { text: string }) {
  if (!text) return null;
  const lines = text.split("\n");
  const out: React.ReactNode[] = [];
  let i = 0, key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("|")) {
      let j = i;
      while (j < lines.length && lines[j].startsWith("|")) j++;
      out.push(renderTable(lines.slice(i, j), key++));
      i = j;
    } else if (line.startsWith(">")) {
      const quote = lines[i].replace(/^>\s?/, "");
      out.push(<blockquote key={key++} className="md-quote">{quote}</blockquote>);
      i++;
    } else if (line.trim() === "") {
      i++;
    } else {
      out.push(<p key={key++}>{line}</p>);
      i++;
    }
  }
  return <div className="md">{out}</div>;
}
