# -*- coding: utf-8 -*-
"""
Sprint 0 · HR 示例数据集生成器
生成 chatbi-lite-sprint0/data/hr_demo.db（SQLite，与 PG DDL 字段一致）
用法: python make_hr_data.py
产出统计打印到 stdout；数据用固定随机种子，可复现。
"""
import sqlite3, random, sys, datetime, os

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
random.seed(20260916)

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(BASE, "data", "hr_demo.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)

SURNAMES = "王李张刘陈杨黄赵周吴徐孙马朱胡郭何林罗郑"
GIVEN = ["伟","芳","娜","敏","静","强","磊","军","洋","勇","艳","杰","涛","明","超","霞","平","辉","健","欣","怡","晨","悦","楠","琪","轩","然","瑾"]

# ---- 部门树：1 root -> 4 一级 -> 各 2~3 二级 ----
DEPTS = [
    (1, "总公司", None),
    (2, "技术部", 1), (3, "产品部", 1), (4, "市场部", 1), (5, "人力资源部", 1),
    (6, "后端组", 2), (7, "前端组", 2), (8, "测试组", 2),
    (9, "产品一组", 3), (10, "产品二组", 3),
    (11, "品牌组", 4), (12, "渠道组", 4),
    (13, "HRBP 组", 5), (14, "招聘组", 5),
]

def make_name():
    return random.choice(SURNAMES) + random.choice(GIVEN)

conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.executescript("""
DROP TABLE IF EXISTS department;
DROP TABLE IF EXISTS employee;
DROP TABLE IF EXISTS attendance;
DROP TABLE IF EXISTS leave_balance;
DROP TABLE IF EXISTS payroll;
DROP TABLE IF EXISTS audit_log;
CREATE TABLE department (id INTEGER PRIMARY KEY, name TEXT NOT NULL, parent_id INTEGER);
CREATE TABLE employee (id INTEGER PRIMARY KEY, name TEXT NOT NULL, dept_id INTEGER NOT NULL,
                       level TEXT NOT NULL, salary INTEGER NOT NULL, hire_date TEXT NOT NULL);
CREATE TABLE attendance (emp_id INTEGER NOT NULL, work_date TEXT NOT NULL, hours REAL NOT NULL,
                         late_min INTEGER NOT NULL DEFAULT 0, early_leave INTEGER NOT NULL DEFAULT 0,
                         PRIMARY KEY (emp_id, work_date));
CREATE TABLE leave_balance (emp_id INTEGER NOT NULL, leave_type TEXT NOT NULL,
                            quota REAL NOT NULL, used REAL NOT NULL, PRIMARY KEY (emp_id, leave_type));
-- payroll：语义模型 DSL v1 新增（dsl-v1.html 6.5 / OQ1 已拍板执行 2026-09-16）
-- 全表 critical（除 emp_id/pay_month），字段与 semantic_model/hr_domain_v1.yaml 严格一致
CREATE TABLE payroll (emp_id INTEGER NOT NULL, pay_month TEXT NOT NULL,
                      base_salary REAL NOT NULL, bonus REAL NOT NULL, overtime_pay REAL NOT NULL,
                      insurance_housing REAL NOT NULL, tax REAL NOT NULL, net_pay REAL NOT NULL,
                      PRIMARY KEY (emp_id, pay_month));
CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, role TEXT NOT NULL,
                        smq_digest TEXT NOT NULL, decision TEXT NOT NULL, model_version TEXT);
""")

cur.executemany("INSERT INTO department VALUES (?,?,?)", DEPTS)

# ---- 员工：叶子部门铺人，约 120 人；首条为钉死的演示员工（Spike Q1 过滤对象）----
leaf_depts = [d[0] for d in DEPTS if d[0] >= 6]
employees = [
    (1001, "王伟", 6, "P5", 32000, "2021-03-15"),   # 后端组
]
eid = 1001   # 1001 已被钉死的演示员工占用，随机员工从 1002 起
for dept in leaf_depts:
    for _ in range(random.randint(12, 22)):
        eid += 1
        employees.append((
            eid, make_name(), dept,
            random.choice(["P4", "P5", "P5", "P6", "P6", "P7", "P8"]),
            random.randint(12, 55) * 1000,                       # salary：敏感列
            (datetime.date(2016, 1, 1) + datetime.timedelta(days=random.randint(0, 3600))).isoformat(),
        ))
cur.executemany("INSERT INTO employee VALUES (?,?,?,?,?,?)", employees)

# ---- 考勤：近 90 个自然日（仅工作日）----
today = datetime.date(2026, 9, 15)
rows = []
for emp, *_ in employees:
    d = today - datetime.timedelta(days=89)
    while d <= today:
        if d.weekday() < 5:
            late = random.choice([0, 0, 0, 0, 0, 0, 5, 10, 15, 30])
            rows.append((emp, d.isoformat(), round(random.uniform(7.2, 10.5), 1),
                         late, 1 if random.random() < 0.03 else 0))
        d += datetime.timedelta(days=1)
cur.executemany("INSERT INTO attendance VALUES (?,?,?,?,?)", rows)
att_count = len(rows)

# ---- 假期余额：每人 4 假别 ----
LEAVE = ["年假", "事假", "病假", "调休"]
lb = [(emp, lt, random.randint(5, 15) * 1.0, round(random.uniform(0, 8), 1))
      for emp, *_ in employees for lt in LEAVE]
cur.executemany("INSERT INTO leave_balance VALUES (?,?,?,?)", lb)

# ---- 审计日志：2 条样例，证明 LOG_ONLY 通路；model_version = C7 决策可追溯 ----
cur.executemany("INSERT INTO audit_log (ts, role, smq_digest, decision, model_version) VALUES (?,?,?,?,?)", [
    ("2026-09-15T10:00:00Z", "hrbp",    "sha256:demo-1", "ALLOW", "seed"),
    ("2026-09-15T10:05:00Z", "employee", "sha256:demo-2", "MASK", "seed"),
])

# ---- payroll：近 6 个完整月（2026-03 ~ 2026-08）× 全员 ----
# 派生逻辑与 DSL 语义一致：base ≈ salary 主成分，net = 应发 - 五险一金 - 个税。
# 全部随机量放在已有表之后，保证此前随机序列与既有演示数据完全不变。
PAY_MONTHS = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
pay = []
for emp, _name, _dept, _lvl, salary, _hire in employees:
    for m in PAY_MONTHS:
        base   = round(salary * random.uniform(0.68, 0.76), 2)
        bonus  = round(salary * random.uniform(0.06, 0.16), 2)
        ot     = round(salary / 174 * random.uniform(0, 12), 2)          # 0~12h 加班时薪
        ins    = round((base + bonus + ot) * 0.105, 2)                    # 个人五险一金近似 10.5%
        gross  = round(base + bonus + ot, 2)
        taxable = max(0.0, gross - ins - 5000)                            # 起征点 5000
        if taxable <= 3000:      rate, ded = 0.03, 0.0
        elif taxable <= 12000:   rate, ded = 0.10, 210.0
        elif taxable <= 25000:   rate, ded = 0.20, 1410.0
        elif taxable <= 35000:   rate, ded = 0.25, 2660.0
        else:                    rate, ded = 0.30, 4410.0
        tax = round(taxable * rate - ded, 2)
        net = round(gross - ins - tax, 2)
        pay.append((emp, m, base, bonus, ot, ins, tax, net))
cur.executemany("INSERT INTO payroll VALUES (?,?,?,?,?,?,?,?)", pay)

# ---- 2026-08 入职新员工（W4 Q6 数据修复）：e2e Q6「2026 年 8 月入职的员工有哪些？」----
# 独立随机源 rng，保证既有 133 人及全部演示数据的随机序列零变化（append-only 原则）。
rng = random.Random(20260801)
NEW_HIRES = [
    # (dept, level, salary, hire_date)  部门取叶子组，入职日覆盖上/中/下旬
    (6,  "P5", 28000, "2026-08-03"),
    (9,  "P4", 19000, "2026-08-17"),
    (14, "P6", 33000, "2026-08-28"),
]
hire_rows = []
for dept, lvl, sal, hd in NEW_HIRES:
    eid += 1
    hire_rows.append((eid, rng.choice(SURNAMES) + rng.choice(GIVEN), dept, lvl, sal, hd))
# 防止撞名导致 Q6 结果歧义（钉死主键锚定已兜底，但演示数据求直观）：重名直接加「新」
seen_names = {e[1] for e in employees}
fixed = []
for r in hire_rows:
    name = r[1]
    while name in seen_names:
        name = rng.choice(SURNAMES) + rng.choice(GIVEN)
    seen_names.add(name)
    fixed.append((r[0], name) + r[2:])
hire_rows = fixed
cur.executemany("INSERT INTO employee VALUES (?,?,?,?,?,?)", hire_rows)

# 新员工的考勤（入职日~today 工作日）/ 假期余额 / 8 月薪资：走 rng，与主序列无关
att2, lb2, pay2 = [], [], []
for emp, _name, _dept, _lvl, salary, hd in hire_rows:
    d = datetime.date.fromisoformat(hd)
    while d <= today:
        if d.weekday() < 5:
            late = rng.choice([0, 0, 0, 0, 0, 0, 5, 10, 15, 30])
            att2.append((emp, d.isoformat(), round(rng.uniform(7.2, 10.5), 1), late,
                         1 if rng.random() < 0.03 else 0))
        d += datetime.timedelta(days=1)
    lb2.extend((emp, lt, rng.randint(5, 15) * 1.0, round(rng.uniform(0, 3), 1)) for lt in LEAVE)
    m = "2026-08"                                    # 8 月入职 -> 仅 8 月有 payroll
    base  = round(salary * rng.uniform(0.68, 0.76), 2)
    bonus = round(salary * rng.uniform(0.06, 0.16), 2)
    ot    = round(salary / 174 * rng.uniform(0, 12), 2)
    ins   = round((base + bonus + ot) * 0.105, 2)
    gross = round(base + bonus + ot, 2)
    taxable = max(0.0, gross - ins - 5000)
    if taxable <= 3000:      rate, ded = 0.03, 0.0
    elif taxable <= 12000:   rate, ded = 0.10, 210.0
    elif taxable <= 25000:   rate, ded = 0.20, 1410.0
    elif taxable <= 35000:   rate, ded = 0.25, 2660.0
    else:                    rate, ded = 0.30, 4410.0
    tax = round(taxable * rate - ded, 2)
    pay2.append((emp, m, base, bonus, ot, ins, tax, round(gross - ins - tax, 2)))
cur.executemany("INSERT INTO attendance VALUES (?,?,?,?,?)", att2)
cur.executemany("INSERT INTO leave_balance VALUES (?,?,?,?)", lb2)
cur.executemany("INSERT INTO payroll VALUES (?,?,?,?,?,?,?,?)", pay2)

conn.commit()

emp_n = cur.execute("SELECT COUNT(*) FROM employee").fetchone()[0]
dept_n = cur.execute("SELECT COUNT(*) FROM department").fetchone()[0]
pay_n = cur.execute("SELECT COUNT(*) FROM payroll").fetchone()[0]
# 完整性自检：net_pay 必须 = 应发 - 五险一金 - 个税（容差 0.02）
bad = cur.execute("""SELECT COUNT(*) FROM payroll
                     WHERE ABS(net_pay - (base_salary + bonus + overtime_pay - insurance_housing - tax)) > 0.02""").fetchone()[0]
print(f"OK db={DB}")
print(f"departments={dept_n} employees={emp_n} (含 2026-08 新入职 {len(hire_rows)} 人) "
      f"attendance_rows={att_count + len(att2)} leave_rows={len(lb) + len(lb2)} "
      f"payroll_rows={pay_n} (months={len(PAY_MONTHS)}) audit=2 "
      f"net_pay_check={'PASS' if bad == 0 else f'FAIL({bad})'}")
conn.close()
