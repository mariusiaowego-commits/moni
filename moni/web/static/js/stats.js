/* stats.js — W1 量化看板 Alpine 组件 (v1.0.19)
 *
 * MOA 4 视角拍板组合 A+A+A+A+A+(A+补丁):
 * - 5 模块横排 + 顶部三卡 + 节省瀑布图
 * - 数据稀疏: display_status='数据积累中' 时不展示百分比/节省
 * - 抽取层 (events/cases) 阈值 N_EXTRACT_THRESHOLD=5 (其他模块 N_DEFAULT_THRESHOLD=10)
 * - 节省公式: display_label='公式估算' (防评委误读为实测)
 *
 * 4 个端点 + 2 个补充端点 (单模块拉取避免一次拉全):
 * - GET /api/stats/overview
 * - GET /api/stats/timeseries?metric=&granularity=
 * - GET /api/stats/llm
 * - GET /api/stats/savings
 * - GET /api/stats/fetch / extract / usage (单模块, 按需)
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