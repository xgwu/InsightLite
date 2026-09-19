import { ConfigProvider, App as AntApp } from "antd";
import zhCN from "antd/locale/zh_CN";
import { useState } from "react";
import { Chat } from "./Chat";
import { Dashboard } from "./Dashboard";
import { Login } from "./Login";
import { clearSession, getToken } from "./api";
import type { WidgetDef } from "./types";

type Page = "chat" | "dashboard";

export default function App() {
  const [logged, setLogged] = useState<boolean>(!!getToken());
  const [page, setPage] = useState<Page>("chat");
  const [drill, setDrill] = useState<WidgetDef | null>(null);   // 看板 -> 对话下钻上下文

  const logout = () => { clearSession(); setPage("chat"); setLogged(false); };

  if (!logged) {
    return (
      <ConfigProvider locale={zhCN}>
        <AntApp><Login onLogin={() => setLogged(true)} /></AntApp>
      </ConfigProvider>
    );
  }
  return (
    <ConfigProvider locale={zhCN}>
      <AntApp>
        {page === "chat" ? (
          <Chat
            onLogout={logout}
            onGoDashboard={() => setPage("dashboard")}
            drillContext={drill}
            onConsumeDrill={() => setDrill(null)}
          />
        ) : (
          <Dashboard
            onLogout={logout}
            onDrillDown={(w) => { if (w.id) { setDrill(w); } setPage("chat"); }}
            onBackChat={() => setPage("chat")}
          />
        )}
      </AntApp>
    </ConfigProvider>
  );
}
