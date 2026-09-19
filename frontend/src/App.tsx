import { ConfigProvider, App as AntApp } from "antd";
import zhCN from "antd/locale/zh_CN";
import { useState } from "react";
import { Chat } from "./Chat";
import { Login } from "./Login";
import { clearSession, getToken } from "./api";

export default function App() {
  const [logged, setLogged] = useState<boolean>(!!getToken());
  return (
    <ConfigProvider locale={zhCN}>
      <AntApp>
        {logged
          ? <Chat onLogout={() => { clearSession(); setLogged(false); }} />
          : <Login onLogin={() => setLogged(true)} />}
      </AntApp>
    </ConfigProvider>
  );
}
