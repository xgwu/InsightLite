# -*- coding: utf-8 -*-
"""
ChatBI-Lite W4 · DSL 加载器（dev-plan.html C1 / C6 / C7）
把 semantic_model/hr_domain_v1.yaml 编译为内存注册表，四方共用：
  smq_schema（校验）/ compiler（编译）/ perm_engine（权限）/ LLM 提示词。

设计约定（dsl-v1.html 第 2 节）：
  - 启动时加载、不支持热更（OQ5 已拍板）；model_version = 版本号 + 文件内容 sha8（C7）。
  - 加载即自检：未知键、缺必填、类型不合法 → 直接抛 DslError，服务拒绝启动（fail-closed 从模型层开始）。
  - 物理别名保持 SMQ v0 兼容：t=数据集本表 / e=employee / d=department（compiler 回归基准依赖）。
"""
import hashlib
import io
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
YAML_PATH = os.path.join(os.path.dirname(os.path.dirname(HERE)), "semantic_model", "hr_domain_v1.yaml")

SENSITIVITY_LEVELS = ("public", "internal", "sensitive", "critical")
KINDS = ("dimension", "measure", "attribute")
AGG_NAMES = ("sum", "avg", "count", "min", "max")
REQUIRED_FIELD_KEYS = {"display_name", "expr", "type", "kind"}
KNOWN_FIELD_KEYS = REQUIRED_FIELD_KEYS | {
    "aggregable", "allowed_aggs", "sensitivity", "mask", "synonyms", "description", "grain",
}
KNOWN_TOP_KEYS = {
    "version", "model", "roles", "entities", "datasets",
    "virtual_columns", "inherit_blocks", "time_semantics", "permission_matrix",
}
KNOWN_DATASET_KEYS = {"table", "inherits", "pk_composite", "fields"}


class DslError(Exception):
    """DSL 文件不合法：加载期显式失败，拒绝启动。"""


def _self_check(m):
    unknown_top = set(m) - KNOWN_TOP_KEYS - {"_model_version"}
    if unknown_top:
        raise DslError(f"unknown top-level keys: {sorted(unknown_top)}")
    if m.get("version") != 1:
        raise DslError(f"unsupported dsl version: {m.get('version')!r}")
    for ds, spec in m.get("datasets", {}).items():
        unknown_ds = set(spec) - KNOWN_DATASET_KEYS
        if unknown_ds:
            raise DslError(f"dataset {ds!r}: unknown keys {sorted(unknown_ds)}")
        fields = spec.get("fields") or {}
        if not fields:
            raise DslError(f"dataset {ds!r}: fields must be non-empty")
        for fname, fm in fields.items():
            missing = REQUIRED_FIELD_KEYS - set(fm)
            if missing:
                raise DslError(f"{ds}.{fname}: missing required keys {sorted(missing)}")
            unknown = set(fm) - KNOWN_FIELD_KEYS
            if unknown:
                raise DslError(f"{ds}.{fname}: unknown keys {sorted(unknown)}")
            if fm["kind"] not in KINDS:
                raise DslError(f"{ds}.{fname}: bad kind {fm['kind']!r}")
            aggs = fm.get("allowed_aggs") or []
            if aggs and not set(aggs) <= set(AGG_NAMES):
                raise DslError(f"{ds}.{fname}: bad allowed_aggs {aggs}")
            if fm["kind"] == "measure" and (not fm.get("aggregable") or not aggs):
                raise DslError(f"{ds}.{fname}: measure must be aggregable with allowed_aggs")
            if fm["kind"] == "attribute" and fm.get("aggregable"):
                raise DslError(f"{ds}.{fname}: attribute must not be aggregable")
            sens = fm.get("sensitivity")
            if sens not in SENSITIVITY_LEVELS:
                raise DslError(f"{ds}.{fname}: bad sensitivity {sens!r}")
            mask = fm.get("mask")
            if mask is not None and mask not in ("none", "null", "hash", "range"):
                raise DslError(f"{ds}.{fname}: bad mask rule {mask!r}")
            if sens in ("sensitive", "critical") and mask not in ("null", "hash", "range"):
                raise DslError(f"{ds}.{fname}: {sens} field requires mask rule")
    for vc, spec in m.get("virtual_columns", {}).items():
        if "expr" not in spec or "via" not in spec:
            raise DslError(f"virtual_columns.{vc}: requires expr + via")


class Registry:
    """不可变注册表：一次构建，只读共享。"""

    def __init__(self, model):
        self.model = model
        self.version = model["_model_version"]
        self.model_name = model["model"]["name"]
        self.dialect = model["model"].get("dialect", "sqlite")
        self.roles = dict(model["roles"])
        self.entities = dict(model["entities"])
        self.time_semantics = dict(model["time_semantics"])
        self.permission_matrix = dict(model["permission_matrix"])

        self.own = {}        # ds -> {field: meta}  本表物理字段（别名 t）
        self.inherited = {}  # ds -> {field: meta}  经 employee 维度继承（别名 e；dept_name 为 d）
        self.tables = {}     # ds -> set(全部可用字段)
        self.dataset_table = {}
        self.join_paths = {}  # ds -> [(table, alias, on)]，v0 兼容字符串

        inherit_blocks = model.get("inherit_blocks", {})
        virtual = model.get("virtual_columns", {})

        for ds, spec in model["datasets"].items():
            self.dataset_table[ds] = spec["table"]
            self.own[ds] = {k: dict(v) for k, v in (spec.get("fields") or {}).items()}
            inh = {}
            for blk in spec.get("inherits") or []:
                src_ds, flist = inherit_blocks[blk]["from"], inherit_blocks[blk]["fields"]
                for f in flist:
                    if f in virtual:
                        inh[f] = {"_virtual": True, "expr": virtual[f]["expr"], "via": virtual[f]["via"],
                                  "kind": virtual[f].get("kind", "dimension"), "type": virtual[f].get("type", "string"),
                                  "display_name": virtual[f]["display_name"], "synonyms": virtual[f].get("synonyms", [])}
                    else:
                        src = model["datasets"][src_ds]["fields"][f]
                        inh[f] = dict(src)
            self.inherited[ds] = inh
            self.tables[ds] = set(self.own[ds]) | set(inh)

        # join 路径：星型事实表 -> employee(e) -> department(d)；employee 自身 -> department(d)
        # 注意：employee 也继承 employee_dims（为获得 dept_name 虚拟列），但它不是事实表——
        # 必须排除，否则会被套上 t.emp_id = e.id 的错误 join（employee 主键是 id）。
        fact_ds = [ds for ds in model["datasets"] if ds != "employee" and any(
            b in (model["datasets"][ds].get("inherits") or []) and inherit_blocks[b]["from"] == "employee"
            for b in (model["datasets"][ds].get("inherits") or []))]
        for ds in model["datasets"]:
            if ds in fact_ds:
                self.join_paths[ds] = [
                    ("employee", "e", "t.emp_id = e.id"),
                    ("department", "d", "e.dept_id = d.id"),
                ]
            elif ds == "employee":
                self.join_paths[ds] = [("department", "d", "t.dept_id = d.id")]
            else:
                self.join_paths[ds] = []

        # department 表自身没有 d 别名——dept_name 虚拟列在 department 数据集不可用（v0 隐患显式化）
        self.virtual_alias = {"dept_name": "d"}
        # 表名 -> 别名映射（虚拟列 expr 用全表名书写，编译期改写为 join 别名）
        self.table_alias = {}
        for ds, paths in self.join_paths.items():
            self.table_alias[ds] = {tbl: alias for tbl, alias, _ in paths}

    # ---- 别名解析（与 SMQ v0 _alias 语义逐字兼容） ----
    def alias(self, ds, field):
        if field in self.virtual_alias:
            return self.virtual_alias[field]
        if field in self.own[ds]:
            return "t"
        if field in self.inherited[ds]:
            return "e"
        raise KeyError(field)

    def colref(self, ds, field):
        """字段 -> 物理列引用。virtual 列解析为真实物理列（表名改写为 join 别名）。"""
        if field in self.inherited[ds] and self.inherited[ds][field].get("_virtual"):
            expr = self.inherited[ds][field]["expr"]          # 如 department.name
            for tbl, alias in self.table_alias.get(ds, {}).items():
                expr = expr.replace(f"{tbl}.", f"{alias}.")   # -> d.name
            return expr
        return f'{self.alias(ds, field)}."{field}"'

    def group_exprs(self, ds, field):
        """group_by 展开：attribute 字段锚定实体主键（防「同名不同人」口径错误）。
        返回表达式列表；普通字段返回单元素。"""
        expr = self.colref(ds, field)
        fm = self.inherited[ds].get(field) or self.own[ds].get(field)
        if fm and fm.get("kind") == "attribute":
            entity = "employee" if self.alias(ds, field) == "e" or (field in self.own[ds] and ds == "employee") else ds
            pk = self.entities[entity]["pk"]
            pk_alias = self.alias(ds, field)
            pk_expr = f'{pk_alias}."{pk}"'
            return [pk_expr, expr] if pk_expr != expr else [expr]
        return [expr]

    def date_fields(self, ds):
        return [f for f, m in self.own[ds].items() if m.get("type") in ("date", "month")]

    # ---- C6：LLM 提示词自动拼装 ----
    def build_system_prompt(self):
        lines = []
        lines.append("你是 ChatBI 的意图解析器。把用户的自然语言问题转换为 SMQ 查询对象（JSON），绝不输出 SQL。")
        lines.append("")
        lines.append("SMQ schema：")
        lines.append('{')
        lines.append('  "dataset": "' + " | ".join(self.tables) + '",')
        lines.append('  "columns": ["字段名" 或 {"field": "字段名", "agg": "sum|avg|count|min|max|null"}（非聚合列可省略 agg 或写 null）],')
        lines.append('  "filter":  [{"field": "...", "op": "eq|ne|gt|ge|lt|le|in", "value": ...}],')
        lines.append('  "time_window": {"field": "日期字段", "preset": "this_month|last_month|last_3_months"}（可选，与该字段的 filter 互斥）,')
        lines.append('  "compare_to": {"months_back": 1-12}（可选，环比；必须搭配 time_window）,')
        lines.append('  "group_by": ["字段名"],')
        lines.append('  "order_by": [{"field": "...", "agg": 可选, "dir": "asc|desc"}],')
        lines.append('  "limit": 整数（可选）')
        lines.append("}")
        lines.append("")
        lines.append("可用数据集与字段（字段名逐字匹配，含中文注释）：")
        label = {"attendance": "考勤", "leave_balance": "假期余额", "employee": "员工",
                 "department": "部门", "payroll": "历史薪资发放"}
        for ds in self.tables:
            items = []
            for f, m in self.own[ds].items():
                note = m["display_name"]
                if m["kind"] == "measure":
                    note += "/" + "|".join(m.get("allowed_aggs") or [])
                items.append(f"{f}({note})")
            for f, m in self.inherited[ds].items():
                items.append(f"{f}({m.get('display_name', f)}" + ("，继承列" if not m.get("_virtual") else "，虚拟列") + ")")
            lines.append(f"- {ds}（{label.get(ds, ds)}）: " + ", ".join(items))
        lines.append("")
        lines.append("硬性规则：")
        # 相对时间锚点必须运行时注入（W4 eval Q8 实证：不注入时模型把「上个月」解析成任意月份
        # 或直接丢弃时间过滤——相对时间解析是服务运行时职责，不是模型的常识）
        import datetime as _dt
        _today = _dt.date.today()
        _this = _today.strftime("%Y-%m")
        _last = (_today.replace(day=1) - _dt.timedelta(days=1)).strftime("%Y-%m")
        lines.append(f'0. 今天的日期是 {_today.isoformat()}（本月 = {_this}，上个月 = {_last}）。'
                     '一切相对时间（上个月/本月/最近一周）必须以这个锚点解析为绝对日期区间写进 filter，'
                     '缺时间过滤的查询一律错误。')
        lines.append("1. 只输出一个 JSON 对象，不要任何解释、markdown 或 SQL。")
        lines.append('2. 日期过滤用 ge/lt 闭开区间（如 8 月 = ge "2026-08-01" 且 lt "2026-09-01"）。')
        lines.append('3. 问题超出可用字段/数据集范围时，输出 {"error": "OUT_OF_SCOPE", "reason": "..."}。')
        lines.append('4. 「导出/下载 Excel、发邮件、生成报表文件」等动作类请求不是查询，必须 OUT_OF_SCOPE'
                     '（可查询数据本身，但导出动作不在语义查询表达力内）。')
        lines.append('5. 「迟到/早退几次」= 过滤 late_min/early_leave > 0 后对 emp_id 做 count（不是 count(late_min)，'
                     '那会数成出勤天数）；「迟到总分钟数」用 sum(late_min)；「工时」用 hours；「假期」用 leave_balance 数据集。')
        lines.append('6. 「月薪/工资」当前时点用 employee.salary；历史逐月薪资用 payroll 数据集（pay_month 格式 YYYY-MM）。')
        lines.append('7. 相对时间（上个月/本月/最近一周）必须解析为绝对日期区间写进 filter（如「上个月」='
                     'ge "2026-08-01" 且 lt "2026-09-01"），绝不能省略时间过滤。')
        lines.append("8. 按员工分组聚合时 group_by 直接写 name（编译器自动锚定工号，无需拆 id）。")
        lines.append("9. group_by 只能放维度字段（部门、职级、假别、月份、姓名等）；工时/迟到/薪资/额度等"
                     "度量字段只能出现在 columns 的 agg 聚合里，绝不能进 group_by。")
        lines.append('10. 歧义澄清：问题存在多种合理解读、且贸然缺省可能给出误导性答案时（如指标本身不明、'
                     '实体或时间范围有多种理解且影响结论），输出 {"clarify": true, "question": "向用户的澄清问题", '
                     '"options": ["可选答案1", ...]}，此时不带任何查询字段。'
                     '能用合理缺省的不要滥用澄清（如「查工资」默认当前工资、「考勤」默认按天明细）；'
                     'clarify 的判断标准是「猜错的代价高」，不是「信息不够全」。')
        lines.append('11. 提到部门一律用 dept_name 字段过滤/分组（如 {"field":"dept_name","op":"eq","value":"研发部"}），'
                     '绝不要用 dept_id——你没有 id 与部门名的映射，填不出正确的 id 值。')
        lines.append('12. 跨实体对比类问题（两个部门/两个人/两个假别并排对比）当前查询能力不支持，'
                     '必须 clarify 说明或 OUT_OF_SCOPE，绝不能把两个实体塞进一个查询冒充对比。')
        lines.append('13. 相对时间（本月/上个月/近三个月）优先用 time_window 预设：'
                     '{"field":"<日期字段>","preset":"this_month|last_month|last_3_months"}，'
                     '不要自己计算日期；其他绝对时间仍用 filter ge/lt。time_window 与同一字段的 filter 不能同时出现。')
        lines.append('14. 环比/同环比类问题（「较上月变化」「环比增长」「X月环比Y月」）用 time_window + compare_to：'
                     '{"months_back": N}，N = 对比的期数距离（较上月 = 1，同比 = 12）。'
                     '只要出现 compare_to，时间约束就必须写 time_window 预设，绝不能用 filter 日期'
                     '（「8月环比7月」= time_window 8月的预设 + months_back 1，不要写 filter）。'
                     'compare_to 只能搭配 time_window 预设，且查询必须含聚合（group_by 或 agg 列）。')
        lines.append("")
        lines.append("示例：")
        lines.append("问：8 月迟到总分钟数最多的 10 名员工？")
        lines.append('答：{"dataset":"attendance","columns":["name",{"field":"late_min","agg":"sum"}],')
        lines.append('    "filter":[{"field":"work_date","op":"ge","value":"2026-08-01"},')
        lines.append('              {"field":"work_date","op":"lt","value":"2026-09-01"}],')
        lines.append('    "group_by":["name"],"order_by":[{"field":"late_min","agg":"sum","dir":"desc"}],"limit":10}')
        lines.append("问：8 月请年假的员工，他们的假期余额还剩多少？")
        lines.append('答：{"dataset":"leave_balance","columns":["name","leave_type","quota","used"],')
        lines.append('    "filter":[{"field":"leave_type","op":"eq","value":"年假"}]}')
        lines.append("（注意：这类「某类人的余额/数值是多少」是明细查询，不需要 group_by——"
                     "leave_balance 每人每假别一行，quota/used 是数值本身，绝不能进 group_by）")
        lines.append("问：李娜 8 月迟到几次？")
        lines.append('答：{"dataset":"attendance","columns":["name",{"field":"emp_id","agg":"count"}],')
        lines.append('    "filter":[{"field":"name","op":"eq","value":"李娜"},')
        lines.append('              {"field":"late_min","op":"gt","value":0},')
        lines.append('              {"field":"work_date","op":"ge","value":"2026-08-01"},')
        lines.append('              {"field":"work_date","op":"lt","value":"2026-09-01"}],')
        lines.append('    "group_by":["name"]}')
        lines.append("（注意：「几次」= 计数迟到发生的天数，即 late_min>0 的行数，用 count(emp_id) 实现）")
        lines.append(f"问：上个月谁老迟到？（以规则 0 的日期锚点，上个月 = {_last}）")
        lines.append('答：{"dataset":"attendance","columns":["name",{"field":"late_min","agg":"sum"}],')
        lines.append('    "filter":[{"field":"late_min","op":"gt","value":0},')
        lines.append('              {"field":"work_date","op":"ge","value":"2026-08-01"},')
        lines.append('              {"field":"work_date","op":"lt","value":"2026-09-01"}],')
        lines.append('    "group_by":["name"],"order_by":[{"field":"late_min","agg":"sum","dir":"desc"}]}')
        lines.append("（注意：相对时间「上个月」必须换成绝对区间过滤，哪怕问题里没有明确日期；"
                     "迟到字面量过滤 late_min>0 与时间区间过滤要同时存在）")
        lines.append("问：各部门上个月平均工时，环比上上个月变化多少？")
        lines.append('答：{"dataset":"attendance","columns":["dept_name",{"field":"hours","agg":"avg"}],')
        lines.append('    "time_window":{"field":"work_date","preset":"last_month"},')
        lines.append('    "compare_to":{"months_back":1},"group_by":["dept_name"],')
        lines.append('    "order_by":[{"field":"hours","agg":"avg","dir":"desc"}]}')
        lines.append("（注意：环比 = time_window 预设 + compare_to；上个月相对今天 = last_month，较上期 = months_back 1；"
                     "两个时段的差值由系统计算，SMQ 只描述本期口径）")
        return "\n".join(lines)


def load():
    with io.open(YAML_PATH, encoding="utf-8") as f:
        raw = f.read()
    model = yaml.safe_load(raw)
    if not isinstance(model, dict):
        raise DslError("DSL root must be a mapping")
    _self_check(model)
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    model["_model_version"] = f"dsl-v{model.get('version', '?')}@{sha}"
    return model


MODEL = load()
REG = Registry(MODEL)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r = REG
    print(f"OK model={r.model_name} version={r.version} dialect={r.dialect}")
    for ds in r.tables:
        print(f"  {ds} ({r.dataset_table[ds]}): own={sorted(r.own[ds])} inherited={sorted(r.inherited[ds])}")
    print(f"roles={sorted(r.roles)}")
    print(f"prompt chars={len(r.build_system_prompt())}")
