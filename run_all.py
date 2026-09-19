# -*- coding: utf-8 -*-
"""
InsightLite · 一键自检（承接 Sprint 0 run_all.py，2026-09-19 适配新目录布局）
用法: python run_all.py
  （自动先执行 deploy/seed/make_hr_data.py 生成 data/hr_demo.db，缺失时重建）
离线可跑：除 llm_intent 的 dry-run 外零 API 依赖；live 验证另跑 backend/core/e2e_live.py。
"""
import subprocess, sys, os

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

STEPS = [
    ("deploy/seed/make_hr_data.py",  "HR 种子数据生成（data/hr_demo.db）"),
    ("tests/test_dsl_v1.py",         "DSL v1 契约 + 权限矩阵 + 编译器单测"),
    ("backend/core/compiler.py",     "SMQ -> SQL 编译器自检"),
    ("backend/core/sso_mock.py",     "模拟 SSO fail-closed 验证"),
    ("backend/core/llm_intent.py",   "LLM 意图层 dry-run（无 Key 也可跑）"),
    ("backend/core/render.py",       "渲染层与 ask() 链路冒烟"),
    ("eval/eval_harness.py",         "eval harness 离线回放（口径一致率基线）"),
]


def run(script):
    print(f"\n>>> python {script}")
    r = subprocess.run([PY, os.path.join(ROOT, script)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    print(r.stdout)
    if r.returncode != 0:
        print(f"STDERR: {r.stderr}")
    return r.returncode == 0


if __name__ == "__main__":
    ok = True
    for script, label in STEPS:
        print(f"\n===== {label} =====")
        ok &= run(script)
    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED — see above"))
    sys.exit(0 if ok else 1)
