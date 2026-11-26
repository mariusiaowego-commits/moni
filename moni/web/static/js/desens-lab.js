/* 脱敏实验室（v1.0.139）
 *
 * 内联在「配置管理 → 脱敏」页签里，不再单独跳页。与主应用刻意解耦：
 * 这里只用 DOM + fetch，不碰 Alpine 状态；首次切到脱敏页签时才初始化，
 * 避免给其它页面加一次 stats 请求。
 */
(function () {
  const DEFAULT_TERMS = "招商银行, 兴业银行";
  const SAMPLE = [
    "招商银行与兴业银行同日发布公告：招商银行客服电话 13800138000，",
    "联系人邮箱 analyst@bankcorp.cn。",
    "请只复述上述联系方式，不要展开分析。",
  ].join("\n");

  const SQL_TEXT =
    "SELECT count(*) AS calls, sum(blocked) AS blocked_calls,\n"
    + "       sum(masked_total) AS masked_total, sum(restored_total) AS restored_total,\n"
    + "       sum(unrestored_count) AS unrestored_count, sum(leak_scan_hits) AS leak_scan_hits,\n"
    + "       sum(residual_placeholders) AS residual_placeholders,\n"
    + "       round(100.0 * sum(restored_total)\n"
    + "             / nullif(sum(restored_total) + sum(unrestored_count), 0), 1) AS restore_success_pct\n"
    + "FROM desensitize_events WHERE call_site != 'desens_lab';";

  const POLICY_LABEL = { standard: "标准策略", web_search_safe: "联网核查策略" };
  const BLOCK_LABEL = { policy_block: "命中拦截规则", leak_scan_hit: "出站泄漏扫描命中" };
  const FALLBACK_LABEL = {
    mask_error: "遮蔽异常，已原样放行",
    mask_error_scrub_pass: "遮蔽异常，仅擦除凭据后放行",
    restore_error: "还原异常，已展示模型原包",
  };
  const CALL_SITE_LABEL = {
    summary_gen: "摘要生成",
    worker_llm: "分享简报",
    reader: "文章阅读",
    stream: "流式对话",
    runner: "通用调用",
    desens_lab: "实验室",
  };
  const TASK_TYPE_LABEL = { standard: "标准策略", fact_check_plus: "联网核查" };
  const label = (map, value, fallback) => map[value] || fallback || value || "—";
  const $ = (id) => document.getElementById(id);
  let started = false;

  function setStatus(text) {
    const el = $("lab-status");
    el.textContent = text || "";
    el.title = text ? "" : "运行结束后显示本次调用的策略与耗时";
  }

  function setErr(text) {
    $("lab-err").textContent = text || "";
  }

  function extraTerms() {
    return $("lab-terms").value.split(/[,，、\s]+/).map((t) => t.trim()).filter(Boolean);
  }

  function metric(k, v, cls) {
    return '<div class="metric ' + (cls || "") + '"><div class="k">' + k
      + '</div><div class="v">' + v + "</div></div>";
  }

  // 空态：模型实测才会产出还原/泄漏指标，本地预览只证明遮蔽，别写成「待运行」。
  function renderMetrics(s) {
    const el = $("lab-metrics");
    if (!s) {
      el.innerHTML = '<span class="sub">模型实测后显示本次指标</span>';
      return;
    }
    const leak = s.leak_scan_hits;
    const unrest = s.unrestored_count;
    const resid = s.residual_placeholders;
    el.innerHTML = [
      metric("遮蔽数", s.masked_total),
      metric("还原数", s.restored_total, "good"),
      metric("未还原", unrest, unrest === 0 ? "good" : "bad"),
      metric("出站泄漏命中", leak, leak === 0 ? "good" : "bad"),
      metric("残留占位符", resid, resid === 0 ? "good" : "bad"),
      metric("遮蔽耗时", s.mask_latency_us + " µs"),
      metric("还原耗时", s.restore_latency_us + " µs"),
      metric("模型耗时", s.model_duration_ms + " ms"),
    ].join("");
  }

  function renderPreviewMetrics(d) {
    $("lab-metrics").innerHTML = [
      metric("遮蔽数", d.masked_total),
      metric("遮蔽耗时", d.latency_us + " µs"),
      '<span class="sub">还原与泄漏指标需模型实测</span>',
    ].join("");
  }

  async function loadStats() {
    const res = await fetch("/api/desensitize/stats", { credentials: "same-origin" });
    if (!res.ok) throw new Error("stats " + res.status);
    const d = await res.json();
    const t = d.totals;
    const lab = d.lab_totals;
    $("lab-ledger-state").textContent = "账本 生产 " + t.calls + " · 实验室 " + lab.calls;
    $("lab-ledger").innerHTML =
      "<table><thead><tr><th>统计口径</th><th class='num'>调用数</th><th class='num'>遮蔽数</th>"
      + "<th class='num'>还原数</th><th class='num'>未还原</th><th class='num'>出站泄漏命中</th>"
      + "<th class='num'>残留占位符</th><th class='num'>拦截数</th><th class='num'>还原成功率</th></tr></thead><tbody>"
      + row("生产（不含实验室）", t) + row("实验室", lab)
      + "</tbody></table>";
    renderRecent(d.recent || []);
    $("lab-sql").textContent = SQL_TEXT;
  }

  function row(name, t) {
    const rate = t.restore_success_rate;
    return "<tr><td>" + name + "</td><td class='num'>" + t.calls + "</td><td class='num'>"
      + t.masked_total + "</td><td class='num'>" + t.restored_total + "</td><td class='num'>"
      + t.unrestored_count + "</td><td class='num'>" + t.leak_scan_hits + "</td><td class='num'>"
      + t.residual_placeholders + "</td><td class='num'>" + (t.blocked_calls || 0)
      + "</td><td class='num'>"
      + (rate === null || rate === undefined ? "—" : (rate * 100).toFixed(1) + "%")
      + "</td></tr>";
  }

  // 服务端字段一律走 textContent，只有本文件的固定文案进 innerHTML。
  function renderRecent(recent) {
    if (!recent.length) return;
    const wrap = document.createElement("div");
    wrap.className = "recent";
    const title = document.createElement("h3");
    title.textContent = "近期记录";
    wrap.appendChild(title);
    const table = document.createElement("table");
    table.innerHTML = "<thead><tr><th>调用点</th><th>任务类型</th>"
      + "<th class='num'>遮蔽</th><th class='num'>还原</th><th class='num'>未还原</th>"
      + "<th class='num'>出站泄漏命中</th><th>状态</th><th>时间</th></tr></thead>";
    const tbody = document.createElement("tbody");
    for (const r of recent) {
      const tr = document.createElement("tr");
      for (const [value, map] of [[r.call_site, CALL_SITE_LABEL], [r.task_type, TASK_TYPE_LABEL]]) {
        const td = document.createElement("td");
        td.textContent = label(map, value, value || "");
        tr.appendChild(td);
      }
      for (const v of [r.masked_total, r.restored_total, r.unrestored_count, r.leak_scan_hits]) {
        const td = document.createElement("td");
        td.className = "num";
        td.textContent = String(v === null || v === undefined ? 0 : v);
        tr.appendChild(td);
      }
      const tdOutcome = document.createElement("td");
      tdOutcome.textContent = r.blocked
        ? "已拦截"
        : (r.fallback_used ? label(FALLBACK_LABEL, r.fallback_used, "异常放行") : "正常");
      tr.appendChild(tdOutcome);
      const tdTime = document.createElement("td");
      tdTime.textContent = r.created_at || "";
      tr.appendChild(tdTime);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    $("lab-ledger").appendChild(wrap);
  }

  async function preview() {
    setErr("");
    setStatus("本地预览中…");
    const res = await fetch("/api/desensitize/preview", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: $("lab-input").value,
        task_type: $("lab-task-type").value,
        extra_terms: extraTerms(),
      }),
    });
    const d = await res.json();
    if (!res.ok) {
      setErr(d.message || d.detail || "预览失败");
      setStatus("");
      return;
    }
    $("lab-c1").textContent = $("lab-input").value;
    $("lab-c2").textContent = d.masked_text;
    $("lab-c3").textContent = "—";
    $("lab-c4").textContent = "—";
    renderPreviewMetrics(d);
    if (d.blocked) {
      setErr("已拦截：" + label(BLOCK_LABEL, d.blocked_reason, "命中拦截规则"));
      $("lab-err").title = "命中拦截规则或出站泄漏扫描；即使点模型实测也不会发往大模型";
      setStatus("已拦截 · 遮蔽 " + d.masked_total + " 处");
      return;
    }
    setStatus("遮蔽 " + d.masked_total + " 处 · " + label(POLICY_LABEL, d.policy_mode, d.policy_mode)
      + " · " + d.latency_us + " µs");
  }

  async function run() {
    setErr("");
    setStatus("模型调用中…");
    $("lab-status").title = "将已遮蔽的内容发往大模型并在本机还原，通常 5–15 秒";
    $("lab-run").disabled = true;
    $("lab-preview").disabled = true;
    try {
      const res = await fetch("/api/desensitize/run", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: $("lab-input").value,
          task_type: $("lab-task-type").value,
          extra_terms: extraTerms(),
        }),
      });
      const d = await res.json();
      if (!res.ok) {
        setErr(d.message || d.detail || "调用失败");
        setStatus("");
        return;
      }
      $("lab-c1").textContent = d.original;
      $("lab-c2").textContent = d.masked;
      $("lab-c3").textContent = d.reply_raw;
      $("lab-c4").textContent = d.restored;
      renderMetrics(d.stats);
      setStatus("完成 · " + label(POLICY_LABEL, d.policy_mode, d.policy_mode)
        + (d.fallback_used ? " · " + label(FALLBACK_LABEL, d.fallback_used, "异常放行") : ""));
      // 账本刷新失败不能把「完成」态抹掉：单独兜一下。
      await loadStats().catch(() => {});
    } catch (e) {
      setErr(String(e));
      setStatus("");
    } finally {
      $("lab-run").disabled = false;
      $("lab-preview").disabled = false;
    }
  }

  function ensure() {
    if (started) return;
    started = true;
    $("lab-input").value = SAMPLE;
    $("lab-terms").value = DEFAULT_TERMS;
    $("lab-sql").textContent = SQL_TEXT;
    $("lab-sample").addEventListener("click", () => {
      $("lab-input").value = SAMPLE;
      $("lab-terms").value = DEFAULT_TERMS;
    });
    $("lab-preview").addEventListener("click", preview);
    $("lab-run").addEventListener("click", run);
    renderMetrics(null);
    loadStats().catch(() => {});
  }

  window.moniDesensLab = { ensure };
})();
