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
    statsLlmUsage: {
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

    // 高价值榜单双维反选 (localStorage v2: moni_case_exclusions_v2)
    caseExclusions: { version: 2, keywords: [], countries: [], regions: [] },
    excludeKeywordOptions: [],
    excludePanelOpen: false,
    excludeTab: 'keywords',
    excludeSearch: '',
    excludeCollapsedRegions: [],
    rankPage: 1,
    rankPageSize: 20,
    rankPageSizeOptions: [20, 50],

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
          this.loadStatsLlmUsage(),
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

    async loadStatsLlmUsage() {
      const r = await fetch(`/api/stats/llm-usage?period=${this.period}`);
      if (!r.ok) throw new Error(`llm-usage ${r.status}`);
      this.statsLlmUsage = await r.json();
    },

    async loadCasesDashboard() {
      // 申报累计口径, 固定 all, 不跟 toolbar 7d/30d/90d (审计 F7)
      // 失败不抛: 不要把整页 overview/趋势一起打成 error
      try {
        const r = await fetch('/api/stats/cases-dashboard?period=all');
        if (!r.ok) throw new Error(`cases-dashboard ${r.status}`);
        const d = await r.json();
        this.casesDashboard = Object.assign({}, this.casesDashboard, d);
        this._normalizeRankingRows();
        this._restoreExclusions();
        if (Array.isArray(d.keyword_options) && d.keyword_options.length) {
          this.excludeKeywordOptions = d.keyword_options
            .map((s) => String(s).trim()).filter(Boolean);
        } else {
          await this.loadRankingKeywords();
        }
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
          `<text x="${cx}" y="${cy - 4}" text-anchor="middle" font-size="24" font-weight="700" fill="#3B82F6">${pct}%</text>` +
          `<text x="${cx}" y="${cy + 14}" text-anchor="middle" font-size="12" fill="#6B7280">${label}</text>`;
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

    // ── 双维精确反选 (sprint-19 PR #136) ───────────────────────
    // 关键词精确 Set + country_code + region, 任一命中剔除.
    // 不做 substring. localStorage v2; v1 moni_excluded_case_tags → keywords.
    _EXCLUSIONS_V2_KEY: 'moni_case_exclusions_v2',
    _EXCLUDED_TAGS_KEY: 'moni_excluded_case_tags',
    _GEO_CHIP_GROUPS: [
      {
        region: 'APAC', label: '亚太',
        countries: [
          { code: 'CN', name: '中国' }, { code: 'HK', name: '中国香港' },
          { code: 'MO', name: '中国澳门' }, { code: 'TW', name: '中国台湾' },
          { code: 'SG', name: '新加坡' }, { code: 'JP', name: '日本' },
          { code: 'KR', name: '韩国' }, { code: 'AU', name: '澳大利亚' },
          { code: 'VN', name: '越南' }, { code: 'ID', name: '印度尼西亚' },
          { code: 'MY', name: '马来西亚' }, { code: 'IN', name: '印度' },
          { code: 'TH', name: '泰国' }, { code: 'PH', name: '菲律宾' },
        ],
      },
      {
        region: 'NAM', label: '北美',
        countries: [
          { code: 'US', name: '美国' }, { code: 'CA', name: '加拿大' },
        ],
      },
      {
        region: 'EMEA', label: '欧洲中东非洲',
        countries: [
          { code: 'GB', name: '英国' }, { code: 'DE', name: '德国' },
          { code: 'FR', name: '法国' }, { code: 'CH', name: '瑞士' },
          { code: 'IT', name: '意大利' }, { code: 'NL', name: '荷兰' },
          { code: 'ES', name: '西班牙' }, { code: 'AE', name: '阿联酋' },
          { code: 'SA', name: '沙特阿拉伯' },
        ],
      },
      {
        region: 'LATAM', label: '拉美',
        countries: [
          { code: 'BR', name: '巴西' }, { code: 'MX', name: '墨西哥' },
          { code: 'AR', name: '阿根廷' }, { code: 'CL', name: '智利' },
        ],
      },
      {
        region: 'GLOBAL', label: '全球',
        countries: [
          { code: 'GLOBAL', name: '全球' },
        ],
      },
    ],
    // 无 geo 文本兜底别名 (与 repository.COUNTRY_TEXT_ALIASES 同步; 不含 ISO-2)
    _COUNTRY_TEXT_ALIASES: {
      CN: ['中国', '国内', '境内', 'China', 'PRC'],
      HK: ['中国香港', '香港', 'Hong Kong'],
      MO: ['中国澳门', '澳门', 'Macau'],
      TW: ['中国台湾', '台湾', 'Taiwan'],
      SG: ['新加坡', '狮城', 'Singapore'],
      JP: ['日本', 'Japan'],
      KR: ['韩国', 'Korea'],
      AU: ['澳大利亚', '澳洲', 'Australia'],
      VN: ['越南', 'Vietnam'],
      ID: ['印度尼西亚', '印尼', 'Indonesia'],
      MY: ['马来西亚', '马来', 'Malaysia'],
      IN: ['印度', 'India'],
      TH: ['泰国', 'Thailand'],
      PH: ['菲律宾'],
      US: ['美国', '美利坚', 'USA', 'America', 'United States', '华尔街'],
      CA: ['加拿大', 'Canada'],
      GB: ['英国', '英格兰', 'United Kingdom'],
      DE: ['德国', 'Germany'],
      FR: ['法国', 'France'],
      CH: ['瑞士', 'Switzerland'],
      IT: ['意大利', 'Italy'],
      NL: ['荷兰'],
      ES: ['西班牙'],
      AE: ['阿联酋', '迪拜', 'UAE'],
      SA: ['沙特', '沙特阿拉伯'],
      BR: ['巴西', 'Brazil', 'Pix'],
      MX: ['墨西哥', 'Mexico'],
      AR: ['阿根廷'],
      CL: ['智利'],
      GLOBAL: ['全球', '国际', '跨国'],
    },

    _emptyExclusions() {
      return { version: 2, keywords: [], countries: [], regions: [] };
    },

    _parseKeywords(raw) {
      if (Array.isArray(raw)) {
        return raw.map((s) => String(s).trim()).filter(Boolean);
      }
      if (typeof raw === 'string') {
        const s = raw.trim();
        if (!s) return [];
        if (s.charAt(0) === '[') {
          try {
            const arr = JSON.parse(s);
            if (Array.isArray(arr)) {
              return arr.map((x) => String(x).trim()).filter(Boolean);
            }
          } catch (e) { /* fall through */ }
        }
        return s.split(',').map((p) => p.trim()).filter(Boolean);
      }
      return [];
    },

    _normalizeRankingRows() {
      const rows = this.casesDashboard.high_value_ranking || [];
      rows.forEach((row) => {
        row.keywords = this._parseKeywords(row.keywords);
      });
    },

    _restoreExclusions() {
      try {
        const v2raw = window.localStorage.getItem(this._EXCLUSIONS_V2_KEY);
        if (v2raw) {
          const obj = JSON.parse(v2raw) || {};
          const clean = (arr) => Array.isArray(arr)
            ? arr.map((s) => String(s).trim()).filter(Boolean)
            : [];
          this.caseExclusions = {
            version: 2,
            keywords: clean(obj.keywords),
            countries: clean(obj.countries),
            regions: clean(obj.regions),
          };
          return;
        }
        const v1raw = window.localStorage.getItem(this._EXCLUDED_TAGS_KEY);
        const arr = v1raw ? JSON.parse(v1raw) : [];
        const keywords = Array.isArray(arr)
          ? arr.map((s) => String(s).trim()).filter(Boolean)
          : [];
        this.caseExclusions = {
          version: 2, keywords, countries: [], regions: [],
        };
        if (keywords.length) this._persistExclusions();
      } catch (e) {
        this.caseExclusions = this._emptyExclusions();
      }
    },

    _persistExclusions() {
      try {
        const payload = {
          version: 2,
          keywords: this.caseExclusions.keywords || [],
          countries: this.caseExclusions.countries || [],
          regions: this.caseExclusions.regions || [],
        };
        window.localStorage.setItem(this._EXCLUSIONS_V2_KEY, JSON.stringify(payload));
      } catch (e) { /* quota / private mode */ }
    },

    _setExclusion(dim, arr) {
      const next = Object.assign({}, this.caseExclusions);
      next[dim] = arr;
      this.caseExclusions = next;
      this._persistExclusions();
      this.rankPage = 1;
    },

    async loadRankingKeywords() {
      const rows = this.casesDashboard.high_value_ranking || [];
      this.excludeKeywordOptions = [];
      if (!rows.length) return;
      const already = rows.some((r) => (r.keywords || []).length);
      if (already) {
        const set = new Set();
        rows.forEach((r) => (r.keywords || []).forEach((k) => set.add(k)));
        this.excludeKeywordOptions = Array.from(set).sort();
        return;
      }
      const set = new Set();
      const withKw = (row, keywords) => Object.assign({}, row, { keywords });
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
      (this.caseExclusions.keywords || []).forEach((k) => set.add(k));
      return Array.from(set).sort();
    },

    filteredKeywordOptions() {
      const q = (this.excludeSearch || '').trim().toLowerCase();
      const all = this.excludeKeywordList();
      if (!q) return all;
      return all.filter((k) => String(k).toLowerCase().includes(q));
    },

    geoChipGroups() {
      const extra = {};
      const dist = ((this.casesDashboard || {}).geo_distribution || {}).by_country || [];
      dist.forEach((c) => {
        const code = String(c.country_code || '').trim();
        const region = String(c.region || '').trim().toUpperCase();
        if (!code || !region) return;
        extra[region] = extra[region] || [];
        extra[region].push({
          code,
          name: c.country_name || code,
        });
      });
      return this._GEO_CHIP_GROUPS.map((g) => {
        const known = new Set(g.countries.map((c) => c.code));
        const more = (extra[g.region] || []).filter((c) => !known.has(c.code));
        return {
          region: g.region,
          label: g.label,
          countries: g.countries.concat(more),
        };
      });
    },

    _geoLabel(code) {
      const key = String(code || '');
      for (let i = 0; i < this._GEO_CHIP_GROUPS.length; i++) {
        const g = this._GEO_CHIP_GROUPS[i];
        if (g.region === key) return g.label;
        for (let j = 0; j < g.countries.length; j++) {
          if (g.countries[j].code === key) return g.countries[j].name;
        }
      }
      return key;
    },

    _rowKeywordSet(row) {
      const set = new Set();
      this._parseKeywords(row && row.keywords).forEach((k) => set.add(k.toLowerCase()));
      return set;
    },

    _aliasesForCode(code) {
      const c = String(code || '').trim().toUpperCase();
      const base = this._COUNTRY_TEXT_ALIASES[c] || [];
      const names = base.slice();
      const dist = ((this.casesDashboard || {}).geo_distribution || {}).by_country || [];
      dist.forEach((row) => {
        if (String(row.country_code || '').trim().toUpperCase() === c && row.country_name) {
          names.push(String(row.country_name));
        }
      });
      const chip = this._geoLabel(c);
      if (chip && chip !== c) names.push(chip);
      const seen = new Set();
      return names.filter((n) => {
        const k = String(n).trim();
        if (!k || seen.has(k)) return false;
        seen.add(k);
        return true;
      });
    },

    _rowTextBlob(row) {
      const summary = String((row && row.summary) || '').slice(0, 100);
      return [row && row.subject, row && row.counterparty, summary]
        .map((s) => String(s || ''))
        .join('\n');
    },

    _textHitsCountry(blob, code) {
      if (!blob) return false;
      const aliases = this._aliasesForCode(code);
      const low = blob.toLowerCase();
      return aliases.some((alias) => {
        if (!alias) return false;
        if (/^[\x00-\x7F]+$/.test(alias)) return low.includes(alias.toLowerCase());
        return blob.includes(alias);
      });
    },

    _rowHitsExcluded(row, excluded) {
      if (!excluded) return false;
      const kwsEx = excluded.keywords || [];
      const ctryEx = excluded.countries || [];
      const regEx = excluded.regions || [];
      if (!kwsEx.length && !ctryEx.length && !regEx.length) return false;
      const kws = this._rowKeywordSet(row);
      if (kwsEx.some((e) => {
        const t = String(e || '').trim().toLowerCase();
        return t && kws.has(t);
      })) return true;
      const code = String((row && row.country_code) || '').trim().toUpperCase();
      if (code && ctryEx.some((c) => String(c || '').trim().toUpperCase() === code)) {
        return true;
      }
      const region = String((row && row.region) || '').trim().toUpperCase();
      if (region && regEx.some((r) => String(r || '').trim().toUpperCase() === region)) {
        return true;
      }
      // 无 geo: subject/counterparty/summary[:100] 扫被排除国家别名
      if (!code && ctryEx.length) {
        const blob = this._rowTextBlob(row);
        if (ctryEx.some((c) => this._textHitsCountry(blob, c))) return true;
      }
      return false;
    },

    filteredHighValueRanking() {
      const rows = this.casesDashboard.high_value_ranking || [];
      const ex = this.caseExclusions || this._emptyExclusions();
      if (!(ex.keywords || []).length
          && !(ex.countries || []).length
          && !(ex.regions || []).length) {
        return rows;
      }
      return rows.filter((row) => !this._rowHitsExcluded(row, ex));
    },

    excludedRankCount() {
      const all = (this.casesDashboard.high_value_ranking || []).length;
      return Math.max(0, all - this.filteredHighValueRanking().length);
    },

    excludedItemCount() {
      const ex = this.caseExclusions || this._emptyExclusions();
      return (ex.keywords || []).length
        + (ex.countries || []).length
        + (ex.regions || []).length;
    },

    excludeBadgeText() {
      const ex = this.caseExclusions || this._emptyExclusions();
      const k = (ex.keywords || []).length;
      const m = (ex.countries || []).length;
      const r = (ex.regions || []).length;
      const parts = [];
      if (k) parts.push(k + ' 关键词');
      if (m) parts.push(m + ' 国家');
      if (r) parts.push(r + ' 区域');
      if (!parts.length) return '';
      return '已排除 ' + parts.join(' · ') + ' · 过滤 ' + this.excludedRankCount() + ' 条';
    },

    excludedChips() {
      const ex = this.caseExclusions || this._emptyExclusions();
      const chips = [];
      (ex.keywords || []).forEach((k) => {
        chips.push({ dim: 'keywords', value: k, label: k, key: 'k:' + k });
      });
      (ex.countries || []).forEach((c) => {
        chips.push({ dim: 'countries', value: c, label: this._geoLabel(c), key: 'c:' + c });
      });
      (ex.regions || []).forEach((r) => {
        chips.push({ dim: 'regions', value: r, label: this._geoLabel(r), key: 'r:' + r });
      });
      return chips;
    },

    isKeywordExcluded(kw) {
      return (this.caseExclusions.keywords || []).includes(kw);
    },
    isCountryExcluded(code) {
      return (this.caseExclusions.countries || []).includes(code);
    },
    isRegionExcluded(region) {
      return (this.caseExclusions.regions || []).includes(region);
    },

    toggleKeyword(kw) {
      const cur = this.caseExclusions.keywords || [];
      this._setExclusion('keywords',
        cur.includes(kw) ? cur.filter((k) => k !== kw) : cur.concat([kw]));
    },
    toggleCountry(code) {
      const cur = this.caseExclusions.countries || [];
      this._setExclusion('countries',
        cur.includes(code) ? cur.filter((c) => c !== code) : cur.concat([code]));
    },
    toggleRegion(region) {
      const cur = this.caseExclusions.regions || [];
      this._setExclusion('regions',
        cur.includes(region) ? cur.filter((r) => r !== region) : cur.concat([region]));
    },
    removeExclusion(chip) {
      const cur = this.caseExclusions[chip.dim] || [];
      this._setExclusion(chip.dim, cur.filter((x) => x !== chip.value));
    },
    resetExclusions() {
      this.caseExclusions = this._emptyExclusions();
      this._persistExclusions();
      this.rankPage = 1;
    },

    rankPageCount() {
      const n = this.filteredHighValueRanking().length;
      const size = Number(this.rankPageSize) || 20;
      if (n <= 0) return 1;
      return Math.ceil(n / size);
    },

    rankDisplayPage() {
      const max = this.rankPageCount();
      const page = Number(this.rankPage) || 1;
      if (page > max) return max;
      if (page < 1) return 1;
      return page;
    },

    pagedHighValueRanking() {
      const rows = this.filteredHighValueRanking();
      const size = Number(this.rankPageSize) || 20;
      const page = this.rankDisplayPage();
      const start = (page - 1) * size;
      return rows.slice(start, start + size);
    },

    rankPageNumbers() {
      const n = this.rankPageCount();
      const pages = [];
      for (let i = 1; i <= n; i++) pages.push(i);
      return pages;
    },

    setRankPageSize(size) {
      const n = Number(size);
      this.rankPageSize = (n === 50) ? 50 : 20;
      this.rankPage = 1;
    },

    goRankPage(p) {
      const page = Number(p);
      if (!page) return;
      const max = this.rankPageCount();
      this.rankPage = Math.min(Math.max(page, 1), max);
    },

    rankPrevPage() {
      this.goRankPage(this.rankDisplayPage() - 1);
    },

    rankNextPage() {
      this.goRankPage(this.rankDisplayPage() + 1);
    },

    isRegionCollapsed(region) {
      return (this.excludeCollapsedRegions || []).includes(region);
    },
    toggleRegionGroup(region) {
      const cur = this.excludeCollapsedRegions || [];
      this.excludeCollapsedRegions = cur.includes(region)
        ? cur.filter((r) => r !== region)
        : cur.concat([region]);
    },

    closeExcludePanel() {
      this.excludePanelOpen = false;
      if (this._excludeDocHandler) {
        document.removeEventListener('pointerdown', this._excludeDocHandler);
        this._excludeDocHandler = null;
      }
    },

    toggleExcludePanel() {
      if (this.excludePanelOpen) {
        this.closeExcludePanel();
        return;
      }
      this.excludePanelOpen = true;
      this.excludeTab = 'keywords';
      this.excludeSearch = '';
      this.$nextTick(() => {
        const el = this.$refs.excludeSearch;
        if (el && typeof el.focus === 'function') el.focus();
      });
      const onDoc = (ev) => {
        const wrap = ev.target && ev.target.closest
          ? ev.target.closest('.stats-exclude-wrap')
          : null;
        if (wrap) return;
        this.closeExcludePanel();
      };
      this._excludeDocHandler = onDoc;
      setTimeout(() => document.addEventListener('pointerdown', onDoc), 0);
    },
  };
}
