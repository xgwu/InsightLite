// 对话页：SSE 流式接收，分阶段状态（意图->权限->执行->final）
import { useRef, useState } from "react";
import { Alert, Button, Input, Space, Steps, Tooltip } from "antd";
import { ClearOutlined, QuestionCircleOutlined, SendOutlined } from "@ant-design/icons";
import { askStream, getRole, getToken } from "./api";
import type { Msg, Stage } from "./types";
import { ResultCard } from "./ResultCard";

const STAGE_ITEMS = [
  { title: "意图解析" },
  { title: "权限校验" },
  { title: "执行查询" },
];

const STAGE_INDEX: Record<Stage, number> = { intent: 0, permission: 1, execute: 2, final: 3 };

const EXAMPLES = ["各部门平均工资", "上个月全公司迟到次数", "按部门统计在职人数"];

export function Chat({ onLogout }: { onLogout: () => void }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  const scrollBottom = () => {
    requestAnimationFrame(() => {
      listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
    });
  };

  const send = async (q?: string) => {
    const question = (q ?? input).trim();
    if (!question || busy) return;
    if (!getToken()) { onLogout(); return; }
    setInput("");
    setBusy(true);
    setMsgs((m) => [...m, { role: "user", text: question }, { role: "assistant", text: "", stage: "intent" }]);
    scrollBottom();
    const patchLast = (patch: Partial<Msg>) =>
      setMsgs((m) => { const c = [...m]; c[c.length - 1] = { ...c[c.length - 1], ...patch }; return c; });

    try {
      await askStream(
        question,
        (stage) => { patchLast({ stage }); scrollBottom(); },
        (result) => { patchLast({ stage: "final", result, text: result.text ?? "" }); scrollBottom(); },
      );
    } catch (e: any) {
      patchLast({ stage: "final", error: e?.message ?? String(e) });
    } finally {
      setBusy(false);
      scrollBottom();
    }
  };

  const currentStage = msgs[msgs.length - 1]?.role === "assistant" && busy
    ? msgs[msgs.length - 1].stage : undefined;

  return (
    <div className="chat-wrap">
      <div className="chat-header">
        <Space>
          <b>InsightLite</b>
          <span className="role-badge">{getRole()}</span>
        </Space>
        <Space>
          <Tooltip title="权限语义：老板全量 / 经理本部门 MASK / 员工仅本人；auditor 仅 public">
            <QuestionCircleOutlined />
          </Tooltip>
          <Button size="small" icon={<ClearOutlined />} onClick={() => setMsgs([])}>清空</Button>
          <Button size="small" onClick={onLogout}>退出</Button>
        </Space>
      </div>

      <div className="chat-list" ref={listRef}>
        {msgs.length === 0 && (
          <div className="chat-empty">
            <p>向数据提问，例如：</p>
            {EXAMPLES.map((e) => (
              <Button key={e} size="small" type="dashed" onClick={() => send(e)}>{e}</Button>
            ))}
          </div>
        )}
        {msgs.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="bubble user">{m.text}</div>
          ) : (
            <div key={i} className="bubble assistant">
              {m.error ? (
                <Alert type="error" showIcon message={m.error} />
              ) : m.result ? (
                <ResultCard result={m.result} />
              ) : (
                <Steps size="small" direction="vertical"
                  current={STAGE_INDEX[(m.stage ?? "intent") as Stage]}
                  items={STAGE_ITEMS.slice(0, m.stage === "intent" ? 1 : m.stage === "permission" ? 2 : 3)} />
              )}
            </div>
          ),
        )}
      </div>

      <div className="chat-input">
        <Space.Compact style={{ width: "100%" }}>
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder={busy ? "查询进行中…" : "用自然语言提问（Enter 发送）"}
            disabled={busy}
          />
          <Button type="primary" icon={<SendOutlined />} onClick={() => send()} loading={busy} />
        </Space.Compact>
      </div>
      {currentStage && <div className="stage-mask" />}
    </div>
  );
}
