/* stats.js — W1 量化看板 Alpine 组件 (v1.0.19 + v1.0.25 method 饼图)
 *
 * MOA 4 视角拍板组合 A+A+A+A+A+(A+补丁):
 * - 5 模块横排 + 顶部三卡 + 节省瀑布图
 * - v1.0.25: 第 6 块「事实核查 method 分布」饼图
 * - 数据稀疏: display_status='数据积累中' 时不展示百分比/节省
 * - 抽取层 (events/cases) 阈值 N_EXTRACT_THRESHOLD=5 (其他模块 N_DEFAULT_THRESHOLD=10)
 * - 节省公式: display_label='公式估算' (防评委误读为实测)
 *
 * 端点:
 * - GET /api/stats/overview
 * - GET /api/stats/timeseries?metric=&granularity=
 * - GET /api/stats/llm
 * - GET /api/stats/savings
 * - GET /api/stats/fetch / extract / usage (单模块, 按需)
 * - GET /api/stats/fact-check-method?period=7d|30d|all  (v1.0.25)
 * - GET /api/stats/llm-usage?period=7d|30d|90d  (v1.0.32 LLM 用量)
 * - GET /api/stats/cases-dashboard?period=all   (v1.0.35.19 成果指标, 累计)
 * - GET /api/stats/data-funnel                  (v1.0.35.19 数据漏斗 4 数字)
 */
function statsModule() {
  return {
    // ── state ──────────────────────────────────────────────
    period: '30d',                  // 7d / 30d / 90d
    loading: false,
    error: null,

    // 顶部三卡
    cards: { baseline: {}, current: {}, target: {} },

    // 5 模块
    fetch:  { display_status: '数据积累中', raw: {}, count: 0, threshold: 10 },
    llm:    { display_status: '数据积累中', raw: {}, count: 0, threshold: 10 },
    extract:{ display_status: '数据积累中', raw: {}, count: 0, threshold: 5 },
    usage:  { display_status: '数据积累中', raw: {}, count: 0, threshold: 10 },
    efficiency: { display_status: '数据积累中', raw: {}, count: 0, threshold: 10,
                  display_label: '公式估算', t0_source: '', t0_config_version: '' },

    // v1.0.25: 事实核查 method 分布
    factCheckMethod: {
      period: '30d',
      period_start: null,
      total_articles_with_fact_check: 0,
      by_method: [],
      unfact_checked_total: 0,
      module: 'fact_check_method',
    },

    // v1.0.32: Q4 LLM 用量 (token/cost by model)
    llmUsage: {
      period: '30d',
      period_start: null,
      totals: { calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0 },
      by_model: [],
      module: 'llm_usage',
    },

    // 趋势图 (默认显示 fetch + llm)
    timeseriesFetch: { points: [], total: 0, display_status: 'ok' },
    timeseriesLlm:   { points: [], total: 0, display_status: 'ok' },
    timeseriesMetric: 'fetch',
    timeseriesGranularity: 'daily',

    // v1.0.35.19: 成果指标 (累计 all, 不跟 toolbar period)
    casesDashboard: {
      period: 'all',
      display_label: '待核验',
      total_cases: 0,
      total_institutions: 0,
      high_value_cases: 0,
      verified_cases: 0,
      published_cases: 0,
      pending_review: 0,
      fetch_total: 0,
      high_value_ranking: [],
    },

    // v1.0.35.19: 数据漏斗 (累计入库 → 事件 → 案例 → 简报)
    dataFunnel: {
      articles_total: 0,
      events_total: 0,
      cases_total: 0,
      share_tasks_done: 0,
    },

    // 高价值榜单排除标签 (localStorage: moni_excluded_case_tags)
    excludedCaseTags: [],
    excludeKeywordOptions: [],
    excludePanelOpen: false,

    // ── lifecycle ──────────────────────────────────────────
    async init() {
      if (this.currentView !== 'stats') return;
      await this.loadAll();
    },

    async loadAll() {
      this.loading = true;
      this.error = null;
      try {
        await Promise.all([
          this.loadOverview(),
          this.loadTimeseries('fetch'),
          this.loadTimeseries('llm'),
          this.loadFactCheckMethod(),
          this.loadLlmUsage(),
          this.loadCasesDashboard(),
          this.loadDataFunnel(),
        ]);
      } catch (e) {
        this.error = String(e);
        console.error('[stats] loadAll error', e);
      } finally {
        this.loading = false;
      }
    },

    async loadOverview() {
      const r = await fetch(`/api/stats/overview?period=${this.period}`);
      if (!r.ok) throw new Error(`overview ${r.status}`);
      const d = await r.json();
      this.cards = d.cards;
      this.fetch = d.modules.fetch;
      this.llm = d.modules.llm;
      this.extract = d.modules.extract;
      this.usage = d.modules.usage;
      this.efficiency = d.modules.efficiency;
    },

    async loadTimeseries(metric) {
      const r = await fetch(
        `/api/stats/timeseries?metric=${metric}` +
        `&period=${this.period}&granularity=${this.timeseriesGranularity}`
      );
      if (!r.ok) throw new Error(`timeseries ${metric} ${r.status}`);
      const d = await r.json();
      if (metric === 'fetch') this.timeseriesFetch = d;
      if (metric === 'llm')   this.timeseriesLlm   = d;
    },

    async loadFactCheckMethod() {
      // toolbar 用 7d/30d/90d; 端点额外支持 all (spec §3.3)
      const p = this.period === '90d' ? '90d' : this.period;
      const r = await fetch(`/api/stats/fact-check-method?period=${p}`);
      if (!r.ok) throw new Error(`fact-check-method ${r.status}`);
      this.factCheckMethod = await r.json();
    },

    async loadLlmUsage() {
      const r = await fetch(`/api/stats/llm-usage?period=${this.period}`);
      if (!r.ok) throw new Error(`llm-usage ${r.status}`);
      this.llmUsage = await r.json();
    },

    async loadCasesDashboard() {
      // 申报累计口径, 固定 all, 不跟 toolbar 7d/30d/90d (审计 F7)
      // 失败不抛: 不要把整页 overview/趋势一起打成 error
      try {
        const r = await fetch('/api/stats/cases-dashboard?period=all');
        if (!r.ok) throw new Error(`cases-dashboard ${r.status}`);
        const d = await r.json();
        this.casesDashboard = Object.assign({}, this.casesDashboard, d);
        this._restoreExcludedTags();
        await this.loadRankingKeywords();
      } catch (e) {
        console.error('[stats] cases-dashboard', e);
      }
    },

    async loadDataFunnel() {
      // 首选专用端点 (累计 4 数). 404 时拼现有接口, 入库文章暂用 fetch_total
      try {
        const r = await fetch('/api/stats/data-funnel');
        if (r.ok) {
          const d = await r.json();
          this.dataFunnel = Object.assign({}, this.dataFunnel, d);
          return;
        }
      } catch (e) {
        console.error('[stats] data-funnel', e);
      }
      try {
        const [evR, shR] = await Promise.all([
          fetch('/api/events/stats'),
          fetch('/api/share/tasks?page=1&page_size=1'),
        ]);
        const ev = evR.ok ? await evR.json() : {};
        const sh = shR.ok ? await shR.json() : {};
        this.dataFunnel = {
          articles_total: this.casesDashboard.fetch_total || 0,
          events_total: ev.total || 0,
          cases_total: this.casesDashboard.total_cases || 0,
          share_tasks_done: sh.total || 0,
        };
      } catch (e) {
        console.error('[stats] data-funnel fallback', e);
      }
    },

    // ── 周期切换 ──────────────────────────────────────────
    async setPeriod(p) {
      this.period = p;
      await this.loadAll();
    },

    // ── 辅助: 数字千位分隔 ────────────────────────────────
    fmt(n) {
      if (n == null || isNaN(n)) return '0';
      return Number(n).toLocaleString('zh-CN');
    },

    pct(x) {
      if (x == null || isNaN(x)) return '0%';
      return (x * 100).toFixed(1) + '%';
    },

    // ── 节省瀑布图 ────────────────────────────────────────
    savingsWaterfall() {
      const list = (this.efficiency.raw?.by_task_type || []).slice(0, 5);
      const max = Math.max(1, ...list.map(x => x.savings_minutes_total));
      return list.map(x => ({
        task_type: x.task_type,
        label: this.taskTypeLabel(x.task_type),
        value: x.savings_minutes_total,
        height: Math.round((x.savings_minutes_total / max) * 100),
        t0: x.t0_minutes_per_task,
        actual: x.actual_minutes_per_task,
        savings: x.savings_minutes_per_task,
      }));
    },

    // ── 趋势图 SVG (inline, 不引 chart.js) ────────────────
    trendPath(points, width = 320, height = 80) {
      if (!points || points.length === 0) return '';
      const counts = points.map(p => p.count);
      const max = Math.max(1, ...counts);
      const step = width / Math.max(1, points.length - 1);
      return counts.map((c, i) => {
        const x = i * step;
        const y = height - (c / max) * (height - 8);
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
    },

    trendDots(points, width = 320, height = 80) {
      if (!points || points.length === 0) return [];
      const counts = points.map(p => p.count);
      const max = Math.max(1, ...counts);
      const step = width / Math.max(1, points.length - 1);
      return counts.map((c, i) => ({
        cx: (i * step).toFixed(1),
        cy: (height - (c / max) * (height - 8)).toFixed(1),
        label: points[i].bucket,
        count: c,
      }));
    },

    // ── v1.0.25 method 饼图 (native SVG, 3 色) ────────────
    methodColor(method) {
      return ({
        llm_inference: '#3B82F6',  // blue-500
        web_search:    '#10B981',  // emerald-500
        human:         '#6B7280',  // gray-500
      })[method] || '#9CA3AF';
    },

    methodLabel(method) {
      return ({
        llm_inference: '基础档 (LLM)',
        web_search:    '联网档',
        human:         '人工',
        '<NULL>':      '未标注',
      })[method] || method;
    },

    methodPieSlices(size = 120) {
      const items = (this.factCheckMethod.by_method || []).filter(
        (x) => (x.article_count || 0) > 0
      );
      const total = items.reduce((s, x) => s + (x.article_count || 0), 0);
      if (!total) return [];

      const cx = size / 2;
      const cy = size / 2;
      const r = size / 2 - 2;
      let angle = -Math.PI / 2; // start at 12 o'clock
      const slices = [];

      for (const item of items) {
        const frac = (item.article_count || 0) / total;
        const sweep = frac * Math.PI * 2;
        // full circle → two semicircles
        if (frac >= 0.999) {
          slices.push({
            method: item.method,
            color: this.methodColor(item.method),
            d: `M ${cx} ${cy - r} A ${r} ${r} 0 1 1 ${cx} ${cy + r} ` +
               `A ${r} ${r} 0 1 1 ${cx} ${cy - r} Z`,
          });
          break;
        }
        const x1 = cx + r * Math.cos(angle);
        const y1 = cy + r * Math.sin(angle);
        const x2 = cx + r * Math.cos(angle + sweep);
        const y2 = cy + r * Math.sin(angle + sweep);
        const large = sweep > Math.PI ? 1 : 0;
        slices.push({
          method: item.method,
          color: this.methodColor(item.method),
          d: `M ${cx} ${cy} L ${x1.toFixed(2)} ${y1.toFixed(2)} ` +
             `A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)} Z`,
        });
        angle += sweep;
      }
      return slices;
    },

    // v1.0.32 fix: SVG path attribute reactivity quirk in Alpine — render
    // via x-html on <g> instead of :d binding. Returns SVG fragment
    // (path elements + <text> percentage label for single-method case).
    methodPieSvg(size = 120) {
      const slices = this.methodPieSlices(size);
      const cx = size / 2;
      const cy = size / 2;
      if (slices.length === 0) return '';
      const paths = slices.map((s) =>
        `<path d="${s.d}" fill="${s.color}" class="stats-method-color stats-method-${s.method}"></path>`
      ).join('');
      // single-method 空状态提示: 显示百分比文字在圆中央
      const items = (this.factCheckMethod.by_method || []).filter(
        (x) => (x.article_count || 0) > 0
      );
      let centerText = '';
      if (items.length === 1 && items[0].percentage != null) {
        const pct = items[0].percentage.toFixed(0);
        const label = this.methodLabel(items[0].method);
        centerText =
          `<text x="${cx}" y="${cy - 4}" text-anchor="middle" font-size="20" font-weight="700" fill="#3B82F6">${pct}%</text>` +
          `<text x="${cx}" y="${cy + 14}" text-anchor="middle" font-size="9" fill="#6B7280">${label}</text>`;
      }
      return paths + centerText;
    },

    // ── 标签/枚举 ────────────────────────────────────────
    costFmt(n) {
      if (n == null || isNaN(n)) return '$0';
      const v = Number(n);
      if (v === 0) return '$0';
      if (v < 0.01) return '$' + v.toFixed(6);
      return '$' + v.toFixed(4);
    },

    taskTypeLabel(t) {
      return ({
        'summary': '摘要',
        'fact_check': '事实核查',
        'event_extract': '事件抽取',
        'case_extract': '案例抽取',
        'predict_tags': '标签预测',
      })[t] || t;
    },

    moduleStatusClass(mod) {
      return mod.display_status === 'ok' ? 'stat-ok' : 'stat-sparse';
    },

    moduleStatusLabel(mod) {
      return mod.display_status === 'ok' ? '展示中' : '数据积累中';
    },

    // ── v1.0.35.19 成果指标 ──────────────────────────────────
    _shortLabel(s, n) {
      const t = String(s ?? '');
      return t.length > n ? t.slice(0, n) + '…' : t;
    },

    _confidencePct(n) {
      if (n == null || n === '' || isNaN(Number(n))) return null;
      const v = Number(n);
      return v <= 1 ? Math.round(v * 100) : Math.round(v);
    },

    outcomeConfidencePct(n) {
      const pct = this._confidencePct(n);
      return pct == null ? '—' : pct + '%';
    },

    outcomeConfidenceClass(n) {
      const pct = this._confidencePct(n);
      if (pct == null) return 'stats-outcome-conf-low';
      if (pct >= 80) return 'stats-outcome-conf-high';
      if (pct >= 50) return 'stats-outcome-conf-mid';
      return 'stats-outcome-conf-low';
    },

    // ── 排除标签反选 ─────────────────────────────────────────
    // 匹配策略: 案例已有 case_keywords → 精确匹配;
    // 尚未打标时 fallback 文本包含 (subject+counterparty+summary).
    // auto_llm 回填后自动走精确分支, 无需改 UI.
    _EXCLUDED_TAGS_KEY: 'moni_excluded_case_tags',

    _restoreExcludedTags() {
      try {
        const raw = window.localStorage.getItem(this._EXCLUDED_TAGS_KEY);
        const arr = raw ? JSON.parse(raw) : [];
        this.excludedCaseTags = Array.isArray(arr)
          ? arr.map((s) => String(s).trim()).filter(Boolean)
          : [];
      } catch (e) {
        this.excludedCaseTags = [];
      }
    },

    _persistExcludedTags() {
      try {
        window.localStorage.setItem(
          this._EXCLUDED_TAGS_KEY,
          JSON.stringify(this.excludedCaseTags || [])
        );
      } catch (e) { /* quota / private mode */ }
    },

    async loadRankingKeywords() {
      const rows = this.casesDashboard.high_value_ranking || [];
      this.excludeKeywordOptions = [];
      if (!rows.length) return;
      const set = new Set();
      const withKw = (row, keywords) => Object.assign({}, row, { keywords });
      // 只拉榜单 TOP (≤10). 先探测 1 条, 表未建/500 时不再连打
      try {
        const r0 = await fetch(
          `/api/cases/${encodeURIComponent(rows[0].case_id)}/keywords`,
          { credentials: 'same-origin' }
        );
        if (!r0.ok) return;
        const d0 = await r0.json();
        const kw0 = (d0.items || []).map((i) => i.keyword).filter(Boolean);
        kw0.forEach((k) => set.add(k));
        const rest = await Promise.all(rows.slice(1).map(async (row) => {
          try {
            const r = await fetch(
              `/api/cases/${encodeURIComponent(row.case_id)}/keywords`,
              { credentials: 'same-origin' }
            );
            if (!r.ok) return withKw(row, []);
            const d = await r.json();
            const keywords = (d.items || []).map((i) => i.keyword).filter(Boolean);
            keywords.forEach((k) => set.add(k));
            return withKw(row, keywords);
          } catch (e) {
            return withKw(row, []);
          }
        }));
        this.casesDashboard.high_value_ranking = [withKw(rows[0], kw0)].concat(rest);
        this.excludeKeywordOptions = Array.from(set).sort();
      } catch (e) {
        this.excludeKeywordOptions = [];
      }
    },

    excludeKeywordList() {
      const set = new Set(this.excludeKeywordOptions || []);
      (this.excludedCaseTags || []).forEach((k) => set.add(k));
      return Array.from(set).sort();
    },

    _rowHitsExcluded(row, ex) {
      if (!ex || !ex.length) return false;
      const kws = (row.keywords || []).map((k) => String(k).toLowerCase());
      if (kws.length) {
        return ex.some((e) => kws.includes(String(e).toLowerCase()));
      }
      const blob = [row.subject, row.counterparty, row.summary]
        .map((s) => String(s || '').toLowerCase())
        .join('\n');
      return ex.some((e) => e && blob.includes(String(e).toLowerCase()));
    },

    filteredHighValueRanking() {
      const rows = this.casesDashboard.high_value_ranking || [];
      const ex = this.excludedCaseTags || [];
      if (!ex.length) return rows;
      return rows.filter((row) => !this._rowHitsExcluded(row, ex));
    },

    excludedRankCount() {
      const all = (this.casesDashboard.high_value_ranking || []).length;
      return Math.max(0, all - this.filteredHighValueRanking().length);
    },

    toggleExcludedTag(kw) {
      const cur = this.excludedCaseTags || [];
      if (cur.includes(kw)) {
        this.excludedCaseTags = cur.filter((k) => k !== kw);
      } else {
        this.excludedCaseTags = cur.concat([kw]);
      }
      this._persistExcludedTags();
    },

    resetExcludedTags() {
      this.excludedCaseTags = [];
      this._persistExcludedTags();
    },

    toggleExcludePanel() {
      this.excludePanelOpen = !this.excludePanelOpen;
      if (this.excludePanelOpen) {
        const onDoc = (ev) => {
          const wrap = ev.target && ev.target.closest
            ? ev.target.closest('.stats-exclude-wrap')
            : null;
          if (wrap) return;
          this.excludePanelOpen = false;
          document.removeEventListener('pointerdown', onDoc);
        };
        setTimeout(() => document.addEventListener('pointerdown', onDoc), 0);
      }
    },
  };
}
