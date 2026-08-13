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

    // 趋势图 (默认显示 fetch + llm)
    timeseriesFetch: { points: [], total: 0, display_status: 'ok' },
    timeseriesLlm:   { points: [], total: 0, display_status: 'ok' },
    timeseriesMetric: 'fetch',
    timeseriesGranularity: 'daily',

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

    // ── 标签/枚举 ────────────────────────────────────────
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
  };
}
