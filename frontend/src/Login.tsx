// 登录页：mock SSO 角色选择（Sprint 0 缝线；真实 SSO 上线后替换为跳转）
import { useState } from "react";
import { Alert, Button, Card, Select, Typography } from "antd";
import { devLogin } from "./api";

const ROLES: [string, string][] = [
  ["exec", "老板（exec）— 全量数据"],
  ["hrbp", "HR（hrbp）— 全量数据"],
  ["manager", "部门经理（manager）— 本部门，薪酬列 MASK"],
  ["employee", "员工（employee）— 仅本人数据"],
  ["auditor", "审计（auditor）— 仅 public（部门架构）"],
];

export function Login({ onLogin }: { onLogin: () => void }) {
  const [role, setRole] = useState<string>("hrbp");
  const [err, setErr] = useState<string>("");

  const go = async () => {
    setErr("");
    try {
      await devLogin(role);
      onLogin();
    } catch (e: any) {
      setErr(e?.message ?? String(e));
    }
  };

  return (
    <div className="login-wrap">
      <Card title="InsightLite · 轻量级 ChatBI" style={{ width: 420 }}>
        <Typography.Paragraph type="secondary">
          选择演示身份登录（mock SSO）。不同角色看到不同的数据边界——这是权限策略引擎的行为演示。
        </Typography.Paragraph>
        <Select value={role} onChange={setRole} style={{ width: "100%" }}
          options={ROLES.map(([v, label]) => ({ value: v, label }))} />
        {err && <Alert style={{ marginTop: 12 }} type="error" showIcon message={err} />}
        <Button type="primary" block style={{ marginTop: 16 }} onClick={go}>进入</Button>
      </Card>
    </div>
  );
}
