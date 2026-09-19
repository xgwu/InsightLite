// W9 看板页：react-grid-layout 拖拽布局 + 读取时权限重裁的 widget 渲染 + 回跳下钻
// 安全语义（FR-33）：看板只存口径不存数据，每个 widget 以当前用户身份重新执行——
// 同一看板不同角色看到不同范围；denied widget 保留卡片显示原因（不静默消失）。
import { useCallback, useEffect, useState } from "react";
import { Alert, App as AntApp, Button, Card, Empty, Space, Spin, Tag, Tooltip } from "antd";
import { DeleteOutlined, LineChartOutlined, QuestionCircleOutlined } from "@ant-design/icons";
import GridLayout, { type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import { deleteWidget, fetchDashboard, getRole, saveLayout } from "./api";
import type { WidgetDef } from "./types";
import { Chart } from "./Chart";

export function Dashboard({ onLogout, onDrillDown, onBackChat }: {
  onLogout: () => void;
  onDrillDown: (w: WidgetDef) => void;
  onBackChat: () => void;
}) {
  const { modal } = AntApp.useApp();
  const [widgets, setWidgets] = useState<WidgetDef[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const body = await fetchDashboard();
      setWidgets(body.widgets);
    } catch (e: any) {
      setErr(e?.message ?? String(e));
      if (String(e?.message ?? "").includes("会话失效")) onLogout();
    } finally {
      setLoading(false);
    }
  }, [onLogout]);

  useEffect(() => { load(); }, [load]);

  // FR-25：拖拽布局持久化（节流由 onDragStop 天然保证——拖完才触发）
  const onLayoutChange = (layout: Layout[]) => {
    if (!widgets.length || loading) return;
    const payloads = layout.map((l) => ({ id: l.i, x: l.x, y: l.y, w: l.w, h: l.h }));
    saveLayout(payloads).catch(() => { /* 布局失败不打断浏览，下次拖动重试 */ });
  };

  const remove = (w: WidgetDef) => {
    modal.confirm({
      title: `删除看板项「${w.title}」？`,
      content: "仅删除该口径卡片，不影响语义模型与数据。",
      okText: "删除", okButtonProps: { danger: true }, cancelText: "取消",
      onOk: async () => { await deleteWidget(w.id); await load(); },
    });
  };

  const cols = 12;
  const layout: Layout[] = widgets.map((w, i) => ({
    i: w.id, x: w.layout?.x ?? (i % 2) * 6, y: w.layout?.y ?? Math.floor(i / 2) * 5,
    w: w.layout?.w ?? 6, h: w.layout?.h ?? 5, minW: 3, minH: 3,
  }));

  return (
    <div className="chat-wrap dashboard-wrap">
      <div className="chat-header">
        <Space>
          <b>InsightLite · 看板</b>
          <span className="role-badge">{getRole()}</span>
          <Tooltip title="看板只存口径不存数据：每个卡片按你的权限重新执行——经理看到本部门、员工看到本人、越权口径显示拒绝">
            <QuestionCircleOutlined />
          </Tooltip>
        </Space>
        <Space>
          <Button size="small" onClick={load}>刷新</Button>
          <Button size="small" type="primary" ghost icon={<LineChartOutlined />} onClick={onBackChat}>
            回到对话
          </Button>
          <Button size="small" onClick={onLogout}>退出</Button>
        </Space>
      </div>

      <div className="dashboard-body">
        {loading && <div className="dash-center"><Spin tip="加载看板…" /></div>}
        {err && <Alert type="error" showIcon message={err} />}
        {!loading && !err && widgets.length === 0 && (
          <div className="dash-center">
            <Empty description="看板还是空的——在对话里对满意的结果点「加入看板」" />
          </div>
        )}
        {!loading && widgets.length > 0 && (
          <GridLayout className="layout" layout={layout} cols={cols} rowHeight={64}
            onLayoutChange={onLayoutChange} draggableHandle=".dash-title">
            {widgets.map((w) => {
              const r = w.result;
              return (
                <div key={w.id} className="dash-widget">
                  <Card size="small" title={
                    <span className="dash-title" title="点击回对话下钻">
                      <a onClick={() => onDrillDown(w)}>{w.title}</a>
                      {r?.decision && (
                        <Tag style={{ marginLeft: 8 }}
                          color={r.decision === "DENY" ? "red" : r.decision === "MASK" ? "orange" : "green"}>
                          {r.decision}
                        </Tag>
                      )}
                    </span>
                  } extra={
                    <Button type="text" size="small" danger icon={<DeleteOutlined />}
                      onClick={() => remove(w)} />
                  } styles={{ body: { padding: 8 } }}>
                    {r?.status === "answered" && r.data
                      ? <Chart data={r.data} chart={w.chart} height={w.layout.h * 64 - 110} />
                      : <Alert type={r?.status === "denied" ? "warning" : "error"} showIcon
                          message={r?.status === "denied" ? "无权查看该口径"
                            : r?.status === "clarify" ? "该口径需要澄清，未执行"
                            : r?.text ?? "加载失败"} />}
                  </Card>
                </div>
              );
            })}
          </GridLayout>
        )}
      </div>
    </div>
  );
}
