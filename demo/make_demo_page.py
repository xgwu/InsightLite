# -*- coding: utf-8 -*-
"""
ChatBI-Lite W6 · M2 演示页生成器
读取 scripts/demo_transcript.json（demo_transcript.py 的真实全链路输出），
生成 chatbi-lite-site/demo.html（对话式演示页），并把「端到端演示」注入全站导航。
用 Python 写文件（UTF-8），规避 Windows shell 中文编码坑。
"""
import io, json, os, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))     # scripts -> sprint0 -> 工作区根
SITE = os.path.join(ROOT, "chatbi-lite-site")
TRANSCRIPT = os.path.join(HERE, "demo_transcript.json")

with io.open(TRANSCRIPT, encoding="utf-8") as f:
    data = json.load(f)

STATUS_META = {
    "answered":     ("已回答", "good"),
    "clarify":      ("澄清反问", "info"),
    "denied":       ("权限拦截", "risk"),
    "refused_scope":("越界拒答", "warn"),
}

items_js = json.dumps(data["items"], ensure_ascii=False)
meta_js = json.dumps({"date": data["date"], "model": data["model"]}, ensure_ascii=False)

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>端到端演示 — 轻量级 ChatBI</title>
<link rel="stylesheet" href="assets/site.css">
<style>
  /* demo 页无 pagenav 目录列：单列布局（否则 .main 落进 186px 首列） */
  .wrap { grid-template-columns: minmax(0, 1fr); }
  .chat { max-width: 860px; margin: 0 auto; }
  .bubble { border: 1px solid var(--line, #e3e6ec); border-radius: 12px; padding: 14px 18px; margin: 14px 0; background: var(--panel, #fff); }
  .bubble__q { border-left: 4px solid #2f6fed; background: #f4f7ff; }
  .bubble__a { border-left: 4px solid #c8ceda; }
  .bubble--denied { border-left-color: #d5455c; }
  .bubble--refused_scope { border-left-color: #d98a1f; }
  .bubble--clarify { border-left-color: #7a5af8; }
  .bubble__meta { font-size: 12px; color: #6b7280; margin-bottom: 6px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .bubble__a .mdtable { overflow-x: auto; }
  .bubble__a table { border-collapse: collapse; font-size: 13px; margin: 6px 0; width: 100%; }
  .bubble__a th, .bubble__a td { border-bottom: 1px solid #e7eaf0; padding: 6px 10px; text-align: left; white-space: nowrap; }
  .bubble__a th { background: #f5f7fb; }
  .bubble__a blockquote { margin: 8px 0; padding: 6px 12px; border-left: 3px solid #d98a1f; background: #fdf6e9; font-size: 12.8px; }
  .bubble__a p { margin: 4px 0; }
  details.smq { margin-top: 8px; font-size: 12px; }
  details.smq pre { background: #f6f8fa; padding: 8px 10px; border-radius: 8px; overflow-x: auto; }
</style>
</head>
<body>

<header class="sitebar">
  <div class="sitebar__inner">
    <a class="sitebar__brand" href="index.html"><b>轻量级 ChatBI</b><span>调研 · 描述书 · 原型</span></a>
    <nav class="sitebar__nav">
      <a href="index.html">摘要</a>
      <a href="market.html">市场格局</a>
      <a href="competitors.html">标杆竞品</a>
      <a href="emerging.html">新兴产品</a>
      <a href="tech.html">技术路线</a>
      <a href="security.html">权限安全</a>
      <a href="prd.html">功能描述书</a>
      <a href="prototype.html">交互原型</a>
      <a href="dev-plan.html">开发规划</a>
      <a href="dsl-v1.html">语义模型</a>
      <a href="demo.html"><b>端到端演示</b></a>
      <a href="sources.html">数据来源</a>
    </nav>
  </div>
</header>

<div class="wrap">
  <main class="main">
    <div class="eyebrow">ChatBI-Lite End-to-End Demo · W6 / M2 交付物 · 2026-09-17</div>
    <h1>端到端演示 — 真实对话转录</h1>
    <p class="lead">以下每一轮对话都是<strong>系统真实跑出来的</strong>，不是摆拍：自然语言 → DeepSeek 意图解析（只产 SMQ，禁 SQL）→ DSL 权限三态决策 → sqlglot 确定性编译 → SQLite 真实执行 → 中文渲染。数据为 HR 演示库（136 名员工、6 个月考勤/假期/薪资）。</p>

    <div class="kpi-row">
      <div class="kpi kpi--accent"><div class="kpi__value">10 <small>问</small></div><div class="kpi__label">演示问题覆盖全状态</div><div class="kpi__note">含 W6 新增环比能力</div></div>
      <div class="kpi kpi--good"><div class="kpi__value">6 <small>答出</small></div><div class="kpi__label">明细 / 聚合 / TopN / 环比</div><div class="kpi__note">全部真实执行出数</div></div>
      <div class="kpi"><div class="kpi__value">2 <small>反问</small></div><div class="kpi__label">歧义澄清（clarify）</div><div class="kpi__note">猜错代价高时反问，不瞎猜</div></div>
      <div class="kpi kpi--risk"><div class="kpi__value">2 <small>拦截</small></div><div class="kpi__label">越权 1 + 越界 1</div><div class="kpi__note">fail-closed：明确失败，绝不静默放行</div></div>
    </div>

    <div class="sec-head" id="s1">演示转录（__DATE__ 录制 · 模型 __MODEL__）</div>
    <div class="chat" id="chat"></div>

    <div class="sec-head" id="s2">六种状态图例</div>
    <div class="table-scroll">
      <table>
        <thead><tr><th style="width:18%">状态</th><th>含义</th><th style="width:34%">本次演示中的例子</th></tr></thead>
        <tbody>
          <tr><td><span class="badge badge--good">已回答</span></td><td>权限放行 → 编译执行 → 如实渲染；敏感列按角色 MASK</td><td>考勤明细、部门均工时、迟到 TopN、<b>实发工资环比</b>等 6 问</td></tr>
          <tr><td><span class="badge badge--info">澄清反问</span></td><td>歧义大、猜错代价高时反问用户（绝不带查询字段执行）</td><td>「出勤情况好吗」「对比 8 月和 9 月」</td></tr>
          <tr><td><span class="badge badge--risk">权限拦截</span></td><td>R1–R4 权限引擎显式 DENY（缺省 DENY）</td><td>普通员工指名查同事考勤（R4）</td></tr>
          <tr><td><span class="badge badge--warn">越界拒答</span></td><td>超出数据域 / 表达力（导出、股价等），设计内拒答</td><td>「导出成 Excel」</td></tr>
        </tbody>
      </table>
    </div>

    <div class="callout callout--key">
      <div class="callout__label">为什么这页能当 M2 里程碑</div>
      <p style="margin:6px 0 0">W6 前系统只会「单时间区间」查询——对比类问题只能澄清或拒绝。本轮把<b>日期算术从 LLM 收回编译器</b>（time_window 预设确定性展开）并新增 <b>compare_to 环比</b>（本期/上期两条平移 SQL + 结果侧合并，权限只决策一次），「各部门 8 月平均实发工资环比 7 月」成为系统真实能力。eval 口径一致率 21/21 = 100%（25 case 全过，fixtures 已录制可离线回放）。</p>
    </div>
  </main>
</div>

<footer class="foot">
  <div>轻量级 ChatBI · 端到端演示（W6 / M2） · 2026-09-17</div>
  <div><a href="dev-plan.html">← 开发规划</a></div>
</footer>

<script type="application/json" id="transcript">__ITEMS__</script>
<script type="application/json" id="meta">__META__</script>
<script>
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function renderMd(text){
  // 迷你 Markdown：表格 / 引用 / 段落（演示转录只需这三样）
  var lines = text.split('\\n'), out = [], i = 0;
  while (i < lines.length){
    var ln = lines[i];
    if (/^\\s*\\|/.test(ln)){
      var rows = [];
      while (i < lines.length && /^\\s*\\|/.test(lines[i])){ rows.push(lines[i]); i++; }
      var cells = function(r){ return r.replace(/^\\s*\\|/,'').replace(/\\|\\s*$/,'').split('|').map(function(c){return c.trim();}); };
      var h = cells(rows[0]);
      var html = '<table><thead><tr>' + h.map(function(c){return '<th>'+esc(c)+'</th>';}).join('') + '</tr></thead><tbody>';
      for (var r = 2; r < rows.length; r++){
        html += '<tr>' + cells(rows[r]).map(function(c){return '<td>'+esc(c)+'</td>';}).join('') + '</tr>';
      }
      out.push('<div class="mdtable">'+html+'</tbody></table></div>');
      continue;
    }
    if (/^\\s*&gt;/.test(ln) || /^\\s*>/.test(ln)){
      var buf = [];
      while (i < lines.length && /^\\s*&gt;|^\\s*>/.test(lines[i])){
        buf.push(lines[i].replace(/^\\s*&gt;\\s?|^\\s*>\\s?/,'')); i++;
      }
      out.push('<blockquote>'+esc(buf.join(' '))+'</blockquote>');
      continue;
    }
    if (ln.trim() === ''){ i++; continue; }
    out.push('<p>'+esc(ln)+'</p>'); i++;
  }
  return out.join('');
}
var items = JSON.parse(document.getElementById('transcript').textContent);
var meta = JSON.parse(document.getElementById('meta').textContent);
var metaMap = {answered:['good','已回答'], clarify:['info','澄清反问'], denied:['risk','权限拦截'], refused_scope:['warn','越界拒答']};
var chat = document.getElementById('chat');
document.querySelectorAll('.eyebrow')[0].textContent = 'ChatBI-Lite End-to-End Demo · W6 / M2 交付物 · ' + meta.date + ' 录制';
items.forEach(function(it){
  var m = metaMap[it.status] || ['mid', it.status];
  var q = document.createElement('div');
  q.className = 'bubble bubble__q';
  q.innerHTML = '<div class="bubble__meta"><span class="badge badge--mid">'+esc(it.intent)+'</span>'+
    '<span>提问角色：'+esc(it.role)+'</span></div><p style="margin:0"><b>'+esc(it.q)+'</b></p>';
  chat.appendChild(q);
  var a = document.createElement('div');
  a.className = 'bubble bubble__a bubble--'+it.status;
  var smq = it.smq ? '<details class="smq"><summary>查看系统内部：SMQ 语义查询（LLM 只产这个，永不产 SQL）</summary><pre>'+
      esc(JSON.stringify(it.smq, null, 1))+'</pre></details>' : '';
  a.innerHTML = '<div class="bubble__meta"><span class="badge badge--'+m[0]+'">'+m[1]+'</span></div>'+
    '<div class="bubble__a">'+renderMd(it.text)+'</div>' + smq;
  chat.appendChild(a);
});
</script>
</body>
</html>
"""

html = (HTML.replace("__ITEMS__", items_js)
            .replace("__META__", meta_js)
            .replace("__DATE__", data["date"])
            .replace("__MODEL__", data["model"]))

out_path = os.path.join(SITE, "demo.html")
with io.open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print(f"demo.html written: {out_path} ({len(html)} chars, {len(data['items'])} turns)")

# ---- 全站导航注入「端到端演示」（幂等：已有则跳过） ----
nav_link = '      <a href="demo.html"><b>端到端演示</b></a>\n'
count = 0
for fn in sorted(os.listdir(SITE)):
    if not fn.endswith(".html") or fn == "demo.html":
        continue
    p = os.path.join(SITE, fn)
    with io.open(p, encoding="utf-8") as f:
        src = f.read()
    if 'href="demo.html"' in src:
        continue
    if 'href="sources.html"' in src:
        src = src.replace('      <a href="sources.html">', nav_link + '      <a href="sources.html">', 1)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(src)
        count += 1
        print(f"nav injected: {fn}")
print(f"nav injected into {count} pages")
