-- =====================================================================
-- ChatBI-Lite Sprint 0 · HR 数据域建表脚本（PostgreSQL 方言）
-- 对应 dev-plan.html 第 3 节「HR 数据域：5 张表与权限场景」
-- 用法（PostgreSQL）:  psql -d chatbi_demo -f 001_hr_schema_postgres.sql
-- 说明: scripts/make_hr_data.py 会生成等构的 SQLite 库（hr_demo.db）
--       供 Spike 本地执行验证；两份 DDL 字段完全一致。
-- 权限语义标记（注释级，供语义模型 YAML 引用）:
--   employee.salary        -> col:sensitive  （列级 MASK 主实验对象）
--   employee.dept_id       -> row:dept_scope （行级部门隔离作用域）
--   attendance/leave       -> row:dept_scope 递归到本人行
-- =====================================================================

CREATE TABLE department (
    id          INTEGER PRIMARY KEY,
    name        VARCHAR(64)  NOT NULL,
    parent_id   INTEGER REFERENCES department(id)   -- 树形组织架构，行级隔离按子树递归
);

CREATE TABLE employee (
    id          INTEGER PRIMARY KEY,
    name        VARCHAR(32)  NOT NULL,
    dept_id     INTEGER      NOT NULL REFERENCES department(id),
    level       VARCHAR(8)   NOT NULL,               -- 职级 P3~P9
    salary      INTEGER      NOT NULL,               -- col:sensitive：普通员工/部门经理 MASK，HRBP ALLOW
    hire_date   DATE         NOT NULL
);

CREATE TABLE attendance (
    emp_id      INTEGER      NOT NULL REFERENCES employee(id),
    work_date   DATE         NOT NULL,
    hours       NUMERIC(4,1) NOT NULL,               -- 当日工时
    late_min    INTEGER      NOT NULL DEFAULT 0,     -- 迟到分钟数（0 = 未迟到）
    early_leave BOOLEAN      NOT NULL DEFAULT FALSE,
    PRIMARY KEY (emp_id, work_date)
);

CREATE TABLE leave_balance (
    emp_id      INTEGER      NOT NULL REFERENCES employee(id),
    leave_type  VARCHAR(16)  NOT NULL,               -- 年假/事假/病假/调休
    quota       NUMERIC(5,1) NOT NULL,
    used        NUMERIC(5,1) NOT NULL,
    PRIMARY KEY (emp_id, leave_type)
);

CREATE TABLE audit_log (                            -- LOG_ONLY 审计（PRD FR-41，方案 B 不做 ENFORCE）
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    role        VARCHAR(16)  NOT NULL,               -- employee/manager/hrbp/exec
    smq_digest  TEXT         NOT NULL,               -- SMQ JSON 摘要（不含原始 NL，避免留敏感问题）
    decision    VARCHAR(8)   NOT NULL                -- ALLOW / MASK / DENY 三态
);

CREATE INDEX idx_att_date    ON attendance(work_date);
CREATE INDEX idx_emp_dept    ON employee(dept_id);
CREATE INDEX idx_audit_ts    ON audit_log(ts);
