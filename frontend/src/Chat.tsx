// 对话页：SSE 流式接收，分阶段状态（意图->权限->执行->final）+ 一键固化入板 + 看板下钻回跳
import { useEffect, useRef, useState } from "react";
import { Alert, App as AntApp, Button, Input, Space, Steps, Tooltip } from "antd";
import { ClearOutlined, DashboardOutlined, QuestionCircleOutlined, SendOutlined } from "@ant-design/icons";
import { addWidget, askStream, getRole, getToken } from "./api";
import type { Msg, Stage, WidgetDef } from "./types";
import { ResultCard } from "./ResultCard";

const STAGE_ITEMS = [
  { title: "意图解析" },
  { title: "权限校验" },
  { title: "执行查询" },
];

const STAGE_INDEX: Record<Stage, number> = { intent: 0, permission: 1, execute: 2, final: 3 };

const EXAMPLES = ["各部门平均工资", "上个月全公司迟到次数", "按部门统计在职人数"];

export function Chat({ onLogout, onGoDashboard, drillContext, onConsumeDrill }: {
  onLogout: () => void;
  onGoDashboard: () => void;
  drillContext?: WidgetDef | null;          // 看板点击标题回跳携带的口径上下文
  onConsumeDrill: () => void;
}) {
  const { message } = AntApp.useApp();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  // FR-26 看板 -> 对话回跳：带着该图的口径作为上下文继续追问
  useEffect(() => {
    if (!drillContext) return;
    setMsgs((m) => [...m,
      { role: "user", text: `📊 下钻看板「${drillContext.title}」` },
      { role: "assistant", text: "已带上该图的查询口径（SMQ）。请继续用自然语言追问，例如「只要其中工资最高的前 5 人」「换成上个月再看一次」。", result: { status: "answered", text: "", smq: drillContext.smq } },
    ]);
    onConsumeDrill();
    requestAnimationFrame(scrollBottom);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drillContext]);

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
    setMsgs((m) => [...m, { role: "user", text: question }, { role: "assistant", text: "", stage: "intent", question }]);
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

  const handleAddToDashboard = async (title: string, smq: Record<string, unknown>, chart: any) => {
    try {
      await addWidget(title, smq, chart);
      message.success(`已加入看板：「${title}」（其他角色打开时将按各自权限重新执行）`);
    } catch (e: any) {
      message.error(e?.message ?? "加入看板失败");
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
          <Button size="small" icon={<DashboardOutlined />} onClick={onGoDashboard}>看板</Button>
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
                <ResultCard result={m.result} question={m.question} onAddToDashboard={handleAddToDashboard} />
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
