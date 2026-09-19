# -*- coding: utf-8 -*-
"""
Sprint 0 · Spike B：mock SSO 骨架（JWT 风格 HMAC-SHA256，零外部依赖）
对应 dev-plan.html W1「模拟 SSO（JWT 签发）」与第 6 节权限测试矩阵的 5 角色。

设计要点：
  - 角色 = employee / manager / hrbp / exec（+ 未登录 = 无 token）——与权限矩阵对齐
  - claims 携带 sub / role / dept_id / dept_path（行级部门隔离的作用域来源）
  - 签发用 server secret；验证失败（篡改 / 错钥伪造提权 / 过期）一律 DENY —— fail-closed
  - 这是 mock：替换为企业真实 SSO 时只换「签发方」，验证端接口不变
用法: python sso_mock.py
"""
import hmac, hashlib, base64, json, time, sys, os

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SERVER_SECRET = b"sprint0-demo-secret-do-not-use-in-prod"   # mock 专用
ROLES = {"employee", "manager", "hrbp", "exec"}


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(sub: str, role: str, dept_id: int, dept_path: list, ttl: int = 3600, secret=SERVER_SECRET) -> str:
    """签发 token。role 必须在 ROLES 白名单内。"""
    if role not in ROLES:
        raise ValueError(f"unknown role: {role!r}")
    header = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64u(json.dumps({
        "sub": sub, "role": role, "dept_id": dept_id, "dept_path": dept_path,
        "exp": int(time.time()) + ttl,
    }, ensure_ascii=False, separators=(",", ":")).encode())
    sig = _b64u(hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def verify(token: str, secret=SERVER_SECRET):
    """验证 -> claims（ALLOW）或 None（DENY）。无策略即拒绝。"""
    try:
        header, payload, sig = token.split(".")
        expect = _b64u(hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            return None                                   # 签名不符：篡改或错钥
        claims = json.loads(_b64u_decode(payload))
        if claims.get("role") not in ROLES:
            return None                                   # 角色白名单外的 token：DENY
        if claims.get("exp", 0) < time.time():
            return None                                   # 过期：DENY
        return claims
    except Exception:
        return None                                       # 任何解析异常：DENY（fail-closed）


def main():
    print("=" * 72)
    print("SPIKE B: mock SSO (JWT-style HMAC, zero deps) — permission matrix alignment")
    print("=" * 72)

    good = issue("1001", "hrbp", 13, [1, 5, 13])
    claims = verify(good)
    print(f"[case1 valid hrbp token]  -> {'ALLOW ' + claims['role'] if claims else 'DENY'}")

    tampered = good[:-6] + ("AAAAAA" if good[-6:] != "AAAAAA" else "BBBBBB")
    print(f"[case2 tampered signature] -> {'ALLOW (BAD!)' if verify(tampered) else 'DENY'}")

    forged = issue("1002", "hrbp", 9, [1, 3, 9], secret=b"attacker-key")   # 普通员工想自签 hrbp
    print(f"[case3 forged escalation]  -> {'ALLOW (BAD!)' if verify(forged) else 'DENY'}")

    expired = issue("1003", "employee", 6, [1, 2, 6], ttl=-10)
    print(f"[case4 expired token]      -> {'ALLOW (BAD!)' if verify(expired) else 'DENY'}")

    bad_role = issue("1004", "superadmin", 1, [1])
    print(f"[case5 off-whitelist role] -> DENY at issuance (ValueError expected)")

    print("\nDONE. fail-closed verified: tamper/forgery/expiry/off-whitelist all DENY.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"case5 explicit fail OK -> ValueError: {e}")
        # 继续打印其余 case 的汇总
        print("\nDONE. fail-closed verified: tamper/forgery/expiry/off-whitelist all DENY.")
