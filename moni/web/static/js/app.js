// Root Alpine component — single source of truth for all state and methods

// v1.0.9.8: particle class for fetch modal — smooth curve, no wobble
const PARTICLE_PALETTE = [
  [122, 115, 116],  // 锌灰 #7A7374
  [128, 118, 110],  // 雁灰 #80766E
  [194, 124, 136],  // 山黎豆红 #C27C88
  [181, 152, 161],  // 隐红灰 #B598A1
  [189, 174, 173],  // 芦穗灰 #BDAEAD
];

// v1.0.45 (sprint-25 A1-a): hard ceiling on live fetch-modal particles.
// Bursts fire every 200 ms and each particle lives ~70-110 frames, so an
// uncapped array grows the whole time the modal is open — costing one
// update() + up to 7 arc() calls per particle per frame.
//
// 200, not 180: warden measured the pre-cap live population between 89 and
// 173 across runs, so 180 would clip the observed peak and change how the
// modal currently looks. 200 sits above every observed peak, so the ceiling
// is invisible in normal use and only stops the unbounded case.
// dad pinned 200 (PLAN §2.1).
const MAX_PARTICLES = 200;

class FetchParticle {
  constructor(sx, sy, tx, ty) {
    this.sx = sx; this.sy = sy;
    this.tx = tx; this.ty = ty;
    this.x = sx; this.y = sy;
    const base = PARTICLE_PALETTE[Math.floor(Math.random() * PARTICLE_PALETTE.length)];
    this.rgb = base;
    this.alpha = 1.0;  // opaque
    this.size = 1.5 + Math.random() * 2.5;
    if (this.size > 3.5) this.size *= 1.5;  // top 3 largest -> 1.5x radius
    this.progress = 0;
    this.duration = 70 + Math.random() * 40; // ~1-2s (2x speed)
    this.cpx = (sx + tx) / 2 + (Math.random() - 0.5) * 140;
    this.cpy = (sy + ty) / 2 + (Math.random() - 0.5) * 160;
    this.alive = true;
    this.trail = [];
  }
  update() {
    this.progress += 1;
    if (this.progress >= this.duration) { this.alive = false; return; }
    const t = this.progress / this.duration, mt = 1 - t;
    this.x = mt*mt*this.sx + 2*mt*t*this.cpx + t*t*this.tx;
    this.y = mt*mt*this.sy + 2*mt*t*this.cpy + t*t*this.ty;
    this.trail.push({ x: this.x, y: this.y });
    if (this.trail.length > 6) this.trail.shift();
  }
  draw(ctx) {
    const [r, g, b] = this.rgb;
    const t = this.progress / this.duration;
    let fade = 1;
    if (t < 0.03) fade = t / 0.03;
    if (t > 0.94) fade = (1 - t) / 0.06;
    const a = (this.alpha * fade * 0.8).toFixed(3);
    for (let i = 0; i < this.trail.length; i++) {
      const a2 = (i / this.trail.length) * 0.25 * fade * this.alpha;
      const sz = this.size * (0.3 + 0.7 * (i / this.trail.length));
      ctx.beginPath();
      ctx.arc(this.trail[i].x, this.trail[i].y, sz * 0.35, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${r},${g},${b},${a2.toFixed(3)})`;
      ctx.fill();
    }
    ctx.beginPath();
    ctx.arc(this.x, this.y, this.size * 2.5, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(${r},${g},${b},${(fade * this.alpha * 0.04).toFixed(3)})`;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(${r},${g},${b},${a})`;
    ctx.fill();
  }
}

// ═══════════════════════════════════════════════════════════════
// v1.0.40: Reusable Keyword Chip CRUD Component (Sprint-20 PR1)
// ═══════════════════════════════════════════════════════════════
function keywordChipEditor() {
  return {
    newKeyword: '',
    get items() {
      if (!this.configModalTarget) return [];
      if (!Array.isArray(this.configModalTarget.keywords)) {
        this.configModalTarget.keywords = [];
      }
      return this.configModalTarget.keywords;
    },
    addKeyword(val) {
      const raw = (val !== undefined ? val : this.newKeyword) || '';
      // 支持中英文逗号、换行、分号、顿号批量拆分
      const parts = raw.split(/[\n,，;；、]/).map(s => s.trim()).filter(Boolean);
      if (!parts.length) return;
      if (!this.configModalTarget) return;
      if (!Array.isArray(this.configModalTarget.keywords)) {
        this.configModalTarget.keywords = [];
      }
      for (const p of parts) {
        if (!this.configModalTarget.keywords.includes(p)) {
          this.configModalTarget.keywords.push(p);
        }
      }
      this.newKeyword = '';
    },
    removeKeyword(index) {
      if (!this.configModalTarget || !Array.isArray(this.configModalTarget.keywords)) return;
      this.configModalTarget.keywords.splice(index, 1);
    },
    handleKeydown(e) {
      if (e.isComposing) return; // 保护中文输入法拼音输入中
      if (e.key === 'Enter' || e.key === ',' || e.key === '，' || e.key === '、') {
        e.preventDefault();
        this.addKeyword();
      } else if (e.key === 'Backspace' && !this.newKeyword && this.items.length > 0) {
        this.removeKeyword(this.items.length - 1);
      }
    },
    handleInput(e) {
      if (e && e.isComposing) return;
      if (/[\n,，;；、]/.test(this.newKeyword)) {
        this.addKeyword();
      }
    },
    handlePaste(e) {
      e.preventDefault();
      const pasteText = (e.clipboardData || window.clipboardData).getData('text');
      this.addKeyword(pasteText);
    },
    clearAll() {
      if (this.configModalTarget) {
        this.configModalTarget.keywords = [];
      }
    }
  };
}

function app() {
  const api = {
    // ── Theme engine (from theme.js) ──
    ...themeMixin(),

    // ── Calendar module (v1.0.16 F5 行业日历) ──
    ...calendarModule(),

    // ── Cases module (v1.0.17 F6 同业案例库) ──
    ...casesModule(),

    // ── Stats module (v1.0.19 W1 量化看板) ──
    ...statsModule(),

    // ── Stats view enter hook (用于 x-init="statsOnEnter()") ──
    statsOnEnter() {
      if (typeof this.loadAll === 'function') return this.loadAll();
    },

    // ── Notification helpers (v1.0.13 dialog certainty) ──
    //    Replaces 33 native alert() calls with the moniToast stack.
    //    showToast() in themeMixin is a single-line legacy helper;
    //    _notify() routes to the stacking toast + sticky-on-error.
    _notify(kind, msg, opts) {
      try {
        if (typeof window.moniToast === 'object' && window.moniToast) {
          if (kind === 'error') return window.moniToast.error(msg, opts);
          if (kind === 'success') return window.moniToast.success(msg, opts);
          return window.moniToast.info(msg, opts);
        }
      } catch (e) { /* swallow — fall through */ }
      // Fallback: if toast stack isn't ready (very early load), use legacy showToast.
      if (typeof this.showToast === 'function') this.showToast(String(msg || ''));
    },


    // ── Service control ──
    serviceRunning: false,
    servicePid: null,
    serviceInfo: null,
    serviceInfoLoading: false,

    loadServiceStatus() {
      fetch('/api/service/status', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(d => {
          this.serviceRunning = d.running;
          this.servicePid = d.pid;
        })
        .catch(() => {
          this.serviceRunning = false;
          this.servicePid = null;
        });
    },

    loadServiceInfo(force = false) {
      if (this.serviceInfoLoading) return;
      this.serviceInfoLoading = true;
      const url = '/api/service/info' + (force ? '?force=true' : '');
      fetch(url, { credentials: 'same-origin' })
        .then(r => r.json())
        .then(d => { this.serviceInfo = d; })
        .catch(() => {})
        .finally(() => { this.serviceInfoLoading = false; });
    },

    serviceVersionLabel() {
      const info = this.serviceInfo;
      if (!info) return '—';
      if (info.version && info.commit) {
        const ahead = info.commits_ahead ? ` · 领先 ${info.commits_ahead}` : '';
        return `${info.version} (${info.commit})${ahead}`;
      }
      return info.version || info.commit || '—';
    },

    serviceUptimeLabel() {
      const sec = this.serviceInfo && this.serviceInfo.uptime_sec;
      if (!sec) return '';
      const hours = Math.floor(sec / 3600);
      const mins = Math.round((sec % 3600) / 60);
      return hours ? `${hours}h ${mins}m` : `${mins}m`;
    },

    async restartService() {
      if (!(await moniModal.confirm('确定重启服务？页面会短暂不可用 (~3秒)', { title: '重启服务', okLabel: '重启', danger: true }))) return;
      fetch('/api/service/restart', { method: 'POST', credentials: 'same-origin' })
        .then(r => r.json())
        .then(d => {
          this.showToast('正在重启...');
          // Poll for service to come back
          let attempts = 0;
          const poll = setInterval(() => {
            attempts++;
            fetch('/api/service/status', { credentials: 'same-origin' })
              .then(r => r.json())
              .then(d => {
                if (d.running) {
                  clearInterval(poll);
                  this.serviceRunning = true;
                  this.servicePid = d.pid;
                  this.showToast('服务已重启 ✓');
                }
              })
              .catch(() => {});
            if (attempts > 15) { clearInterval(poll); this.showToast('重启超时，请手动检查'); }
          }, 1000);
        })
        .catch(() => this.showToast('重启请求失败'));
    },

    // v1.0.5.12: curated 8-color palette for /config > Tags > color picker.
    // Sourced from /Users/mt16/dev/uiux-asset-library so the tag chip
    // colours stay visually consistent with the rest of the SPA
    // (calendar-date-picker violet, dizi v1 gold, dizi v2 red, plus
    // the moni sidebar dark and 2 utility neutrals).  Users pick
    // from this list — no more typing hex by hand.
    TAG_COLOR_PALETTE: [
      { hex: '#DC2626', label: '红'    },  // Tailwind red-600   — dizi v2 / urgent
      { hex: '#EA580C', label: '橙'    },  // Tailwind orange-600
      { hex: '#E7BC68', label: '金'    },  // dizi v1 accent gold
      { hex: '#16A34A', label: '绿'    },  // Tailwind green-600
      { hex: '#2563EB', label: '蓝'    },  // Tailwind blue-600
      { hex: '#8A71E8', label: '紫罗兰' },  // calendar-date-picker primary
      { hex: '#6B7280', label: '灰'    },  // Tailwind gray-500
      { hex: '#1a1a2e', label: '深空'  },  // moni sidebar dark
    ],

    // ── Basket (v1.0.6.9: 菜篮子) ──────────────────────────────
    basketItems: [],
    basketTotal: 0,
    basketPage: 1,
    basketPageSize: 20,
    basketHasMore: false,
    basketSource: '',
    basketReason: '',
    basketRescued: '',
    basketSources: [],
    basketDetail: null,
    basketTags: [],
    basketSelectedTags: [],
    basketRescuing: false,

    loadBasket() {
      const params = new URLSearchParams({
        page: this.basketPage,
        page_size: this.basketPageSize,
      });
      if (this.basketSource) params.set('source', this.basketSource);
      if (this.basketReason) params.set('reason', this.basketReason);
      if (this.basketRescued !== '') params.set('rescued', this.basketRescued);

      fetch(`/api/basket?${params}`, { credentials: 'same-origin' })
        .then(r => r.json())
        .then(d => {
          this.basketItems = d.items || [];
          this.basketTotal = d.total || 0;
          this.basketHasMore = (d.page * d.page_size) < d.total;
        })
        .catch(() => {
          this.basketItems = [];
          this.basketTotal = 0;
        });
    },

    loadBasketSources() {
      fetch('/api/basket?page_size=1', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(() => {
          // 从 basket 数据中提取唯一的 source 列表
          fetch('/api/basket?page_size=1000', { credentials: 'same-origin' })
            .then(r => r.json())
            .then(d => {
              const sources = new Set();
              (d.items || []).forEach(item => sources.add(item.source));
              this.basketSources = Array.from(sources).sort();
            });
        });
    },

    loadBasketTags() {
      fetch('/api/config/tags', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(d => {
          this.basketTags = d || [];
        })
        .catch(() => { this.basketTags = []; });
    },

    showBasketDetail(b) {
      this.basketDetail = { ...b };
      this.basketSelectedTags = [];
      this.loadBasketTags();
    },

    rescueBasketItem() {
      if (!this.basketDetail) return;
      this.basketRescuing = true;
      fetch(`/api/basket/${this.basketDetail.id}/rescue`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ tags: this.basketSelectedTags }),
      })
        .then(r => r.json())
        .then(d => {
          if (d.ok) {
            this.showToast('已 rescue 到文章库 ✓');
            this.basketDetail = null;
            this.loadBasket();
          } else {
            this.showToast(d.message || 'rescue 失败');
          }
        })
        .catch(() => this.showToast('rescue 请求失败'))
        .finally(() => { this.basketRescuing = false; });
    },

    async deleteBasketItem() {
      if (!this.basketDetail) return;
      if (!(await moniModal.confirm('确定从菜篮子删除？', { title: '从菜篮子删除', okLabel: '删除', danger: true }))) return;
      fetch(`/api/basket/${this.basketDetail.id}`, {
        method: 'DELETE',
        credentials: 'same-origin',
      })
        .then(r => r.json())
        .then(d => {
          if (d.ok) {
            this.showToast('已删除 ✓');
            this.basketDetail = null;
            this.loadBasket();
          } else {
            this.showToast(d.message || '删除失败');
          }
        })
        .catch(() => this.showToast('删除请求失败'));
    },

    basketPrevPage() {
      if (this.basketPage > 1) {
        this.basketPage--;
        this.loadBasket();
      }
    },

    basketNextPage() {
      if (this.basketHasMore) {
        this.basketPage++;
        this.loadBasket();
      }
    },

    formatBasketReason(reason) {
      const map = { blacklist: '黑名单', whitelist: '白名单', tag_filter: 'Tag 过滤' };
      return map[reason] || reason;
    },

    // ── Content cleaning (sprint-55) ──────────────────────────────
    cleaningData: null,
    cleaningRuns: [],
    cleaningRun: null,
    cleaningBusy: '',
    cleaningError: '',

    _cleaningNum(n) {
      return Number(n || 0).toLocaleString('zh-CN');
    },

    async loadCleaning() {
      try {
        const [overview, runs] = await Promise.all([
          fetch('/api/cleaning/overview', { credentials: 'same-origin' }).then(async r => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
          }),
          fetch('/api/cleaning/runs?limit=10', { credentials: 'same-origin' }).then(async r => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
          }),
        ]);
        this.cleaningData = overview;
        this.cleaningRuns = Array.isArray(runs) ? runs : [];
        if (!this.cleaningRun && this.cleaningRuns.length) {
          this.cleaningRun = this.cleaningRuns[0];
        }
        this.cleaningError = '';
      } catch (e) {
        this.cleaningError = '数据清洗概览加载失败';
      }
    },

    async runCleaningPreview() {
      if (this.cleaningBusy) return;
      this.cleaningBusy = 'preview';
      try {
        const r = await fetch('/api/cleaning/preview', { method: 'POST', credentials: 'same-origin' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        this.cleaningRun = await r.json();
        this._notify('success', `预演完成：${this._cleaningNum(this.cleaningRun.changed)} 篇会变化（未写入）`);
        await this.loadCleaning();
      } catch (e) {
        this._notify('error', `预演失败：${e.message}`);
      } finally {
        this.cleaningBusy = '';
      }
    },

    async runCleaningApply() {
      if (this.cleaningBusy) return;
      const ok = await moniModal.confirm(
        '执行后会重写命中的正文，并清掉这些文章已生成的摘要与简报正文（需重新生成）。' +
        '预演里的数字就是这次的结果；重复执行不会二次破坏正文。',
        { title: '执行数据清洗', okLabel: '执行清洗' }
      );
      if (!ok) return;
      this.cleaningBusy = 'apply';
      try {
        const r = await fetch('/api/cleaning/apply', { method: 'POST', credentials: 'same-origin' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        this.cleaningRun = await r.json();
        this._notify('success', `清洗完成：${this._cleaningNum(this.cleaningRun.changed)} 篇正文已更新`);
        await this.loadCleaning();
      } catch (e) {
        this._notify('error', `清洗失败：${e.message}`);
      } finally {
        this.cleaningBusy = '';
      }
    },

    cleaningReportUrl(run) {
      return run ? `/api/cleaning/runs/${run.id}/report.md` : '#';
    },

    async setCleaningContract(source, contract) {
      try {
        const r = await fetch(`/api/cleaning/sources/${encodeURIComponent(source)}/contract`, {
          method: 'PUT',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ contract }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        await this.loadCleaning();
        this._notify('success', `${source} 的交付形态已更新`);
      } catch (e) {
        this._notify('error', `更新失败：${e.message}`);
      }
    },

    // ── Content moderation labels (sprint-58) ─────────────────────
    labelSummary: null,
    labelRuns: [],
    labelRun: null,
    labelSamples: [],
    labelCategory: 'nsfw_gamble',
    labelBusy: '',
    labelError: '',
    labelRevealed: {},
    labelRevealAll: false,
    labelCategories: [
      { code: 'nsfw_gamble', label: 'S 类', badge: 'S' },
      { code: 'fin_crypto_ad', label: '交易引流', badge: '引流' },
      { code: 'spam_service', label: '灰产服务', badge: '灰产' },
      { code: 'pure_ad', label: '商业推广', badge: '推广' },
      { code: 'engagement_bait', label: '涨粉话术', badge: '涨粉' },
      { code: 'low_value_rant', label: '低质情绪', badge: '情绪' },
    ],

    labelName(code) {
      const found = this.labelCategories.find(c => c.code === code);
      return found ? found.label : code;
    },

    labelBadge(code) {
      const found = this.labelCategories.find(c => c.code === code);
      return found ? found.badge : code;
    },

    labelReportUrl() {
      return this.labelRun ? `/api/moderation/labels/runs/${this.labelRun.id}/report.md` : '#';
    },

    labelMasked(s) {
      return s.category === 'nsfw_gamble' && !this.labelRevealAll && !this.labelRevealed[s.article_id];
    },

    async loadLabels() {
      try {
        const [summary, runs] = await Promise.all([
          fetch('/api/moderation/labels/summary', { credentials: 'same-origin' }).then(async r => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
          }),
          fetch('/api/moderation/labels/runs?limit=10', { credentials: 'same-origin' }).then(async r => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
          }),
        ]);
        this.labelSummary = summary;
        this.labelRuns = Array.isArray(runs) ? runs : [];
        if (!this.labelRun && this.labelRuns.length) this.labelRun = this.labelRuns[0];
        this.labelError = '';
        await this.loadLabelSamples(this.labelCategory);
      } catch (e) {
        this.labelError = '内容分级概览加载失败';
      }
    },

    async loadLabelSamples(category, reveal) {
      try {
        const qs = new URLSearchParams({ category, limit: '20' });
        if (reveal) qs.set('reveal', '1');
        const r = await fetch(`/api/moderation/labels/samples?${qs}`, { credentials: 'same-origin' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        this.labelSamples = await r.json();
      } catch (e) {
        this.labelSamples = [];
      }
    },

    async selectLabelCategory(code) {
      this.labelCategory = code;
      this.labelRevealed = {};
      this.labelRevealAll = false;
      await this.loadLabelSamples(code);
    },

    async revealLabelSample(s) {
      if (this.labelRevealed[s.article_id]) {
        delete this.labelRevealed[s.article_id];
        this.labelRevealed = { ...this.labelRevealed };
        return;
      }
      // 明文只在用户明确点击时向后端要一次，不缓存到本地存储。
      const qs = new URLSearchParams({ category: s.category, limit: '20', reveal: '1' });
      try {
        const r = await fetch(`/api/moderation/labels/samples?${qs}`, { credentials: 'same-origin' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const revealed = await r.json();
        const match = revealed.find(x => x.article_id === s.article_id);
        if (match) s.title = match.title;
        this.labelRevealed = { ...this.labelRevealed, [s.article_id]: true };
      } catch (e) {
        this._notify('error', `读取失败：${e.message}`);
      }
    },

    async toggleLabelRevealAll() {
      this.labelRevealAll = !this.labelRevealAll;
      this.labelRevealed = {};
      await this.loadLabelSamples(this.labelCategory, this.labelRevealAll);
    },

    async toggleLabelGate() {
      const next = !this.labelSummary?.gate_enabled;
      const ok = await moniModal.confirm(
        next
          ? '打开后：命中且未改判（未被标记为误判）的文章，不再进简报、也不再生成摘要。'
          : '关闭后：所有已标注文章恢复正常进入简报与摘要流程。',
        { title: next ? '打开内容闸门' : '关闭内容闸门', okLabel: next ? '打开' : '关闭' }
      );
      if (!ok) return;
      try {
        const r = await fetch('/api/moderation/labels/gate', {
          method: 'PUT',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: next }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        await this.loadLabels();
        this._notify('success', next ? '闸门已打开' : '闸门已关闭');
      } catch (e) {
        this._notify('error', `切换失败：${e.message}`);
      }
    },

    async runLabelPreview() {
      if (this.labelBusy) return;
      this.labelBusy = 'preview';
      try {
        const r = await fetch('/api/moderation/labels/preview', { method: 'POST', credentials: 'same-origin' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        this.labelRun = await r.json();
        this._notify('success', `预演完成：命中 ${this._cleaningNum(this.labelRun.labeled)} 篇（未写入）`);
        await this.loadLabels();
      } catch (e) {
        this._notify('error', `预演失败：${e.message}`);
      } finally {
        this.labelBusy = '';
      }
    },

    async runLabelApply() {
      if (this.labelBusy) return;
      const ok = await moniModal.confirm(
        '标注只写 content_labels 表，不改正文、不删文章。重复执行不会重复写入。',
        { title: '执行内容标注', okLabel: '执行标注' }
      );
      if (!ok) return;
      this.labelBusy = 'apply';
      try {
        const r = await fetch('/api/moderation/labels/apply', { method: 'POST', credentials: 'same-origin' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        this.labelRun = await r.json();
        this._notify('success', `标注完成：写入 ${this._cleaningNum(this.labelRun.new_rows)} 行`);
        await this.loadLabels();
      } catch (e) {
        this._notify('error', `标注失败：${e.message}`);
      } finally {
        this.labelBusy = '';
      }
    },

    async reviewLabelSample(sample, decision) {
      try {
        const r = await fetch(
          `/api/moderation/labels/${encodeURIComponent(sample.article_id)}/${sample.category}/review`,
          {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ decision, note: '' }),
          }
        );
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const idx = this.labelSamples.indexOf(sample);
        if (idx >= 0) this.labelSamples.splice(idx, 1);
        this._notify('success', decision === 'confirmed' ? '已确认' : '已记为误判');
        await this.loadLabels();
      } catch (e) {
        this._notify('error', `提交失败：${e.message}`);
      }
    },

    async copyLabelDigest() {
      const s = this.labelSummary;
      if (!s) return;
      const parts = this.labelCategories.map(c => `${c.label} ${this._cleaningNum(s.categories?.[c.code]?.total || 0)}`);
      const lines = [
        `内容分级标注：${parts.join('，')}。规则版本 ${s.rule_version}。`,
        '只标注、不改正文；S 类默认打码，需人工逐条确认。',
      ];
      if (this.labelRun) {
        lines.push(
          `最近一次${this.labelRun.mode === 'apply' ? '执行' : '预演'}：` +
          `扫描 ${this._cleaningNum(this.labelRun.scanned)} 篇，命中 ${this._cleaningNum(this.labelRun.labeled)} 篇。`
        );
      }
      try {
        await navigator.clipboard.writeText(lines.join('\n'));
        this._notify('success', '汇报文本已复制');
      } catch (e) {
        this._notify('error', '复制失败，请手动选择文本');
      }
    },

    async copyCleaningDigest() {
      const d = this.cleaningData;
      const run = this.cleaningRun;
      if (!d) return;
      const lines = [
        `数据清洗：共 ${this._cleaningNum(d.total)} 篇，` +
        `待清洗 ${this._cleaningNum(d.pending)} 篇，` +
        `空正文 ${this._cleaningNum(d.states?.empty || 0)} 篇，` +
        `短稿 ${this._cleaningNum(d.states?.short || 0)} 篇。`,
      ];
      if (run) {
        lines.push(
          `最近一次${run.mode === 'apply' ? '执行' : '预演'}：` +
          `扫描 ${this._cleaningNum(run.scanned)} 篇，` +
          `正文变化 ${this._cleaningNum(run.changed)} 篇，` +
          `仅补状态 ${this._cleaningNum(run.backfilled)} 篇，` +
          `净减少 ${this._cleaningNum(run.chars_removed)} 字。`
        );
      }
      try {
        await navigator.clipboard.writeText(lines.join('\n'));
        this._notify('success', '汇报文本已复制');
      } catch (e) {
        this._notify('error', '复制失败，请手动选择文本');
      }
    },

    // ── Navigation ────────────────────────────────────────────────
    currentView: 'articles',

    // v1.0.5.10: map view name <-> URL path.  Used by navigate() to
    // pushState, and by init() to deep-link from location.pathname.
    _viewToPath: {
      articles: '/articles',
      calendar: '/calendar',
      cases:    '/cases',
      stats:    '/stats',
      crud:     '/crud',
      cron:     '/cron',
      fetch:    '/fetch',
      llmJobs:  '/llmJobs',
      share:    '/share',
      basket:   '/basket',
      cleaning: '/cleaning',
      config:   '/config',
      llmSettings: '/llmSettings',
    },
    _pathToView: {
      '/articles': 'articles',
      '/calendar': 'calendar',
      '/cases':    'cases',
      '/stats':    'stats',
      '/crud':     'crud',
      '/cron':     'cron',
      '/fetch':    'fetch',
      '/llmJobs':  'llmJobs',
      '/share':    'share',
      '/basket':   'basket',
      '/cleaning': 'cleaning',
      '/config':   'config',
      '/llmSettings': 'llmSettings',
    },

    navigate(view) {
      // v1.0.45 (sprint-25 B10): native View Transitions API for the 11
      // x-show sections. Zero dependency, zero bytes, and browsers without it
      // degrade silently to the current instant switch — so this is a pure
      // enhancement.
      //
      // Gate on prefers-reduced-motion in JS: the ::view-transition pseudo
      // elements are CSS, but the browser still takes the DOM snapshot and
      // runs the animation before CSS is consulted, so the media query alone
      // would not stop the work.
      const wantsReduced = !!(window.matchMedia
        && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
      const canTransition = !wantsReduced
        && typeof document.startViewTransition === 'function';
      const apply = () => { this._navigateNow(view); };
      if (canTransition) {
        try { document.startViewTransition(apply); }
        catch (e) { apply(); }
      } else {
        apply();
      }
    },

    _navigateNow(view) {
      // v1.0.25: 跑批日志 / 大模型设置 收进「配置管理」二级菜单
      if (view === 'cron') {
        this.currentView = 'config';
        this.configTab = 'cron';
        this.loadCronRuns();
        const path = '/config';
        if (window.location.pathname !== path) {
          try { window.history.pushState({ view: 'config', configTab: 'cron' }, '', path); }
          catch (e) { window.history.replaceState({ view: 'config', configTab: 'cron' }, '', path); }
        }
        return;
      }
      if (view === 'llmSettings') {
        this.currentView = 'config';
        this.configTab = 'llm';
        this.llmTab = 'gateway';
        this.loadLlmProviders();
        this.loadLlmModels();
        this.loadLlmSettings();
        this.loadLlmGateway();
        const path = '/config';
        if (window.location.pathname !== path) {
          try { window.history.pushState({ view: 'config', configTab: 'llm' }, '', path); }
          catch (e) { window.history.replaceState({ view: 'config', configTab: 'llm' }, '', path); }
        }
        return;
      }

      this.currentView = view;
      // v1.0.13 (audit fix F3): tear down the share-queue poll if leaving /share.
      if (view !== 'share' && this._sharePollInterval) {
        this._stopSharePoll();
      }
      // v1.0.5.10: sync the URL so refresh / back / forward work and the
      // active link is shareable.  Use replaceState on first call (no
      // history entry yet) and pushState on subsequent navigations.
      const path = this._viewToPath[view] || '/articles';
      if (window.location.pathname !== path) {
        try {
          window.history.pushState({ view }, '', path);
        } catch (e) {
          // Fallback for browsers that disallow history API (rare).
          window.history.replaceState({ view }, '', path);
        }
      }
      if (view === 'articles') {
        this._loadArticlesWithPaging();
        this.loadArticlesFilters();  // v1.0.5.10: refresh source/tag <select> option lists
        // v1.0.8.4: kick off the Pin-tab fetch too so the "Pin (N)"
        // badge in the subtab bar shows the real count on first
        // render.  Without this the badge stays at 0 until the
        // user clicks the Pin tab.  Cheap (default page=1) and
        // re-entrant: when the user actually switches tabs, the
        // existing handler re-fetches with the current page state.
        this.loadPinnedArticles();
      }
      else if (view === 'crud') this.loadCrudArticles();
      else if (view === 'fetch') { this.loadSources(); this.resumeRunningFetchJobs(); }
      else if (view === 'llmJobs') this.loadLlmJobs();  // v1.0.5.1
      else if (view === 'share') { this.loadAllArticles(); this.loadPinnedArticles(); this._startSharePoll(); }
      else if (view === 'basket') { this.loadBasket(); this.loadBasketSources(); }
      else if (view === 'cleaning') { this.loadCleaning(); this.loadLabels(); }
      else if (view === 'calendar') { this.initCalendarPage(); }
      else if (view === 'cases')    { this.initCasesPage(); }
      else if (view === 'stats')    { this.loadAll(); }
      else if (view === 'config') {
        if (!this.configTab) this.configTab = 'sources';
        this.switchConfigTab(this.configTab);
      }
    },

    // v1.0.5.10: handle browser back/forward.  We only update state, not the
    // URL (the URL already changed via the browser's own history machinery).
    onPopState(event) {
      let view = (event.state && event.state.view) || this._pathToView[window.location.pathname] || 'articles';
      // v1.0.25: legacy deep links /cron /llmSettings → config subtabs
      if (view === 'cron') {
        this.currentView = 'config';
        this.configTab = 'cron';
        this.loadCronRuns();
        return;
      }
      if (view === 'llmSettings') {
        this.currentView = 'config';
        this.configTab = 'llm';
        this.llmTab = 'gateway';
        this.loadLlmProviders();
        this.loadLlmModels();
        this.loadLlmSettings();
        this.loadLlmGateway();
        return;
      }
      // v1.0.13 (audit fix F3): same teardown on back/forward.
      if (this.currentView === 'share' && view !== 'share') this._stopSharePoll();
      this.currentView = view;
      if (event.state && event.state.configTab) this.configTab = event.state.configTab;
      // Re-run the loaders for the target view.
      if (view === 'articles') {
        this._loadArticlesWithPaging();
        this.loadArticlesFilters();
        this.loadPinnedArticles();  // v1.0.8.4: see note above
      }
      else if (view === 'crud') this.loadCrudArticles();
      else if (view === 'fetch') { this.loadSources(); this.resumeRunningFetchJobs(); }
      else if (view === 'llmJobs') this.loadLlmJobs();
      else if (view === 'share') { this.loadAllArticles(); this.loadPinnedArticles(); this._startSharePoll(); }
      else if (view === 'basket') { this.loadBasket(); this.loadBasketSources(); }
      else if (view === 'cleaning') { this.loadCleaning(); this.loadLabels(); }
      else if (view === 'calendar') { this.initCalendarPage(); }
      else if (view === 'cases')    { this.initCasesPage(); }
      else if (view === 'stats')    { this.loadAll(); }
      else if (view === 'config') {
        this.switchConfigTab(this.configTab || 'sources');
      }
    },

    // ── Config panel (v1.0.2-F) ────────────────────────────────────
    // v1.0.25: 'sources' | 'tags' | 'filters' | 'llm' | 'cron'
    configTab: 'sources',
    configTags: [],
    configSources: [],
    configFilters: [],
    configFilterSourceFilter: '',
    configModalOpen: false,
    configModalType: null,  // 'tag' | 'source' | 'sourceTags' | 'filter' | 'fetchConfig'
    configModalTarget: null,
    configModalTargetOriginal: null,  // for tag edit: original name (immutable)
    fetchConfigData: { source_id: '', source_name: '', merged: {}, defaults: {}, has_custom: false },
    // v1.0.71 (sprint-35): X-bot 精选的 scope 信息 (来源徽标 / 串联说明)。
    fetchConfigScopeInfo: null,
    // v1.0.88 (sprint-38): 作者屏蔽名单 (独立表 source_author_blocks)。
    //   builtin_handles —— 内置只读黑名单 (刷量/营销/灰产号), 不可删;
    //   blocked_authors —— dad 自己屏蔽的, 可撤销。
    // 名单走**独立 REST 端点**, 与 fetch_config 完全解耦 (改门槛不影响名单)。
    blockedAuthors: { source_id: '', builtin_handles: [], blocked_authors: [] },
    blockedAuthorNewHandle: '',
    blockedAuthorNewName: '',

    // v1.0.12.2: Chinese labels + hints for fetch_config keys (and fallback).
    // Keep key as English for API; UI shows zh + description.
    FETCH_CONFIG_META: {
      // bestblogs / OPML 聚合
      max_feeds: {
        label: '最大 Feed 数',
        hint: '本轮最多处理 OPML 里多少个订阅源（feed）。',
        type: 'number',
      },
      max_articles_per_feed: {
        label: '每个 Feed 最多文章',
        hint: '单个 RSS/OPML feed 最多取最新几篇。',
        type: 'number',
      },
      max_articles_total: {
        label: '本轮文章上限',
        hint: '整次抓取合计最多入库/处理多少篇（硬顶）。',
        type: 'number',
      },
      request_delay: {
        label: '请求间隔（秒）',
        hint: '两次 HTTP 请求之间的等待，减轻对方限流。',
        type: 'number',
      },
      http_timeout: {
        label: '单 Feed 超时（秒）',
        hint: '单个 feed 的 HTTP 请求最多等多久，超时按失败计。',
        type: 'number',
      },
      min_feeds_before_failure_rate_check: {
        label: '失败率检查最小样本',
        hint: '至少检查多少个 feed 后，才开始按失败率判断是否整体断网。',
        type: 'number',
      },
      max_failure_rate: {
        label: '最大失败率',
        hint: '0.7 表示 70%。超过该比例才提前结束本轮；个别失效 feed 不会中断。',
        type: 'number',
      },
      // xwatch — Following (Channel C)
      following_enabled: {
        label: '启用 Following 通道',
        hint: '用登录态 Chrome（Profile 6）滚关注流；关掉则只跑账号列表/关键词。',
        type: 'bool',
      },
      following_max_posts: {
        label: 'Following 最多条数',
        hint: '关注流最多抓多少条（可设 100～300）。',
        type: 'number',
      },
      following_max_scrolls: {
        label: 'Following 最大滚动次数',
        hint: '页面下滚次数上限；条数加大时请同步加大（如 300 条约 80 次）。',
        type: 'number',
      },
      following_scroll_pause: {
        label: '滚动间隔（秒）',
        hint: '两次滚动之间等待，网络慢可略增大。',
        type: 'number',
      },
      following_skip_syndication: {
        label: 'Following 跳过 syndication',
        hint: '关注流已有正文时不再请求 twitter syndication，更快。',
        type: 'bool',
      },
      following_stop_chrome: {
        label: '抓完关闭 Profile 6',
        hint: '抓取结束后关掉 9222 的 Chrome，避免程序坞点不开。登录 cookie 仍保留在磁盘。',
        type: 'bool',
      },
      // xwatch — accounts (Channel A)
      accounts: {
        label: '观察账号列表',
        hint: '要盯的 X handle，一行一个，不要写完整 URL。如 dingyi、HiTw93。',
        type: 'string_list',
      },
      accounts_per_run: {
        label: '每轮抓几个账号',
        hint: '从观察列表轮转取 N 个账号，避免一次打太多。',
        type: 'number',
      },
      max_results_per_account: {
        label: '每账号最多条数',
        hint: '单个观察账号本轮最多发现几条推文。',
        type: 'number',
      },
      account_rss: {
        label: '账号 RSS 覆盖',
        hint: 'JSON 对象：{"handle":"https://...rss"}。有则优先用该 RSS，不走 OPML/DDG。',
        type: 'json_object',
      },
      account_ddg_fallback: {
        label: '账号无 RSS 时用 DDG',
        hint: '观察账号没有 RSS 时，用 DuckDuckGo from:handle 兜底（易被风控）。',
        type: 'bool',
      },
      // xwatch — queries (Channel B)
      queries: {
        label: '关键词列表',
        hint: 'DDG 搜索词，一行一个。如「微信小程序 服务通知」。',
        type: 'string_list',
      },
      queries_per_run: {
        label: '每轮关键词数',
        hint: '每轮只打几条 query（建议 1），降低 DDG 封禁概率。',
        type: 'number',
      },
      max_results_per_query: {
        label: '每关键词最多条数',
        hint: '单个关键词本轮最多返回几条推文 URL。',
        type: 'number',
      },
      ddg_circuit_minutes: {
        label: 'DDG 熔断（分钟）',
        hint: '触发 anomaly/网络错误后，多久内不再请求 DDG。',
        type: 'number',
      },
      search_timeout: {
        label: '搜索超时（秒）',
        hint: 'DDG / 发现请求的 HTTP 超时。',
        type: 'number',
      },
      syndication_timeout: {
        label: '全文抓取超时（秒）',
        hint: 'twitter syndication 拉单条推文的超时。',
        type: 'number',
      },
      skip_existing_urls: {
        label: '跳过已入库 URL',
        hint: '库中已有的推文不再重复请求 syndication。',
        type: 'bool',
      },
      // Scraper / RSS fetch_config keys
      feed_url: {
        label: 'Feed / RSS 地址',
        hint: '源的 Feed 或 RSS XML 链接。',
        type: 'string',
      },
      entry_xpath: {
        label: '条目提取规则 (XPath)',
        hint: '解析 HTML 时定位单篇文章条目的 XPath 表达式。',
        type: 'string',
      },
      title_xpath: {
        label: '标题提取规则 (XPath)',
        hint: '从条目中提取文章标题的 XPath 表达式。',
        type: 'string',
      },
      url_xpath: {
        label: '链接提取规则 (XPath)',
        hint: '从条目中提取文章链接的 XPath 表达式。',
        type: 'string',
      },
      content_xpath: {
        label: '正文提取规则 (XPath)',
        hint: '从条目中提取正文摘要的 XPath 表达式。',
        type: 'string',
      },
      date_xpath: {
        label: '日期提取规则 (XPath)',
        hint: '从条目中提取发布日期的 XPath 表达式。',
        type: 'string',
      },
      thresholds: {
        label: '准入门槛',
        hint: '按 topic 设置的最低点赞数/粉丝数闸门配置。',
        type: 'json_object',
      },
    },

    // v1.0.5.10: name -> {description, keywords} map for hover tooltips
    // on the articles page.  Built from configTags on demand.
    tagTooltipMap: {},
    // v1.0.84: name -> color，给抓取源/过滤表的 tag chip 上色
    tagColorMap: {},

    tagChipStyle(name) {
      const color = this.tagColorMap[name];
      if (!color) return '';
      return `--tag-bg: color-mix(in oklab, ${color} 16%, transparent);`
        + ` --tag-border: color-mix(in oklab, ${color} 36%, transparent);`;
    },

    switchConfigTab(tab) {
      this.configTab = tab || 'sources';
      // Lazy-load data for each config sub-tab
      if (this.configTab === 'sources') {
        this.configLoadSources();
        this.configLoadTags();   // v1.0.84: 抓取源表格要按 tag 上色
      } else if (this.configTab === 'tags') {
        this.configLoadTags();
      } else if (this.configTab === 'filters') {
        this.configLoadSources();
        this.configLoadFilters();
        this.configLoadTags();
      } else if (this.configTab === 'llm') {
        if (!this.llmTab) this.llmTab = 'gateway';
        this.loadLlmProviders();
        this.loadLlmModels();
        this.loadLlmSettings();
        if (this.llmTab === 'gateway') this.loadLlmGateway();
        if (this.llmTab === 'usage') this.loadLlmUsage();
      } else if (this.configTab === 'cron') {
        this.loadCronRuns();
      }
    },

    // v1.0.5.10: build the tooltip text for a given tag name.
    // Returns the tag's description + keywords, or a fallback when the
    // tag isn't in the map yet.  Falls back gracefully during the
    // race between /api/config/tags loading and the first render.
    // v1.0.5.20: 只展示该 tag 下命中的关键词
    formatTagTooltip(tagName, keywordHits = []) {
      const info = this.tagTooltipMap[tagName];
      if (!info) {
        return tagName + ' — 尚未加载 tag 描述';
      }
      const desc = info.description || '(无描述)';
      
      // 只展示该 tag 下命中的关键词
      if (keywordHits.length > 0) {
        return `${tagName}\n${desc}\n命中: ${keywordHits.join(', ')}`;
      }
      
      return `${tagName}\n${desc}`;
    },

    filterFiltersBySource() {
      this.configLoadFilters();
    },

    // ── Tags CRUD ───────────────────────────────────────────────────
    configLoadTags() {
      fetch('/api/config/tags', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          const tags = Array.isArray(data) ? data : [];
          this.configTags = tags;
          // v1.0.5.10: refresh the tooltip map so formatTagTooltip()
          // can answer for every visible tag in articles.
          const m = {};
          const colors = {};
          for (const t of tags) {
            m[t.name] = { description: t.description, keywords: t.keywords || [] };
            if (t.color) colors[t.name] = t.color;
          }
          this.tagTooltipMap = m;
          this.tagColorMap = colors;
        })
        .catch(err => { console.error('Failed to load tags:', err); this.configTags = []; });
    },
    configCreateTag() { this.openConfigModal('tag', null); },
    configEditTag(tag) { this.openConfigModal('tag', tag); },
    async configDeleteTag(tag) {
      if (!(await moniModal.confirm(`确定删除 tag "${tag.name}"? (会级联清除 source_tags 和 article_tags)`, { title: '删除 tag', okLabel: '删除', danger: true }))) return;
      fetch(`/api/config/tags/${encodeURIComponent(tag.name)}`, {
    method: 'DELETE', credentials: 'same-origin',
      })
    .then(r => { if (!r.ok) throw new Error('Delete failed'); this.configLoadTags(); })
    .catch(err => this._notify('error', '删除失败: ' + err.message));
    },
    configSaveTag(body, isEdit, originalName) {
      const url = isEdit
    ? `/api/config/tags/${encodeURIComponent(originalName)}`
    : '/api/config/tags';
      const method = isEdit ? 'PUT' : 'POST';
      return fetch(url, {
    method, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
      }).then(r => {
    if (!r.ok) return r.text().then(t => { throw new Error(t || `HTTP ${r.status}`); });
    return r.json();
      }).then(() => {
        this.configLoadTags();
        this.closeConfigModal();
      }).catch(err => this._notify('error', '保存失败: ' + err.message));
    },
    validateTagName(name) {
      if (!name || !name.trim()) return '标识 name 不能为空';
      const val = name.trim();
      if (!/^[a-z0-9_-]+$/.test(val)) return '格式错误：仅限小写英文、数字、短横线与下划线 (^[a-z0-9_-]+$)';
      const exists = (this.configTags || []).some(t => t.name.toLowerCase() === val.toLowerCase() && t.name !== this.configModalTargetOriginal);
      if (exists) return `标识 "${val}" 已存在，请换一个`;
      return '';
    },
    onTagNameInput() {
      if (!this.configModalTarget) return;
      if (!this.configModalTargetOriginal) {
        // S1: name 一旦被修改，立即回退 nameConfirmed = false，保存按钮重新 disabled
        this.configModalTarget.nameConfirmed = false;
        // #6: 实时校验格式与重名
        this.configModalTarget.nameError = this.validateTagName(this.configModalTarget.name);
      }
    },
    toggleConfirmTagName() {
      if (!this.configModalTarget) return;
      if (this.configModalTarget.nameConfirmed) {
        // 解锁修改
        this.configModalTarget.nameConfirmed = false;
        this.configModalTarget.nameError = this.validateTagName(this.configModalTarget.name);
      } else {
        // 校验并确定标识
        const err = this.validateTagName(this.configModalTarget.name);
        this.configModalTarget.nameError = err;
        if (!err) {
          this.configModalTarget.name = this.configModalTarget.name.trim();
          this.configModalTarget.nameConfirmed = true;
        }
      }
    },

    // ── Sources CRUD ───────────────────────────────────────────────
    configLoadSources() {
      fetch('/api/config/sources', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => { this.configSources = Array.isArray(data) ? data : []; })
        .catch(err => { console.error('Failed to load sources:', err); this.configSources = []; });
    },
    configCreateSource() { this.openConfigModal('source', null); },
    configEditSource(source) { this.openConfigModal('source', source); },
    async configDeleteSource(source) {
      if (!(await moniModal.confirm(`确定删除 source "${source.name}"?`, { title: '删除 source', okLabel: '删除', danger: true }))) return;
      fetch(`/api/config/sources/${encodeURIComponent(source.id)}`, {
        method: 'DELETE', credentials: 'same-origin',
      })
        .then(r => { if (!r.ok) throw new Error('Delete failed'); this.configLoadSources(); })
        .catch(err => this._notify('error', '删除失败: ' + err.message));
    },
    configSaveSource(body, isEdit) {
      const url = isEdit
        ? `/api/config/sources/${encodeURIComponent(body.id)}`
        : '/api/config/sources';
      const method = isEdit ? 'PUT' : 'POST';
      return fetch(url, {
        method, credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then(r => {
        if (!r.ok) return r.text().then(t => { throw new Error(t || `HTTP ${r.status}`); });
        return r.json();
      }).then(() => {
        this.configLoadSources();
        this.closeConfigModal();
      }).catch(err => this._notify('error', '保存失败: ' + err.message));
    },
    configEditSourceTags(source) {
      this.configLoadTags();
      this.openConfigModal('sourceTags', source);
    },
    configSaveSourceTags(sourceId, tagNames) {
      return fetch(`/api/config/sources/${encodeURIComponent(sourceId)}/tags`, {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(tagNames),
      }).then(r => {
        if (!r.ok) return r.text().then(t => { throw new Error(t || `HTTP ${r.status}`); });
        return r.json();
      }).then(() => {
        this.configLoadSources();
        this.closeConfigModal();
      }).catch(err => this._notify('error', '保存 tags 失败: ' + err.message));
    },

    // ── Fetch Config ─────────────────────────────────────────────
    fetchConfigMeta(key) {
      return this.FETCH_CONFIG_META[key] || null;
    },
    fetchConfigLabel(key) {
      const m = this.fetchConfigMeta(key);
      return m ? m.label : key;
    },
    fetchConfigHint(key) {
      const m = this.fetchConfigMeta(key);
      return m ? m.hint : '（未收录说明的自定义字段，请对照文档修改）';
    },
    fetchConfigFieldType(key, val) {
      const m = this.fetchConfigMeta(key);
      if (m && m.type) return m.type;
      if (typeof val === 'boolean') return 'bool';
      if (typeof val === 'number') return 'number';
      if (Array.isArray(val)) return 'string_list';
      if (val !== null && typeof val === 'object') return 'json_object';
      return 'string';
    },
    /** Textarea display for list/object fields */
    fetchConfigTextValue(key) {
      const val = this.fetchConfigData.merged[key];
      const t = this.fetchConfigFieldType(key, val);
      if (t === 'string_list') {
        return Array.isArray(val) ? val.join('\n') : (val == null ? '' : String(val));
      }
      if (t === 'json_object') {
        try {
          return JSON.stringify(val && typeof val === 'object' ? val : {}, null, 2);
        } catch (_) {
          return '{}';
        }
      }
      return val == null ? '' : String(val);
    },
    setFetchConfigTextValue(key, text) {
      const t = this.fetchConfigFieldType(key, this.fetchConfigData.merged[key]);
      if (t === 'string_list') {
        this.fetchConfigData.merged[key] = String(text || '')
          .split(/\r?\n/)
          .map(s => s.trim())
          .filter(Boolean);
        return;
      }
      if (t === 'json_object') {
        try {
          const parsed = JSON.parse(text || '{}');
          if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
            throw new Error('需要 JSON 对象 {}');
          }
          this.fetchConfigData.merged[key] = parsed;
        } catch (e) {
          this._notify('error', `字段 ${key} JSON 无效: ` + (e.message || e));
        }
        return;
      }
      this.fetchConfigData.merged[key] = text;
    },
    fetchConfigSortedKeys() {
      const merged = this.fetchConfigData.merged || {};
      // v1.0.71 (sprint-35): X-bot 专属键 (thresholds / scope) 有自己的
      // 专属区块 (类型化输入框), 不进通用键循环 —— 免得门槛被当成
      // raw JSON textarea (AC-10.2)。其它信源的键列表一个字不变。
      const isXbot = this.fetchConfigData.source_name === 'xbot_feed';
      const keys = isXbot
        ? Object.keys(merged).filter(k => k !== 'thresholds' && k !== 'scope')
        : Object.keys(merged);
      // Prefer known meta order, then unknown keys alpha
      const order = Object.keys(this.FETCH_CONFIG_META);
      return keys.sort((a, b) => {
        const ia = order.indexOf(a);
        const ib = order.indexOf(b);
        const ra = ia === -1 ? 1000 : ia;
        const rb = ib === -1 ? 1000 : ib;
        return ra - rb || a.localeCompare(b);
      });
    },
    // ── v1.0.71 (sprint-35): X-bot 精选的门槛 + scope 专属区块 ──────
    /** 读门槛值 (0 = 不设限)。 */
    fetchConfigThresholdValue(topic, field) {
      const t = (this.fetchConfigData.merged || {}).thresholds || {};
      const cfg = t[topic] || {};
      const v = cfg[field];
      return (typeof v === 'number' && isFinite(v)) ? v : 0;
    },
    /** 写门槛值 (非数字 → 0)。 */
    setFetchConfigThresholdValue(topic, field, raw) {
      const merged = this.fetchConfigData.merged || (this.fetchConfigData.merged = {});
      if (!merged.thresholds) merged.thresholds = {};
      if (!merged.thresholds[topic]) merged.thresholds[topic] = {};
      const n = parseInt(raw, 10);
      merged.thresholds[topic][field] = (isFinite(n) && n > 0) ? n : 0;
    },
    /** 读 scope 里的列表字段 (一行一个)。 */
    fetchConfigScopeList(key) {
      const s = (this.fetchConfigData.merged || {}).scope || {};
      const v = s[key];
      return Array.isArray(v) ? v.join('\n') : '';
    },
    /** 写 scope 里的列表字段 (一行一个 → 去空行)。 */
    setFetchConfigScopeList(key, text) {
      const merged = this.fetchConfigData.merged || (this.fetchConfigData.merged = {});
      if (!merged.scope) merged.scope = {};
      merged.scope[key] = String(text || '')
        .split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    },
    /** 读 scope 里的字符串字段。 */
    fetchConfigScopeText(key) {
      const s = (this.fetchConfigData.merged || {}).scope || {};
      return typeof s[key] === 'string' ? s[key] : '';
    },
    /** 写 scope 里的字符串字段。 */
    setFetchConfigScopeText(key, text) {
      const merged = this.fetchConfigData.merged || (this.fetchConfigData.merged = {});
      if (!merged.scope) merged.scope = {};
      merged.scope[key] = String(text == null ? '' : text);
    },
    /** scope 的来源徽标文案 (三态: upstream / moni_stored / moni_fallback)。 */
    fetchConfigScopeSourceLabel() {
      const info = this.fetchConfigScopeInfo || {};
      if (info.scope_source === 'upstream') return '上游导出';
      if (info.scope_source === 'moni_stored') return 'moni 侧副本';
      if (info.scope_source === 'moni_fallback') return 'moni 侧兜底（快照 2026-11-11）';
      if (info.scope_edited) return 'moni 侧副本';
      return 'moni 侧兜底（快照 2026-11-11）';
    },
    /** 加载 X-bot 的 scope 信息 (来源徽标 + 串联说明)。 */
    configLoadScope(sourceId) {
      if (!sourceId) return Promise.resolve(null);
      return fetch(`/api/config/sources/${encodeURIComponent(sourceId)}/scope`, { credentials: 'same-origin' })
        .then(r => { if (!r.ok) throw new Error('加载范围失败'); return r.json(); })
        .then(data => { this.fetchConfigScopeInfo = data; return data; })
        .catch(err => {
          console.error('Failed to load scope:', err);
          this.fetchConfigScopeInfo = null;
          return null;
        });
    },
    configEditFetchConfig(source) {
      const sid = encodeURIComponent(source.id);
      fetch(`/api/config/sources/${sid}/fetch_config`, { credentials: 'same-origin' })
        .then(r => { if (!r.ok) throw new Error('加载失败'); return r.json(); })
        .then(data => {
          this.fetchConfigData = {
            source_id: data.source_id,
            source_name: data.source_name,
            merged: { ...data.fetch_config },
            defaults: { ...data.defaults },
            has_custom: data.has_custom,
          };
          this.configModalType = 'fetchConfig';
          this.configModalOpen = true;
          // v1.0.71 (sprint-35): X-bot 精选额外拉 scope (来源徽标 + 串联说明)
          if (data.source_name === 'xbot_feed') {
            this.fetchConfigScopeInfo = null;
            this.configLoadScope(data.source_id);
            // v1.0.88 (sprint-38): 顺带拉作者屏蔽名单 (独立端点, 名单管理区)
            this.configLoadBlockedAuthors(data.source_id);
          } else {
            this.fetchConfigScopeInfo = null;
            this.blockedAuthors = { source_id: '', builtin_handles: [], blocked_authors: [] };
          }
        })
        .catch(err => this._notify('error', '加载抓取配置失败: ' + err.message));
    },
    configSaveFetchConfig() {
      const sid = encodeURIComponent(this.fetchConfigData.source_id);
      fetch(`/api/config/sources/${sid}/fetch_config`, {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(this.fetchConfigData.merged),
      }).then(r => {
        if (!r.ok) return r.text().then(t => { throw new Error(t || `HTTP ${r.status}`); });
        return r.json();
      }).then(data => {
        this.fetchConfigData = {
          source_id: data.source_id,
          source_name: data.source_name,
          merged: { ...data.fetch_config },
          defaults: { ...data.defaults },
          has_custom: data.has_custom,
        };
        this.configLoadSources();
        this.closeConfigModal();
      }).catch(err => this._notify('error', '保存抓取配置失败: ' + err.message));
    },

    // ── 作者屏蔽名单 (v1.0.88 sprint-38) ─────────────────────────────
    //   走**独立 REST 端点** (不是 fetch_config PUT) —— 名单是 dad 的
    //   个人运营资产, 与信源配置解耦, 且不受 seed-config 整行覆盖影响。
    configLoadBlockedAuthors(sourceId) {
      if (!sourceId) return Promise.resolve(null);
      const sid = encodeURIComponent(sourceId);
      return fetch(`/api/config/sources/${sid}/blocked-authors`, { credentials: 'same-origin' })
        .then(r => { if (!r.ok) throw new Error('加载屏蔽名单失败'); return r.json(); })
        .then(data => {
          this.blockedAuthors = {
            source_id: data.source_id,
            builtin_handles: data.builtin_handles || [],
            blocked_authors: data.blocked_authors || [],
          };
          return data;
        })
        .catch(err => {
          console.error('Failed to load blocked authors:', err);
          this.blockedAuthors = { source_id: '', builtin_handles: [], blocked_authors: [] };
          return null;
        });
    },

    configUnblockAuthor(handle) {
      const sourceId = this.blockedAuthors.source_id || (this.fetchConfigData || {}).source_id;
      if (!sourceId || !handle) return;
      const sid = encodeURIComponent(sourceId);
      fetch(`/api/config/sources/${sid}/blocked-authors/${encodeURIComponent(handle)}`, {
        method: 'DELETE', credentials: 'same-origin',
      }).then(r => {
        if (!r.ok) return r.text().then(t => { throw new Error(t || `HTTP ${r.status}`); });
        return r.json();
      }).then(() => {
        this._notify('success', `已解除屏蔽 @${handle}`);
        this.configLoadBlockedAuthors(sourceId);
      }).catch(err => this._notify('error', '解除屏蔽失败: ' + err.message));
    },

    configAddBlockedAuthor() {
      const sourceId = this.blockedAuthors.source_id || (this.fetchConfigData || {}).source_id;
      const handle = (this.blockedAuthorNewHandle || '').trim();
      if (!sourceId || !handle) return;
      const sid = encodeURIComponent(sourceId);
      fetch(`/api/config/sources/${sid}/blocked-authors`, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ handle, display_name: (this.blockedAuthorNewName || '').trim() }),
      }).then(r => {
        if (!r.ok) return r.text().then(t => { throw new Error(t || `HTTP ${r.status}`); });
        return r.json();
      }).then(data => {
        this.blockedAuthorNewHandle = '';
        this.blockedAuthorNewName = '';
        this._notify('success', `已屏蔽 @${data.handle}`);
        this.configLoadBlockedAuthors(sourceId);
      }).catch(err => this._notify('error', '屏蔽失败: ' + err.message));
    },

    // ── 详情弹窗: 屏蔽当前文章作者 (v1.0.88 sprint-38) ────────────────
    //   从 articleDetail.author ("Name (@handle)") 反解 handle —— 只认
    //   handle, display name 不参与匹配 (会变; handle 稳定)。
    parseAuthorHandle(author) {
      if (!author) return '';
      const m = /@([^)]+)\)/.exec(String(author));
      return m ? m[1].trim() : '';
    },

    // 只有 xbot_feed 的文章才有 handle 可屏蔽 (其它源的 publisher 是公众号名,
    // 没有 handle) —— 非 xbot 直接**隐藏**按钮, 不置灰 (agy 建议)。
    canBlockCurrentAuthor() {
      const d = this.articleDetail;
      if (!d) return false;
      if (d.source !== 'xbot_feed') return false;
      return !!this.parseAuthorHandle(d.author);
    },

    async confirmBlockAuthor(evt) {
      const d = this.articleDetail;
      if (!d) return;
      const handle = this.parseAuthorHandle(d.author);
      if (!handle) {
        this._notify('error', '这篇文章没有可屏蔽的作者 handle');
        return;
      }
      // 暂存触发元素 (P1 a11y 焦点还原用)。
      //
      // 优先用事件的 currentTarget —— **`document.activeElement` 不可靠**:
      // Safari 点击 <button> 不会给它焦点 (macOS 上尤其如此), 那样存到的
      // 就是 <body>, 还原等于没还原。currentTarget 一定是那个按钮。
      // 拿不到事件时退回 activeElement (排除 body)。
      const triggerBtn = (evt && evt.currentTarget)
        || (document.activeElement && document.activeElement !== document.body
            ? document.activeElement : null);
      const sourceId = this._xbotSourceId();
      if (!sourceId) {
        this._notify('error', '找不到 X-bot 精选信源');
        return;
      }

      // 先数一下该作者已有多少篇 —— dad 的决定: 旧文章**物理删除**,
      // 不可恢复 → 确认框必须显示篇数 (AC-17)。
      //
      // 必须问**后端**要这个数 (GET ?handle=xxx 返回 existing_count):
      // 前端的 this.articles 只是**当前页** (默认 50 条), 用它数会少算。
      let existing_count = 0;
      try {
        const resp = await fetch(
          `/api/config/sources/${encodeURIComponent(sourceId)}/blocked-authors`
          + `?handle=${encodeURIComponent(handle)}`,
          { credentials: 'same-origin' });
        if (resp.ok) {
          const data = await resp.json();
          existing_count = Number(data.existing_count || 0);
          this.blockedAuthors = {
            source_id: data.source_id,
            builtin_handles: data.builtin_handles || [],
            blocked_authors: data.blocked_authors || [],
          };
        }
      } catch (e) { /* 计数拿不到就按 0 处理, 不阻断流程 */ }

      const msg = existing_count > 0
        ? `屏蔽后, 该作者 (@${handle}) 后续的文章将不再入库 X-bot 精选。\n`
          + `将删除 ${existing_count} 篇已有文章, 不可恢复。`
        : `屏蔽后, 该作者 (@${handle}) 后续的文章将不再入库 X-bot 精选。\n`
          + `该作者暂无已有文章。`;

      // 复用现成的 moniModal.confirm (modal.js:223) —— 不自建第二套弹窗,
      // 保留既有的 a11y (role=alertdialog / aria-modal / focus trap / Esc)。
      //
      // ⚠️ P1 修复 (agy UI review r1, 2026-11-12): `moniModal.confirm` 关闭时
      // **不还原焦点** (它只 removeChild 弹窗, 焦点就掉回 <body>) →
      // Esc / 取消之后键盘用户的 Tab 动线断掉, 触发按钮拿不回焦点。
      // triggerBtn 在函数开头已暂存, 这里关闭后还原。
      const ok = await moniModal.confirm(msg, {
        title: `确认屏蔽作者 @${handle}?`,
        okLabel: '确认屏蔽',
        cancelLabel: '取消',
        danger: true,
      });
      if (!ok) {
        // 取消 (Esc / 点「取消」) → 焦点还给触发按钮。
        if (triggerBtn && typeof triggerBtn.focus === 'function') {
          try { triggerBtn.focus(); } catch (e) { /* swallow */ }
        }
        return;
      }

      try {
        const resp = await fetch(
          `/api/config/sources/${encodeURIComponent(sourceId)}/blocked-authors`,
          {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ handle, display_name: (d.author || '').split('(')[0].trim() }),
          });
        if (!resp.ok) {
          const txt = await resp.text();
          throw new Error(txt || `HTTP ${resp.status}`);
        }
        const data = await resp.json();
        this._notify('success', `已屏蔽作者 @${data.handle}`);
        // 关掉详情弹窗 + 刷新列表 (文章已被物理删除)。
        this.closeArticleDetail();
        this.loadAll();
        this.configLoadBlockedAuthors(sourceId);
      } catch (err) {
        this._notify('error', '屏蔽失败: ' + err.message);
      }
    },

    // X-bot 精选的 source id (从已加载的信源列表里找)。
    //
    // ⚠️ P0 修复 (agy UI review r1, 2026-11-12): 之前这里只查 `configSources`
    // (配置管理 Tab 懒加载) 与 `fetchConfigData` (抓取配置弹窗打开过才有)。
    // **首页 (默认文章查阅视图) 两个都是空的** → 返回 '' → 前置检查直接
    // 报「找不到 X-bot 精选信源」, 普通用户首次点击 100% 中断。
    //
    // 兜底顺序:
    //   1. `configSources` —— 配置管理页加载过
    //   2. `sources`        —— 抓取页加载过 (与 configSources 是不同列表)
    //   3. `fetchConfigData` —— 抓取配置弹窗打开过
    //   4. **常量兜底** `src-xbot-feed` —— migration 039 / seed_config.py
    //      固化的 id (三处一致, 见下方注释), 保证首页首点就能用。
    //
    // 为什么常量兜底是安全的: `sources.id` 是**主键**, `SourceUpdate` 不含
    // id 字段 (改不了); migration 039 与 seed_config.py 都写死
    // 'src-xbot-feed', 生产库实测也是这个值。
    _xbotSourceId() {
      const sources = this.configSources || this.sources || [];
      const hit = sources.find(s => s && s.name === 'xbot_feed');
      if (hit && hit.id) return hit.id;
      const fd = this.fetchConfigData || {};
      if (fd.source_name === 'xbot_feed' && fd.source_id) return fd.source_id;
      // 确定性兜底 —— 见上方注释。
      return 'src-xbot-feed';
    },

    // ── Filters CRUD ───────────────────────────────────────────────
    configLoadFilters() {
      const sourceId = this.configFilterSourceFilter || '';
      // 注意 (v1.0.40 PR2 review): 配置管理页 (admin) 需拉取全部规则 (含已停用 is_active=false 的规则),
      // 保证用户在界面上可以查看、编辑、恢复启用或删除已停用的全局/源规则，避免规则停用后直接从界面消失导致无法管理。
      if (sourceId === '__global__') {
        // Only load global filters
        fetch('/api/config/global-filters?include_inactive=true', { credentials: 'same-origin' })
          .then(r => r.json())
          .then(data => {
            const list = Array.isArray(data) ? data : [];
            this.configFilters = list.map(f => ({ ...f, is_global: true, source_id: '__global__' }));
          })
          .catch(err => { console.error('Failed to load global filters:', err); this.configFilters = []; });
      } else if (sourceId) {
        // Specific source filters
        fetch(`/api/config/filters?source_id=${encodeURIComponent(sourceId)}&include_inactive=true`, { credentials: 'same-origin' })
          .then(r => r.json())
          .then(data => {
            const list = Array.isArray(data) ? data : [];
            this.configFilters = list.map(f => ({ ...f, is_global: false }));
          })
          .catch(err => { console.error('Failed to load filters:', err); this.configFilters = []; });
      } else {
        // All sources: load both global and source filters, placing global at top!
        Promise.all([
          fetch('/api/config/global-filters?include_inactive=true', { credentials: 'same-origin' }).then(r => r.json()).catch(() => []),
          fetch('/api/config/filters?include_inactive=true', { credentials: 'same-origin' }).then(r => r.json()).catch(() => []),
        ]).then(([globals, sources]) => {
          const gList = (Array.isArray(globals) ? globals : []).map(f => ({ ...f, is_global: true, source_id: '__global__' }));
          const sList = (Array.isArray(sources) ? sources : []).map(f => ({ ...f, is_global: false }));
          this.configFilters = [...gList, ...sList];
        }).catch(err => {
          console.error('Failed to load filters:', err);
          this.configFilters = [];
        });
      }
    },
    filterFiltersBySource() {
      this.configLoadFilters();
    },
    configCreateFilter() { this.openConfigModal('filter', null); },
    configEditFilter(filter) { this.openConfigModal('filter', filter); },
    async configDeleteFilter(filter) {
      const isGlobal = filter.is_global || filter.source_id === '__global__';
      const label = isGlobal ? `全局 filter id=${filter.id}` : `filter id=${filter.id}`;
      if (!(await moniModal.confirm(`确定删除 ${label}?`, { title: '删除 filter', okLabel: '删除', danger: true }))) return;
      const url = isGlobal ? `/api/config/global-filters/${filter.id}` : `/api/config/filters/${filter.id}`;
      fetch(url, {
        method: 'DELETE', credentials: 'same-origin',
      })
        .then(r => { if (!r.ok) throw new Error('Delete failed'); this.configLoadFilters(); })
        .catch(err => this._notify('error', '删除失败: ' + err.message));
    },
    configSaveFilter(body, isEdit) {
      const isGlobal = body.is_global || body.source_id === '__global__';
      const url = isGlobal
        ? (isEdit ? `/api/config/global-filters/${body.id}` : '/api/config/global-filters')
        : (isEdit ? `/api/config/filters/${body.id}` : '/api/config/filters');
      const method = isEdit ? 'PUT' : 'POST';
      const payload = isGlobal
        ? {
            mode: body.mode,
            match_field: body.match_field,
            keywords: (body.keywords || []).map(s => String(s).trim()).filter(Boolean),
            is_active: !!body.is_active,
            notes: body.notes || null,
          }
        : {
            source_id: body.source_id,
            mode: body.mode,
            match_field: body.match_field,
            keywords: (body.keywords || []).map(s => String(s).trim()).filter(Boolean),
            is_active: !!body.is_active,
            notes: body.notes || null,
          };
      return fetch(url, {
        method, credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).then(r => {
        if (!r.ok) return r.text().then(t => { throw new Error(t || `HTTP ${r.status}`); });
        return r.json();
      }).then(() => {
        this.configLoadFilters();
        this.closeConfigModal();
      }).catch(err => this._notify('error', '保存失败: ' + err.message));
    },

    // ── Modal helpers ───────────────────────────────────────────────
    openConfigModal(type, target) {
      this.configModalType = type;
      // Deep clone to avoid mutating the table row
      this.configModalTarget = target ? JSON.parse(JSON.stringify(target)) : {};
      // Track original identifiers for PUT semantics (tag name, source id)
      if (type === 'tag') {
        if (target && target.name) {
          this.configModalTargetOriginal = target.name;
          const kws = Array.isArray(target.keywords) ? [...target.keywords] : [];
          this.configModalTarget.keywords = kws;
          this.configModalTarget.nameConfirmed = true;
          this.configModalTarget.nameError = '';
        } else {
          this.configModalTargetOriginal = null;
          this.configModalTarget = {
            name: '', display_name: '', description: '', color: '',
            keywords: [], is_active: true,
            nameConfirmed: false, nameError: '',
          };
        }
      } else if (type === 'source' && target && target.id) {
        this.configModalTargetOriginal = target.id;
      } else if (type === 'filter') {
        if (target && target.id) {
          this.configModalTargetOriginal = target.id;
          const kws = Array.isArray(target.keywords) ? [...target.keywords] : [];
          this.configModalTarget.keywords = kws;
          this.configModalTarget.is_global = !!target.is_global || target.source_id === '__global__';
          this.configModalTarget.source_id = this.configModalTarget.is_global ? '__global__' : target.source_id;
        } else {
          this.configModalTargetOriginal = null;
          const defaultSource = (this.configFilterSourceFilter === '__global__')
            ? '__global__'
            : (this.configFilterSourceFilter || (this.configSources[0]?.id || '__global__'));
          this.configModalTarget = {
            source_id: defaultSource,
            is_global: defaultSource === '__global__',
            mode: 'blacklist', match_field: 'title+content',
            keywords: [], is_active: true, notes: '',
          };
        }
      } else {
        this.configModalTargetOriginal = null;
      }
      if (type === 'source' && !target) {
        this.configModalTarget = {
          id: '', name: '', display_name: '', source_type: 'html', url: '',
          fetch_interval_min: 30, is_active: true, notes: '',
        };
      } else if (type === 'sourceTags' && target) {
        this.configModalTarget = {
          id: target.id, name: target.name,
          tags: Array.isArray(target.tags) ? [...target.tags] : [],
        };
      }
      this.configModalOpen = true;
    },
    closeConfigModal() {
      this.configModalOpen = false;
      this.configModalType = null;
      this.configModalTarget = null;
      this.configModalTargetOriginal = null;
    },
    toggleSourceTagInModal(tagName, checked) {
      if (!this.configModalTarget.tags) this.configModalTarget.tags = [];
      if (checked) {
        if (!this.configModalTarget.tags.includes(tagName)) {
          this.configModalTarget.tags.push(tagName);
        }
      } else {
        this.configModalTarget.tags = this.configModalTarget.tags.filter(t => t !== tagName);
      }
    },

    // ── Shared helpers ────────────────────────────────────────────
    formatTime(ts) {
      if (!ts) return '-';
      // Accept ISO 8601 string OR epoch seconds (legacy).
      const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
      if (isNaN(d)) return '-';
      const pad = n => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ` +
             `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    },

    // v1.0.8.5: truncate long titles in list views.  Returns the
    // string unchanged if it's already within `max` chars; otherwise
    // slices on a char boundary and appends an ellipsis.  Counts
    // JavaScript .length (UTF-16 code units) which is close enough
    // for "around 150 Chinese chars" — at 150 we never slice a
    // surrogate pair in practice.  CSS .title-cell single-line ellipsis
    // does the visual truncation; this just trims the underlying
    // string so we don't send 1000-char titles over the wire.
    truncateTitle(text, max = 150) {
      if (text == null) return '';
      const s = String(text);
      if (s.length <= max) return s;
      // Slice at max, then trim a trailing half-word / comma /
      // bracket so the visible text doesn't end mid-punctuation.
      let cut = s.slice(0, max);
      // Strip trailing whitespace + common CJK / ASCII
      // punctuation so the ellipsis reads cleanly.
      cut = cut.replace(/[\s,。.;；:：、\)\)\}\]」』]+$/, '');
      return cut + '…';
    },

    isNew(fetchedAt) {
      if (!fetchedAt) return false;
      // Accept ISO 8601 string OR epoch seconds.
      const epoch = typeof fetchedAt === 'number' ? fetchedAt : Math.floor(new Date(fetchedAt).getTime() / 1000);
      return (Math.floor(Date.now() / 1000) - epoch) < 86400;
    },

    // ── Articles panel ─────────────────────────────────────────────
    articles: [],
    articlesSearch: '',
    articlesSource: '',   // v1.0.5.10: '' = all sources
    articlesTag: '',      // v1.0.5.10: '' = all tags
    articlesSources: [],  // v1.0.5.10: list of {id, name, display_name} for the source <select>
    articlesTags: [],     // v1.0.5.10: list of {name} for the tag <select>
    // v1.0.8: Pin 子 tab.  'all' = existing flow, 'pinned' = article_pins
    // data source.  Pinned data is loaded into its own array; we
    // switch the table's x-for binding between articles[] and
    // pinnedArticles[] via the computed activeArticles getter.
    articlesSubTab: 'all',
    pinnedArticles: [],
    pinnedTotal: 0,
    pinnedPage: 1,
    pinnedPageSize: 20,
    pinnedHasMore: false,
    pinnedSelectedIds: [],  // bulk action selection (in-memory only)
    pinnedSort: 'pinned_at_desc',
    get activeArticles() {
      return this.articlesSubTab === 'pinned' ? this.pinnedArticles : this.articles;
    },
    get pinnedCount() {
      // Number of items in the current view (per-page count).  Good
      // enough for the tab label; the backend total is in pinnedTotal.
      return this.pinnedTotal || this.pinnedArticles.length;
    },

    loadArticles() {
      const params = new URLSearchParams();
      if (this.articlesSearch) params.set('keyword', this.articlesSearch);
      // v1.0.5.10: source/tag filters — empty string means "all"
      if (this.articlesSource) params.set('source', this.articlesSource);
      if (this.articlesTag) params.set('tag', this.articlesTag);
      if (this.articlesLabel) params.set('label_category', this.articlesLabel);
      // v1.0.5.14: pagination.  Backend default is 50; we explicitly
      // send the page size so the user can adjust it later.  Offset
      // comes from articlesPage (1-indexed in the UI, 0-indexed in
      // the API).
      params.set('limit', this.articlesPageSize);
      params.set('offset', (this.articlesPage - 1) * this.articlesPageSize);
      const query = params.toString();
      fetch('/api/articles' + (query ? '?' + query : ''), { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => { this.articles = Array.isArray(data) ? data : (data.articles || []); })
        .catch(err => console.error('loadArticles failed:', err));
    },

    // v1.0.5.14: pagination controls for the articles list.
    articlesLabel: '',
    articlesPage: 1,
    articlesPageSize: 50,
    articlesPageSizeOptions: [20, 50, 100, 200],
    articlesHasMore: false,

    // v1.0.5.14: detect "has more" by over-fetching one extra row.  We
    // ask the API for limit+1 rows and, if we get that many, set
    // articlesHasMore = true and trim the last row.  Cheap, doesn't
    // require a separate count endpoint.
    _loadArticlesWithPaging() {
      const params = new URLSearchParams();
      if (this.articlesSearch) params.set('keyword', this.articlesSearch);
      if (this.articlesSource) params.set('source', this.articlesSource);
      if (this.articlesTag) params.set('tag', this.articlesTag);
      if (this.articlesLabel) params.set('label_category', this.articlesLabel);
      const probe = parseInt(this.articlesPageSize, 10) || 50;
      params.set('limit', probe + 1);
      params.set('offset', (this.articlesPage - 1) * probe);
      const query = params.toString();
      fetch('/api/articles' + (query ? '?' + query : ''), { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          const list = Array.isArray(data) ? data : (data.articles || []);
          if (list.length > probe) {
            this.articlesHasMore = true;
            this.articles = list.slice(0, probe);
          } else {
            this.articlesHasMore = false;
            this.articles = list;
          }
        })
        .catch(err => console.error('_loadArticlesWithPaging failed:', err));
    },

    articlesPrevPage() {
      if (this.articlesPage > 1) { this.articlesPage -= 1; this._loadArticlesWithPaging(); }
    },
    articlesNextPage() {
      if (this.articlesHasMore) { this.articlesPage += 1; this._loadArticlesWithPaging(); }
    },
    articlesJumpToPage(n) {
      const p = parseInt(n, 10);
      if (!isNaN(p) && p >= 1) { this.articlesPage = p; this._loadArticlesWithPaging(); }
    },
    articlesChangePageSize() {
      this.articlesPage = 1; this._loadArticlesWithPaging();
    },

    // v1.0.5.14: when source/tag filter changes, jump to page 1.
    // Previously the filter changed but the user could be on page 3
    // and the list would empty out — now the pager resets.
    onArticlesFilterChange() {
      this.articlesPage = 1;
      this._loadArticlesWithPaging();
    },

    // v1.0.5.10: load the option lists for the source/tag <select>s.
    // Cheap GETs — we fire them in parallel and update independently
    // so a failure on one doesn't blank the other.
    loadArticlesFilters() {
      fetch('/api/config/sources', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          this.articlesSources = Array.isArray(data) ? data : (data.sources || []);
        })
        .catch(err => console.error('loadArticlesFilters (sources) failed:', err));
      fetch('/api/config/tags', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          const tags = Array.isArray(data) ? data : [];
          this.articlesTags = tags;
          // v1.0.5.10: also refresh the tag tooltip map so formatTagTooltip()
          // can answer for tags visible in the article list.
          const m = {};
          for (const t of tags) {
            m[t.name] = { description: t.description, keywords: t.keywords || [] };
          }
          this.tagTooltipMap = m;
        })
        .catch(err => console.error('loadArticlesFilters (tags) failed:', err));
    },

    // ── CRUD panel ────────────────────────────────────────────────
    crudArticles: [],
    crudSearch: '',  // v1.0.35.1: 标题模糊搜索 (debounce 300ms)
    showModal: false,
    deleteTarget: null,

    // v1.0.5.11: form modal state for create / edit.  One modal, two
    // modes — 'create' is empty form, 'edit' is pre-populated and
    // source/url are read-only (UNIQUE constraint).
    crudFormOpen: false,
    crudFormMode: 'create',  // 'create' | 'edit'
    crudForm: {
      id: null,
      title: '',
      source: '',
      url: '',
      author: '',
      summary: '',
      raw_content: '',
    },
    crudFormSaving: false,
    crudFormError: '',

    loadCrudArticles() {
      const params = new URLSearchParams();
      if (this.crudSearch) params.set('keyword', this.crudSearch);
      // v1.0.35.1: CRUD 用 keyword LIKE 模糊搜索 (后端 articles.py 已支持)
      const query = params.toString();
      fetch('/api/articles' + (query ? '?' + query : ''), { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => { this.crudArticles = Array.isArray(data) ? data : (data.articles || []); })
        .catch(err => console.error('loadCrudArticles failed:', err));
    },

    // v1.0.35.1: CRUD 搜索 debounce (300ms 避免每个键击都 fetch)
    crudSearchDebounceTimer: null,
    crudSearchInput() {
      clearTimeout(this.crudSearchDebounceTimer);
      this.crudSearchDebounceTimer = setTimeout(() => {
        this.loadCrudArticles();
      }, 300);
    },

    showDeleteModal(article) {
      this.deleteTarget = article;
      this.showModal = true;
    },

    closeModal() {
      this.showModal = false;
      this.deleteTarget = null;
    },

    confirmDelete() {
      const article = this.deleteTarget;
      if (!article || !article.id) return;
      fetch(`/api/articles/${article.id}?confirm=1`, { method: 'DELETE', credentials: 'same-origin' })
        .then(r => {
          if (!r.ok) throw new Error('Delete failed');
          this.crudArticles    = this.crudArticles.filter(a => a.id !== article.id);
          this.articles        = this.articles.filter(a => a.id !== article.id);
          this.allArticles     = this.allArticles.filter(a => a.id !== article.id);
          this.pinnedArticles  = this.pinnedArticles.filter(a => a.id !== article.id);  // v1.0.35.1: 同步清 pinned
          this._notify('success', `已删除: ${article.title?.slice(0, 30) || article.id}`);
          this.closeModal();
        })
        .catch(err => { this._notify('error', '删除失败: ' + err.message); this.closeModal(); });
    },

    // ── CRUD form (v1.0.5.11) ───────────────────────────────────
    // Opens the modal in either create or edit mode.  Pass `null`
    // for create, an article for edit.
    openCrudForm(article) {
      if (article) {
        this.crudFormMode = 'edit';
        this.crudForm = {
          id: article.id,
          title: article.title || '',
          source: article.source || '',
          url: article.url || '',
          author: article.author || '',
          summary: article.summary || '',
          raw_content: article.raw_content || '',
        };
      } else {
        this.crudFormMode = 'create';
        this.crudForm = {
          id: null,
          title: '',
          source: '',
          url: '',
          author: '',
          summary: '',
          raw_content: '',
        };
      }
      this.crudFormError = '';
      this.crudFormSaving = false;
      this.crudFormOpen = true;
    },

    closeCrudForm() {
      this.crudFormOpen = false;
      this.crudFormError = '';
    },

    submitCrudForm() {
      // Client-side validation
      if (!this.crudForm.title.trim() || !this.crudForm.source.trim() || !this.crudForm.url.trim()) {
        this.crudFormError = 'title / source / url 不能为空';
        return;
      }
      this.crudFormSaving = true;
      this.crudFormError = '';

      const isEdit = this.crudFormMode === 'edit';
      // v1.0.5.11: backend ArticleCreate uses `source_id` (TEXT, the
      // source name).  Field name kept for compatibility with the existing
      // endpoint / test_post_create_201 added in v1.0.5.10.
      const body = {
        title: this.crudForm.title.trim(),
        source_id: this.crudForm.source.trim(),
        url: this.crudForm.url.trim(),
      };
      if (this.crudForm.author.trim())     body.author     = this.crudForm.author.trim();
      if (this.crudForm.summary.trim())     body.summary    = this.crudForm.summary.trim();
      if (this.crudForm.raw_content.trim()) body.content   = this.crudForm.raw_content.trim();

      const url    = isEdit ? `/api/articles/${this.crudForm.id}` : '/api/articles';
      const method = isEdit ? 'PUT' : 'POST';

      fetch(url, {
        method,
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
        .then(async r => {
          const data = await r.json().catch(() => ({}));
          if (!r.ok) {
            const msg = (data && (data.detail || data.message)) || `${r.status} ${r.statusText}`;
            throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
          }
          return data;
        })
        .then(article => {
          // Update the local lists in place so the table re-renders
          // without a full reload.  POST returns the (new or existing)
          // article; PUT returns the updated one.
          if (isEdit) {
            const idx = this.crudArticles.findIndex(a => a.id === article.id);
            if (idx >= 0) this.crudArticles.splice(idx, 1, article);
          } else {
            // Avoid duplicates if the article already existed
            // (POST is idempotent on (source, url) and returns the
            // existing row).
            const existing = this.crudArticles.find(a => a.id === article.id);
            if (!existing) this.crudArticles.unshift(article);
          }
          this.closeCrudForm();
        })
        .catch(err => {
          this.crudFormError = '保存失败: ' + err.message;
          this.crudFormSaving = false;
        });
    },

    // ── Cron panel ────────────────────────────────────────────────
    cronRuns: [],

    loadCronRuns() {
      // v1.0.4: was /api/cron/jobs (returns synthetic jobs, not runs).
      // Use /api/cron/runs which returns real run history from cron_runs.
      fetch('/api/cron/runs', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          this.cronRuns = Array.isArray(data) ? data : (data.runs || []);
        })
        .catch(err => console.error('loadCronRuns failed:', err));
    },

    // ── Fetch panel ───────────────────────────────────────────────
    sources: [],
    fetchResult: '',
    fetchResultObj: null,
    fetchResultKey: 0,  // remount result details via x-for :key (force collapsed)
    fetchAllResultKey: 0,  // remount all-result details (force collapsed)
    fetchAllResult: null,
    fetchingAll: false,
    // v1.0.6.1: structured explanation of fetch result
    formatFetchResult() {
      const d = this.fetchResultObj;
      if (!d || typeof d !== 'object') return [];
      const lines = [];
      const src = d.source || '未知源';
      const inserted = d.inserted ?? 0;
      const fetched = d.fetched ?? 0;
      const parsed = d.parsed ?? 0;
      const filtered = d.filtered ?? 0;
      const droppedBL = d.dropped_blacklist ?? 0;
      const droppedWL = d.dropped_whitelist ?? 0;
      const droppedTag = d.dropped_tag_filter ?? 0;
      const errors = d.parse_errors ?? 0;
      const cleaned = d.cleaned ?? 0;
      const tagsUsed = d.tags_used ?? 0;
      const filtersApplied = d.filters_applied ?? 0;

      // Header: outcome summary
      if (inserted > 0) {
        lines.push({ type: 'success', text: `✅ 新增 ${inserted} 篇文章入库` });
      } else if (fetched > 0 && inserted === 0) {
        lines.push({ type: 'warn', text: `⚠️ 抓取了 ${fetched} 条但无新增（全部重复或被过滤）` });
      } else {
        lines.push({ type: 'muted', text: `ℹ️ 无新内容` });
      }

      // Pipeline breakdown
      lines.push({ type: 'section', text: '抓取流程' });
      lines.push({ type: 'item', label: '远程拉取', value: `${fetched} 条`, detail: '从 RSS/HTML/API 拉到的原始条目数' });
      if (parsed !== fetched) {
        lines.push({ type: 'item', label: '解析成功', value: `${parsed} 条`, detail: `解析失败 ${errors} 条` });
      }
      if (cleaned > 0) {
        lines.push({ type: 'item', label: 'Boilerplate 清理', value: `${cleaned} 条`, detail: '移除了尾部 CTA 等无关文本' });
      }

      // Dedup: articles fetched but not inserted and not filtered out
      const droppedTotal = droppedBL + droppedWL + droppedTag;
      const deduped = parsed - inserted - droppedTotal;
      if (deduped > 0) {
        lines.push({ type: 'item', label: '去重', value: `跳过 ${deduped} 条`, detail: 'URL 已存在，未重复入库' });
      }

      // Filter breakdown
      lines.push({ type: 'section', text: '过滤情况' });
      if (droppedBL > 0) {
        lines.push({ type: 'item', label: '黑名单', value: `丢弃 ${droppedBL} 条`, detail: '命中排除关键词（如入境游/游戏等）' });
      }
      if (droppedWL > 0) {
        lines.push({ type: 'item', label: '白名单', value: `丢弃 ${droppedWL} 条`, detail: '未命中任何包含关键词' });
      }
      if (droppedTag > 0) {
        lines.push({ type: 'item', label: 'Tag 过滤', value: `丢弃 ${droppedTag} 条`, detail: `${tagsUsed} 个关键词命中，但无 tag 匹配` });
      }
      if (droppedBL === 0 && droppedWL === 0 && droppedTag === 0) {
        lines.push({ type: 'item', label: '过滤结果', value: '全部通过', detail: filtersApplied > 0 ? `${filtersApplied} 条规则已检查，无命中` : '无过滤规则或全部通过' });
      }

      // Error
      if (d.error) {
        lines.push({ type: 'error', text: `❌ 错误: ${d.error}` });
      }

      // v1.0.71 (sprint-35): 门槛过滤 (X-bot 精选的第二道闸门)
      const thrDropped = d.threshold_dropped ?? 0;
      const thrBy = d.threshold_dropped_by || {};
      const thrMissing = d.threshold_missing_by || {};
      if (thrDropped > 0 || Object.keys(thrMissing).length > 0) {
        lines.push({ type: 'section', text: '门槛过滤' });
        if (thrDropped > 0) {
          const detail = Object.entries(thrBy)
            .map(([k, v]) => `${k} ${v}`)
            .join('，');
          lines.push({
            type: 'item',
            label: '门槛丢弃',
            value: `丢弃 ${thrDropped} 条`,
            detail: detail || '被 min_followers / min_likes 挡掉',
          });
        }
        if (Object.keys(thrMissing).length > 0) {
          const detail = Object.entries(thrMissing)
            .map(([k, v]) => `${k} ${v}`)
            .join('，');
          lines.push({
            type: 'item',
            label: '字段缺失',
            value: `${Object.values(thrMissing).reduce((a, b) => a + b, 0)} 条`,
            detail: `${detail}（缺字段按放行处理，未丢弃）`,
          });
        }
      }

      // v1.0.88 (sprint-38): 作者屏蔽丢弃 —— **独立**于上面「门槛过滤」。
      //
      // 为什么单列一行: 门槛是「内容不达标」(可调参数的产物), 作者屏蔽是
      // 「这个作者我不要了」(作者级永久黑名单) —— 两套心智模型, 混在一起
      // 会让「为什么少了 N 条」无法归因 (AC-8 / AC-19)。
      const authorBlocked = d.author_blocked ?? 0;
      if (authorBlocked > 0) {
        lines.push({ type: 'section', text: '作者屏蔽' });
        lines.push({
          type: 'item',
          label: '屏蔽丢弃',
          value: `丢弃 ${authorBlocked} 条`,
          detail: '命中已屏蔽作者，未入库',
        });
      }

      return lines;
    },
    // v1.0.5.24: date-range modal for bestblogs
    fetchDateModal: false,
    fetchTargetSource: '',
    fetchDateRange: 'days',  // 'auto' | 'days' | 'custom'
    fetchDateFrom: '',
    // v1.0.7.6: fetch progress modal + 5s polling state
    fetchProgressModal: false,
    fetchProgress: null,       // 当前焦点任务（粒子 modal 用）
    _fetchPollTimer: null,
    fetchBatchMode: false,  // 焦点任务是否为全部抓取
    // v1.0.28: multi-task list — 多源可并行；顶部像素 chip 列表
    fetchTasks: [],            // [{id, kind, job_id?, batch_id?, source, status, ...}]
    fetchChipLinger: false,    // 兼容旧绑定；以 task 级 linger 为主
    fetchChipLingerStatus: null,
    _fetchActiveJobId: null,
    _fetchActiveBatchId: null,
    _fetchChipLingerTimers: {}, // id -> timeout
    fetchDateTo: '',
    fetchDays: 7,
    // v1.0.5.24: copy URL toast
    copyToast: false,
    // v1.0.5.24: import fulltext
    importFilePath: '',
    importLoading: false,

    loadSources() {
      // v1.0.4: was /api/fetch/sources (404). Use /api/config/sources
      // which returns the same data shape (Source + tags).
      fetch('/api/config/sources', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          this.sources = Array.isArray(data) ? data : (data.sources || []);
        })
        .catch(err => { console.error('loadSources failed:', err); this.setFetchResult('加载失败: ' + err.message, null); });
    },

    runSource(id) {
      // v1.0.5.24: bestblogs needs date-range selection first
      // v1.0.6.5: huxiu also needs date-range selection
      const src = this.sources.find(s => s.id === id || s.name === id);
      const needsDateRange = ['bestblogs', 'huxiu'].includes(src?.name);
      if (src && needsDateRange) {
        this.fetchTargetSource = src.id;
        this.fetchDateModal = true;
        return;
      }
      // v1.0.28: 多任务并行，不覆盖其它任务的结果文案
      // v1.0.7.6: async fetch with progress modal + 5s polling
      this.startAsyncFetch(id);
    },

    confirmFetchDate() {
      this.fetchDateModal = false;
      let params = '';
      if (this.fetchDateRange === 'days') {
        const from = new Date(Date.now() - this.fetchDays * 86400000).toISOString().slice(0, 10);
        params = `?date_from=${from}`;
      } else if (this.fetchDateRange === 'custom') {
        const parts = [];
        if (this.fetchDateFrom) parts.push(`date_from=${this.fetchDateFrom}`);
        if (this.fetchDateTo) parts.push(`date_to=${this.fetchDateTo}`);
        if (parts.length) params = '?' + parts.join('&');
      }
      // auto: no params, fetcher uses last_run
      // v1.0.7.6: async fetch with progress modal + 5s polling
      // 不在启动时刷「正在抓取…」到结果区，避免未折叠的中间态占位
      this.startAsyncFetch(this.fetchTargetSource, params);
    },

    // ── v1.0.7.6 / v1.0.28: async fetch + multi-task polling ──────

    _taskSourceName(sourceRef) {
      const src = (this.sources || []).find(s => s.id === sourceRef || s.name === sourceRef);
      return src ? src.name : sourceRef;
    },

    _taskDisplayName(sourceRef) {
      const raw = sourceRef || '';
      const src = (this.sources || []).find(s => s.name === raw || s.id === raw);
      return src ? (src.display_name || src.name) : raw;
    },

    _upsertFetchTask(task) {
      const i = this.fetchTasks.findIndex(t => t.id === task.id);
      if (i >= 0) {
        const prev = this.fetchTasks[i];
        const merged = { ...prev, ...task };
        if (task.open === undefined) merged.open = prev.open;
        // Alpine: replace array for reactivity
        const next = this.fetchTasks.slice();
        next[i] = merged;
        this.fetchTasks = next;
      } else {
        if (task.open === undefined) task.open = false;
        this.fetchTasks = [task, ...this.fetchTasks];
      }
    },

    _removeFetchTask(id) {
      this.fetchTasks = this.fetchTasks.filter(t => t.id !== id);
      if (this._fetchChipLingerTimers[id]) {
        clearTimeout(this._fetchChipLingerTimers[id]);
        delete this._fetchChipLingerTimers[id];
      }
    },

    _focusTask(task) {
      if (!task) return;
      this.fetchProgress = { ...task };
      this.fetchBatchMode = task.kind === 'batch';
      this._fetchActiveJobId = task.job_id || null;
      this._fetchActiveBatchId = task.batch_id || null;
      if (task.kind === 'batch') this.fetchingAll = task.status === 'running';
    },

    _syncFocusFromTasks() {
      // 若焦点任务仍在列表，刷新其数据；否则切到第一个 running
      const focusId = this.fetchProgress?.id;
      if (focusId) {
        const t = this.fetchTasks.find(x => x.id === focusId);
        if (t) {
          this.fetchProgress = { ...t };
          this.fetchBatchMode = t.kind === 'batch';
          return;
        }
      }
      const run = this.fetchTasks.find(t => t.status === 'running');
      if (run) this._focusTask(run);
    },

    _ensureFetchPoll() {
      if (this._fetchPollTimer) return;
      const tick = () => { this._pollAllFetchTasks(); };
      tick();
      this._fetchPollTimer = setInterval(tick, 5000);
    },

    _stopFetchPollIfIdle() {
      const busy = this.fetchTasks.some(t => t.status === 'running');
      if (busy) return;
      if (this._fetchPollTimer) {
        clearInterval(this._fetchPollTimer);
        this._fetchPollTimer = null;
      }
    },

    async _pollAllFetchTasks() {
      const running = this.fetchTasks.filter(t => t.status === 'running');
      if (running.length === 0) {
        this._stopFetchPollIfIdle();
        return;
      }
      await Promise.all(running.map(t => this._pollOneFetchTask(t)));
      this._syncFocusFromTasks();
    },

    async _pollOneFetchTask(t) {
      try {
        if (t.kind === 'batch') {
          const r = await fetch(`/api/fetch/jobs/batch/${t.batch_id}`, { credentials: 'same-origin' });
          if (!r.ok) return;
          const data = await r.json();
          Object.assign(t, {
            status: data.status || t.status,
            total_sources: data.total_sources,
            done_sources: data.done_sources,
            current_source: data.current_source,
            current_feed: data.current_feed,
            articles_so_far: data.articles_so_far,
            elapsed_seconds: data.elapsed_seconds,
            results: data.results,
            errors: data.errors,
            failed_sources: data.failed_sources,
            batchMode: true,
          });
          // force Alpine reactivity
          this._upsertFetchTask({ ...t });

          const terminal = ['done', 'partial', 'failed'].includes(data.status);
          if (!terminal) {
            if (this.fetchProgressModal && this.fetchProgress?.id === t.id) {
              if (this._chainRows.length > 0) {
                const currentSrc = data.current_source || '';
                let idx = this._chainRows.findIndex(r => r.dbName === currentSrc);
                if (idx >= 0) {
                  this._layoutChain(idx);
                  this._flipStatus(`抓取中: ${this._chainRows[idx].name}`);
                  this._spawnBurst(idx, 8);
                }
              }
              this._updateFeedInfo(data.current_source || '', data.current_feed || '');
              this._animateArticleCount(data.articles_so_far || 0);
            }
            return;
          }

          // terminal batch
          this.fetchingAll = false;
        // 终态补一次计数: 快任务可能一次轮询就到终态, 动画没机会跑到最终值。
        this._animateArticleCount(data.articles_so_far || 0);
          const allResults = [
            ...(data.results || []),
            ...(data.errors || []).map(e => ({ source: e.source, error: e.error })),
          ];
          const totalInserted = (data.results || []).reduce((s, r) => s + (r.inserted || 0), 0);
          this.setFetchAllResult({
            status: data.status === 'failed' ? 'partial' : data.status,
            sources_total: data.total_sources,
            sources_ok: data.total_sources - (data.failed_sources || 0) - (data.partial_sources || 0),
            sources_failed: data.failed_sources || 0,
            sources_partial: data.partial_sources || 0,
            total_inserted: totalInserted,
            results: allResults,
          });
          if (this.fetchProgressModal && this.fetchProgress?.id === t.id) {
            this._finishFetchAnimation(true);
            const labelMap = { done: '完成', partial: '部分完成', failed: '失败' };
            this._flipStatus(labelMap[data.status] || data.status);
            const partialNote = data.partial_sources ? ` · ${data.partial_sources} 源部分完成` : '';
            this._showCompletion(
              `${data.total_sources || 0} 个源 · 新增 ${totalInserted} 篇 · 用时 ${data.elapsed_seconds || 0}s${partialNote}`,
              data.status === 'partial' ? '部分完成' : '抓取完成！',
            );
          }
          if (data.status === 'done' || data.status === 'partial') this.loadSources();
          this._scheduleTaskLinger(t, data.status);
          return;
        }

        // single job
        const r = await fetch(`/api/fetch/jobs/${t.job_id}`, { credentials: 'same-origin' });
        if (!r.ok) return;
        const data = await r.json();
        Object.assign(t, {
          status: data.status || t.status,
          source: data.source || t.source,
          done_feeds: data.done_feeds,
          total_feeds: data.total_feeds,
          current_feed: data.current_feed,
          articles_so_far: data.articles_so_far,
          elapsed_seconds: data.elapsed_seconds,
          result: data.result,
          error: data.error,
        });
        this._upsertFetchTask({ ...t });

        const terminal = ['done', 'partial', 'failed', 'timeout', 'stale', 'cancelled'].includes(data.status);
        if (!terminal) {
          if (this.fetchProgressModal && this.fetchProgress?.id === t.id && this.fetchBatchMode === false) {
            this._updateFeedInfo(data.source || t.source || '', data.current_feed || '');
            this._animateArticleCount(data.articles_so_far || 0);
            this._spawnBurst(0, 3);
          }
          return;
        }

        if (this.fetchProgressModal && this.fetchProgress?.id === t.id) {
          this._animateArticleCount(data.articles_so_far || 0);
          this._finishFetchAnimation(data.status !== 'done');
          const labelMap = { done: '完成', partial: '部分完成', failed: '失败', timeout: '超时', stale: '已中止' };
          this._flipStatus(labelMap[data.status] || data.status);
          if (data.status === 'done' || data.status === 'partial') {
            const r2 = data.result || {};
            const gap = data.status === 'partial' ? ` · ${data.error || '部分目标失败'}` : '';
            const summary = `${r2.inserted || 0} 篇 · 用时 ${data.elapsed_seconds || 0}s${gap}`;
            this._suckIntoMoni();
            this._showCompletion(
              summary,
              data.status === 'partial' ? '部分完成' : '抓取完成！',
            );
            this.loadSources();
          }
        } else if (data.status === 'done' || data.status === 'partial' || data.status === 'failed') {
          this.loadSources();
        }
        // surface last single result into result panel（默认折叠）
        if ((data.status === 'done' || data.status === 'partial') && data.result) {
          const text = typeof data.result === 'string'
            ? data.result
            : JSON.stringify(data.result, null, 2);
          this.setFetchResult(text, typeof data.result === 'object' ? data.result : null);
        } else if (data.error) {
          this.setFetchResult(String(data.error), null);
        }
        this._scheduleTaskLinger(t, data.status);
      } catch (err) {
        // network blip
      }
    },

    _scheduleTaskLinger(t, status) {
      t.status = status || t.status;
      this._upsertFetchTask({ ...t });
      if (this._fetchChipLingerTimers[t.id]) clearTimeout(this._fetchChipLingerTimers[t.id]);
      this._fetchChipLingerTimers[t.id] = setTimeout(() => {
        this._removeFetchTask(t.id);
        if (this.fetchProgress?.id === t.id && !this.fetchProgressModal) {
          const next = this.fetchTasks.find(x => x.status === 'running') || this.fetchTasks[0] || null;
          if (next) this._focusTask(next);
          else {
            this.fetchProgress = null;
            this.fetchBatchMode = false;
            this._fetchActiveJobId = null;
            this._fetchActiveBatchId = null;
          }
        }
        this._stopFetchPollIfIdle();
        delete this._fetchChipLingerTimers[t.id];
      }, 6000);
      this._stopFetchPollIfIdle();
    },

    _bootModalParticlesForFocus() {
      if (!this.fetchProgressModal || !this.isFetchRunning) return;
      if (!this.sources || this.sources.length === 0) this.loadSources();
      const boot = () => {
        if (!this.fetchProgressModal || !this.isFetchRunning) return;
        try {
          this._initParticleStage();
          const stage = document.getElementById('particleStage');
          const rect = stage ? stage.getBoundingClientRect() : null;
          if (!rect || rect.width < 10) return;
          let idx = 0;
          if (this.fetchBatchMode || this.fetchProgress?.kind === 'batch') {
            const cur = this.fetchProgress?.current_source || '';
            const found = this._chainRows.findIndex(r => r.dbName === cur);
            if (found >= 0) idx = found;
          }
          this._layoutChain(idx, false);
          this._spawnBurst(idx, 6);
          const p = this.fetchProgress || {};
          if (this.fetchBatchMode || p.kind === 'batch') {
            this._updateFeedInfo(p.current_source || '', p.current_feed || '');
            this._flipStatus(p.current_source ? `抓取中: ${p.current_source}` : '抓取中...');
          } else {
            this._updateFeedInfo(p.source || '', p.current_feed || '');
            this._flipStatus('抓取中...');
          }
          this._animateArticleCount(p.articles_so_far || 0);
        } catch (err) {
          console.error('particle boot failed:', err);
        }
      };
      requestAnimationFrame(() => requestAnimationFrame(boot));
    },

    async startAsyncFetch(sourceRef, queryParams = '') {
      // v1.0.28: 不清理其它任务 — 多源可并行（后端按 source 409）
      const sourceName = this._taskSourceName(sourceRef);
      if (this.isSourceFetching({ id: sourceRef, name: sourceName })) {
        this._notify('info', `${this._taskDisplayName(sourceName)} 已在抓取中`);
        return;
      }

      this.fetchBatchMode = false;
      // 首个任务打开进度弹窗；并行追加时只更新列表，不打断当前弹窗
      const hadRunning = this.fetchTasks.some(t => t.status === 'running');
      const pending = {
        id: `pending-${sourceName}-${Date.now()}`,
        kind: 'single',
        job_id: null,
        source: sourceName,
        status: 'running',
        done_feeds: 0,
        total_feeds: 0,
        current_feed: '(初始化中)',
        articles_so_far: 0,
        elapsed_seconds: 0,
        open: false,
      };
      this._upsertFetchTask(pending);
      this._focusTask(pending);
      if (!hadRunning) {
        this.fetchProgressModal = true;
        this._bootModalParticlesForFocus();
      }

      try {
        const r = await fetch(`/api/fetch/sources/${sourceRef}/run${queryParams}`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const data = await r.json();
        if (r.status === 409) {
          const jid = data?.detail?.running_job_id;
          this._removeFetchTask(pending.id);
          if (jid) {
            const task = {
              id: jid,
              kind: 'single',
              job_id: jid,
              source: sourceName,
              status: 'running',
              done_feeds: 0,
              total_feeds: 0,
              current_feed: '已有抓取任务在进行中',
              articles_so_far: 0,
              elapsed_seconds: 0,
            };
            this._upsertFetchTask(task);
            this._focusTask(task);
            this._ensureFetchPoll();
          }
          return;
        }
        if (!r.ok || !data.job_id) {
          this._removeFetchTask(pending.id);
          const msg = data?.detail?.message || data?.error || `HTTP ${r.status}`;
          this.setFetchResult(msg, null);
          this._notify('error', msg);
          return;
        }
        this._removeFetchTask(pending.id);
        const task = {
          id: data.job_id,
          kind: 'single',
          job_id: data.job_id,
          source: data.source || sourceName,
          status: 'running',
          done_feeds: 0,
          total_feeds: 0,
          current_feed: '(初始化中)',
          articles_so_far: 0,
          elapsed_seconds: 0,
          open: false,
        };
        this._upsertFetchTask(task);
        this._focusTask(task);
        this._ensureFetchPoll();
      } catch (err) {
        this._removeFetchTask(pending.id);
        const msg = '启动抓取失败: ' + err.message;
        this.setFetchResult(msg, null);
        this._notify('error', msg);
      }
    },

    async fetchAllSources() {
      if (this.isFetchRunning) {
        this._notify('info', '已有抓取任务进行中，请结束后再全部抓取');
        return;
      }
      this.fetchingAll = true;
      this.fetchAllResult = null;
      this.fetchAllResultKey = (this.fetchAllResultKey || 0) + 1;
      this.fetchResult = '';
      this.fetchResultObj = null;
      this.fetchResultKey = (this.fetchResultKey || 0) + 1;
      this.fetchBatchMode = true;
      this.fetchProgressModal = true;
      const pending = {
        id: `pending-batch-${Date.now()}`,
        kind: 'batch',
        batch_id: null,
        source: '*',
        status: 'running',
        total_sources: 0,
        done_sources: 0,
        current_source: '(初始化中)',
        current_feed: '',
        articles_so_far: 0,
        elapsed_seconds: 0,
        batchMode: true,
      };
      this._upsertFetchTask(pending);
      this._focusTask(pending);
      this._bootModalParticlesForFocus();

      try {
        const r = await fetch('/api/fetch/all/run', { method: 'POST', credentials: 'same-origin' });
        const data = await r.json();
        if (!r.ok || !data.batch_id) {
          this._removeFetchTask(pending.id);
          this.fetchingAll = false;
          const msg = data?.detail?.message || data?.error || `HTTP ${r.status}`;
          this.setFetchResult(msg, null);
          this._notify('error', msg);
          return;
        }
        this._removeFetchTask(pending.id);
        const task = {
          id: data.batch_id,
          kind: 'batch',
          batch_id: data.batch_id,
          source: '*',
          status: 'running',
          total_sources: data.sources_total || 0,
          done_sources: 0,
          current_source: '(初始化中)',
          current_feed: '',
          articles_so_far: 0,
          elapsed_seconds: 0,
          batchMode: true,
        };
        this._upsertFetchTask(task);
        this._focusTask(task);
        this._ensureFetchPoll();
      } catch (err) {
        this._removeFetchTask(pending.id);
        this.fetchingAll = false;
        const msg = '启动全部抓取失败: ' + err.message;
        this.setFetchResult(msg, null);
        this._notify('error', msg);
      }
    },

    async openRecentBatch() {
      if (this.isFetchRunning) {
        this._notify('info', '抓取进行中，结束后再查看上次结果');
        return;
      }
      try {
        const r = await fetch('/api/fetch/jobs/batch/recent', { credentials: 'same-origin' });
        if (!r.ok) {
          this.showToast('暂无抓取历史');
          return;
        }
        const { batch_id } = await r.json();
        const task = {
          id: batch_id,
          kind: 'batch',
          batch_id,
          source: '*',
          status: 'running',
          total_sources: 0,
          done_sources: 0,
          current_source: '(加载中)',
          current_feed: '',
          articles_so_far: 0,
          elapsed_seconds: 0,
          batchMode: true,
        };
        this._upsertFetchTask(task);
        this._focusTask(task);
        this.fetchProgressModal = true;
        this._bootModalParticlesForFocus();
        this._ensureFetchPoll();
      } catch (err) {
        this.showToast('打开上次结果失败: ' + err.message);
      }
    },

    // v1.0.28 ── multi-task helpers ────────────────────────────────
    get isFetchRunning() {
      if (this.fetchTasks.some(t => t.status === 'running')) return true;
      if (this.fetchingAll) return true;
      return false;
    },

    get runningFetchTasks() {
      return this.fetchTasks.filter(t => t.status === 'running');
    },

    taskLabel(t) {
      if (!t) return '';
      if (t.kind === 'batch') return '全部抓取';
      return this._taskDisplayName(t.source) || t.source || '信源抓取';
    },

    taskAmount(t) {
      if (!t) return '';
      if (t.kind === 'batch') {
        const d = t.done_sources || 0;
        const n = t.total_sources || 0;
        if (n) return `${d}/${n} 源`;
        return t.articles_so_far ? `${t.articles_so_far} 篇` : '…';
      }
      const done = t.done_feeds || 0;
      const total = t.total_feeds || 0;
      if (total) return `${done}/${total} feed`;
      if (t.articles_so_far) return `${t.articles_so_far} 篇`;
      return this.formatTaskElapsed(t);
    },

    taskPillLabel(t) {
      if (!t) return '';
      if (t.status === 'running') return '';
      const map = {
        done: '完成',
        partial: '部分完成',
        failed: '失败',
        timeout: '超时',
        stale: '已中止',
        cancelled: '已停止',
      };
      return map[t.status] || t.status;
    },

    taskPillTone(t) {
      if (!t) return '';
      if (t.status === 'done') return 'green';
      if (t.status === 'partial') return 'amber';
      if (['failed', 'timeout', 'stale', 'cancelled'].includes(t.status)) return 'red';
      return '';
    },

    taskDetails(t) {
      if (!t) return [];
      if (t.kind === 'batch') {
        const rows = [
          { label: '当前源', meta: t.current_source || '—' },
          { label: '当前 feed', meta: t.current_feed || '—' },
          {
            label: '源进度',
            meta: `${t.done_sources || 0}/${t.total_sources || 0}`,
          },
          { label: '已抓文章', meta: String(t.articles_so_far || 0) },
          { label: '耗时', meta: this.formatTaskElapsed(t) },
        ];
        if (t.status !== 'running' && t.results) {
          const ok = (t.results || []).filter(r => !r.error).length;
          rows.push({ label: '成功源', meta: String(ok) });
        }
        return rows;
      }
      const rows = [
        { label: '当前 feed', meta: t.current_feed || '—' },
        {
          label: 'feed 进度',
          meta: `${t.done_feeds || 0}/${t.total_feeds || 0}`,
        },
        { label: '已抓文章', meta: String(t.articles_so_far || 0) },
        { label: '耗时', meta: this.formatTaskElapsed(t) },
      ];
      if (t.result && typeof t.result === 'object') {
        if (t.result.inserted != null) rows.push({ label: '入库', meta: String(t.result.inserted) });
        if (t.result.fetched != null) rows.push({ label: '拉取', meta: String(t.result.fetched) });
      }
      if (t.error) rows.push({ label: '错误', meta: String(t.error).slice(0, 80) });
      return rows;
    },

    toggleTaskOpen(t) {
      if (!t) return;
      t.open = !t.open;
      this._upsertFetchTask({ ...t });
    },

    taskIndexBadge(t) {
      // 1-based index among current list for spinner ring number
      const i = this.fetchTasks.findIndex(x => x.id === t.id);
      return i >= 0 ? i + 1 : 1;
    },


    fetchResultHeadline() {
      // 折叠条上一行结论
      if (this.fetchResultObj) {
        try {
          const lines = this.formatFetchResult() || [];
          const head = lines.find(l => l && (l.type === 'success' || l.type === 'warn' || l.type === 'error' || l.type === 'muted'));
          if (head && head.text) return head.text;
          const src = this.fetchResultObj.source || '';
          const ins = this.fetchResultObj.inserted;
          if (src) return `${src} · 入库 ${ins ?? '—'}`;
        } catch (_) { /* ignore */ }
      }
      const s = String(this.fetchResult || '').trim();
      if (!s) return '抓取结果';
      const one = s.split(/\n/)[0];
      return one.length > 72 ? one.slice(0, 72) + '…' : one;
    },

    setFetchResult(text, obj = null) {
      // 递增 key → x-for remount 出全新 details（无 open）= 默认折叠
      this.fetchResultObj = (obj && typeof obj === 'object') ? obj : null;
      this.fetchResult = text == null ? '' : String(text);
      this.fetchResultKey = (this.fetchResultKey || 0) + 1;
      this._forceCloseFetchDetails();
    },

    setFetchAllResult(payload) {
      this.fetchAllResult = payload || null;
      this.fetchAllResultKey = (this.fetchAllResultKey || 0) + 1;
      this._forceCloseFetchDetails();
    },

    _forceCloseFetchDetails() {
      const closeAll = () => {
        try {
          document.querySelectorAll(
            '.fetch-ops-body details.fetch-summary-details, '
            + '.fetch-ops-body details.fetch-raw-details, '
            + '.fetch-ops-body details.fa-row-details, '
            + '.fetch-ops-body details.fetch-all-details'
          ).forEach((d) => { d.removeAttribute('open'); d.open = false; });
        } catch (_) { /* ignore */ }
      };
      queueMicrotask(closeAll);
      if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(() => requestAnimationFrame(closeAll));
      } else {
        setTimeout(closeAll, 16);
      }
    },

    _collapseFetchResultPanels() {
      // 兼容旧调用点：仅强制关闭当前 DOM 中的 details
      this._forceCloseFetchDetails();
    },

    formatTaskElapsed(t) {
      const sec = Number(t?.elapsed_seconds);
      if (!Number.isFinite(sec) || sec < 0) return '0s';
      if (sec < 60) return `${Math.round(sec)}s`;
      const m = Math.floor(sec / 60);
      const s = Math.round(sec % 60);
      return `${m}m ${s}s`;
    },

    // Drive chip 文案：多任务显示数量，单任务显示源名
    fetchRunningLabel() {
      const running = this.fetchTasks.filter(t => t.status === 'running');
      if (running.length > 1) return `${running.length} 个进行中`;
      const t = running[0]
        || this.fetchTasks.find(x => x.id === this.fetchProgress?.id)
        || this.fetchTasks[0];
      if (!t) return '抓取中';
      return this.taskLabel(t) || '抓取中';
    },

    formatFetchElapsed() {
      const t = this.fetchTasks.find(x => x.id === this.fetchProgress?.id)
        || this.fetchTasks.find(x => x.status === 'running')
        || this.fetchProgress;
      return this.formatTaskElapsed(t);
    },

    isSourceFetching(s) {
      if (!s) return false;
      if (this.fetchTasks.some(t => t.kind === 'batch' && t.status === 'running')) return true;
      return this.fetchTasks.some(t =>
        t.status === 'running' && t.kind === 'single'
        && (t.source === s.name || t.source === s.id || String(t.source) === String(s.id))
      );
    },

    focusFetchTask(t) {
      if (!t) return;
      this._focusTask(t);
      this.fetchProgressModal = true;
      if (t.status === 'running') {
        // 关窗再开：等 modal 显示后再挂粒子，避免 0 尺寸 stage
        this.$nextTick(() => {
          if (this.fetchProgressModal && this.isFetchRunning) {
            this._bootModalParticlesForFocus();
          }
        });
      }
    },

    reopenFetchProgress() {
      // 关 modal 后的主入口：点 Drive chip 重新打开进度
      const t = this.fetchTasks.find(x => x.id === this.fetchProgress?.id && x.status === 'running')
        || this.fetchTasks.find(x => x.status === 'running')
        || this.fetchTasks.find(x => x.id === this.fetchProgress?.id)
        || this.fetchTasks[0];
      if (!t) {
        if (typeof this._notify === 'function') this._notify('info', '当前没有可查看的抓取任务');
        else if (typeof this.showToast === 'function') this.showToast('当前没有可查看的抓取任务');
        return;
      }
      this.focusFetchTask(t);
    },

    async cancelFetchTask(t) {
      // Soft-cancel running job (zombie after restart / hang). Unblocks 立即抓取.
      if (!t) return;
      const jid = t.job_id || (t.kind === 'single' ? t.id : null);
      if (!jid || t.kind === 'batch') {
        // batch: cancel each? For now only single-source cancel is wired.
        if (t.kind === 'batch' && t.batch_id) {
          this._notify('info', '全部抓取暂不支持一键停止，请等超时或刷新后重试');
          return;
        }
        this._notify('info', '无法停止：缺少 job id');
        return;
      }
      const ok = await moniModal.confirm(
        `停止「${this.taskLabel(t)}」的抓取？\n卡住的僵尸任务可立即解除；若线程仍在跑，结束后可能仍会写回结果。`,
        { title: '停止抓取', okLabel: '停止', danger: true }
      );
      if (!ok) return;
      try {
        const r = await fetch(`/api/fetch/jobs/${encodeURIComponent(jid)}/cancel`, {
          method: 'POST',
          credentials: 'same-origin',
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          const msg = data?.detail?.message || data?.message || `HTTP ${r.status}`;
          this._notify('error', '停止失败: ' + msg);
          return;
        }
        this._upsertFetchTask({
          ...t,
          status: 'cancelled',
          error: 'cancelled by user',
        });
        this._scheduleTaskLinger({ ...t, status: 'cancelled' }, 'cancelled');
        this._notify('info', '已停止抓取');
        if (this.fetchProgress?.id === t.id) {
          this.fetchProgress = { ...this.fetchProgress, status: 'cancelled' };
        }
      } catch (err) {
        this._notify('error', '停止失败: ' + (err?.message || err));
      }
    },

    async resumeRunningFetchJobs() {
      if (this.fetchTasks.some(t => t.status === 'running') && this._fetchPollTimer) return;
      try {
        const r = await fetch('/api/fetch/jobs?status=running&limit=20', {
          credentials: 'same-origin',
        });
        if (!r.ok) return;
        const data = await r.json();
        const jobs = Array.isArray(data.jobs) ? data.jobs : [];
        if (jobs.length === 0) return;

        const batchSeen = new Set();
        for (const j of jobs) {
          if (j.batch_id) {
            if (batchSeen.has(j.batch_id)) continue;
            batchSeen.add(j.batch_id);
            this._upsertFetchTask({
              id: j.batch_id,
              kind: 'batch',
              batch_id: j.batch_id,
              source: '*',
              status: 'running',
              total_sources: 0,
              done_sources: 0,
              current_source: j.source || '',
              articles_so_far: j.articles_so_far || 0,
              elapsed_seconds: j.elapsed_seconds || 0,
              batchMode: true,
            });
          } else {
            this._upsertFetchTask({
              id: j.id,
              kind: 'single',
              job_id: j.id,
              source: j.source,
              status: j.status || 'running',
              done_feeds: j.done_feeds || 0,
              total_feeds: j.total_feeds || 0,
              current_feed: j.current_feed || '',
              articles_so_far: j.articles_so_far || 0,
              elapsed_seconds: j.elapsed_seconds || 0,
            });
          }
        }
        if (!this.fetchProgress && this.fetchTasks.length) {
          this._focusTask(this.fetchTasks[0]);
        }
        this.fetchProgressModal = false;
        this._ensureFetchPoll();
      } catch (err) {
        console.error('resumeRunningFetchJobs failed:', err);
      }
    },

    closeFetchProgress() {
      // v1.0.28: 只关 UI；running 任务继续 multi-poll
      this.fetchProgressModal = false;
      if (!this.isFetchRunning) {
        this.fetchBatchMode = false;
        this._stopFetchPollIfIdle();
      }
      if (this._particleInitTimer) {
        clearTimeout(this._particleInitTimer);
        this._particleInitTimer = null;
      }
      this._finishFetchAnimation(true);
      // v1.0.44: window.gsap guard, no try/catch — a bare `gsap.` here throws
      // ReferenceError when the CDN hasn't landed (deferred script still in
      // flight), which would abort closeFetchProgress entirely.
      const mi = document.getElementById('moniIcon');
      if (mi && window.gsap) {
        window.gsap.killTweensOf(mi);
      }
    },

    // v1.0.9.8: particle animation + source chain for fetch modal
    // v1.0.13: stop gating — _particleRunning + tracked timers prevent late
    // bursts from leaking after completion (fetch modal was animating forever
    // because _spawnBurst() schedules setTimeout per particle and the
    // completion handler ran AFTER the spawn).
    _particles: [],
    _animFrame: null,
    _chainRows: [],          // [{el, idx, name, icon, color}]
    _moniLeft: 0,
    _moniY: 0,
    _particleRunning: false,
    _particleBurstTimers: new Set(),   // every setTimeout from _spawnBurst
    _particleInitTimer: null,           // setTimeout retry from initStageWithSources

    _initParticleStage() {
      const stage = document.getElementById('particleStage');
      const chain = document.getElementById('sourceChain');
      const moniIcon = document.getElementById('moniIcon');
      const canvas = document.getElementById('particleCanvas');
      if (!stage || !chain || !moniIcon || !canvas) return;

      // v1.0.28: modal 刚 x-show 时 stage 可能仍是 0×0（关窗重开常见）。
      // 此时定位 moni/chain 会挤到左上角且 canvas 无有效尺寸 → 粒子「没了」。
      const rect0 = stage.getBoundingClientRect();
      if (rect0.width < 10 || rect0.height < 10) {
        if (this._particleInitTimer) clearTimeout(this._particleInitTimer);
        this._particleInitTimer = setTimeout(() => {
          this._particleInitTimer = null;
          if (this.fetchProgressModal) {
            this._initParticleStage();
            // 补一次 chain focus（重开路径）
            if (this._chainRows && this._chainRows.length) {
              let idx = 0;
              if (this.fetchBatchMode || this.fetchProgress?.batchMode) {
                const cur = this.fetchProgress?.current_source || '';
                const found = this._chainRows.findIndex(r => r.dbName === cur);
                if (found >= 0) idx = found;
              }
              this._layoutChain(idx, false);
              this._spawnBurst(idx, 4);
            }
          }
        }, 50);
        return;
      }

      // Stop any prior animation (also clears _particleRunning + timers)
      this._stopParticleAnimation();

      // Mark running BEFORE scheduling any timer/RAF
      this._particleRunning = true;

      // Resize canvas (DPR-aware)
      const rect = stage.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      canvas.style.width = rect.width + 'px';
      canvas.style.height = rect.height + 'px';
      canvas.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);

      // Position moni (right side, vertically centered at 40% of stage height)
      const stageW = rect.width;
      const stageH = rect.height;
      // Source icon center is determined by chain layout: chainW=45%, row right=16px
      // Increase gap: shift source left by 40px, moni right by 40px (still centered)
      const gapShift = 40;
      const actualSrcCx = stageW * 0.45 - (16 + gapShift) - 48;
      const moniCx = stageW - actualSrcCx;
      this._moniLeft = moniCx - 48 + 2;            // 48 = moni half-width, +2 right shift
      this._moniY = stageH * 0.4 - 48;         // 48 = moni half-height; 0.4 = above-center
      // Use a container div with flex-column to match source row layout
      let moniContainer = document.getElementById('moniContainer');
      if (!moniContainer) {
        moniContainer = document.createElement('div');
        moniContainer.id = 'moniContainer';
        moniContainer.className = 'source-row';
        moniContainer.style.position = 'absolute';
        moniContainer.style.right = 'auto';
        // Copy children from existing moni elements
        const stageEl = document.getElementById('particleStage');
        if (stageEl) {
          // Move moniIcon + moniLabel inside container
          const mi = document.getElementById('moniIcon');
          const ml = document.getElementById('moniLabel');
          if (mi && ml) {
            mi.removeAttribute('style');
            mi.style.position = 'relative';
            mi.style.top = 'auto';
            mi.style.left = 'auto';
            ml.removeAttribute('style');
            ml.style.position = 'relative';
            ml.style.top = 'auto';
            ml.style.left = 'auto';
            moniContainer.appendChild(ml);
            moniContainer.appendChild(mi);
          }
          stageEl.appendChild(moniContainer);
        }
      }
      // Position container so its center aligns with pairCenterY
      const containerCenterY = stageH * 0.4;
      const containerTop = containerCenterY - 60; // 60 ≈ (23+4+96)/2
      moniContainer.style.transform = `translate(${this._moniLeft}px, ${containerTop}px)`;
      moniIcon.classList.add('receiving');

      // Build chain from active sources
      const sources = this._activeSourcesForModal();
      chain.innerHTML = '';
      this._chainRows = [];
      sources.forEach((src, i) => {
        const row = document.createElement('div');
        row.className = 'source-row';
        row.dataset.idx = i;
        row.innerHTML = `
          <span class="source-label">${this._escapeHtml(src.label)}</span>
          <div class="source-icon"><img src="${src.icon}" alt="${this._escapeHtml(src.label)}"></div>
        `;
        chain.appendChild(row);
        // Override CSS right to shift sources left (maintains center alignment with moni)
        row.style.right = (16 + gapShift) + 'px';
        this._chainRows.push({ el: row, idx: i, name: src.label, dbName: src.dbName, color: src.color });
      });

      // Initial layout: all below center, transparent
      const centerY = stageH * 0.4;
      this._chainRows.forEach((row, i) => {
        const startCenterY = centerY + 60 + i * 50;
        const rowH = 96;
        const startY = startCenterY - rowH / 2;
        const startStyle = { y: startY, opacity: 0, scale: 0.7, transformOrigin: 'right center' };
        // v1.0.44: gsap may still be in flight (deferred CDN). A bare `gsap.set`
        // here threw ReferenceError and killed the whole particle stage; the
        // CSS fallback below reproduces the same initial state without GSAP.
        if (window.gsap) {
          window.gsap.set(row.el, startStyle);
        } else {
          row.el.style.opacity = '0';
          row.el.style.transformOrigin = 'right center';
          row.el.style.transform = `translateY(${startY}px) scale(0.7)`;
        }
      });

      // Start rAF + continuous burst loop for sustained particles
      this._particles = [];
      // Only kick the RAF loop if running — _stopParticleAnimation already
      // flipped the gate to false during re-init, so re-arm it FIRST.
      this._particleRunning = true;
      this._animFrame = requestAnimationFrame(() => this._animateParticles());
      // Burst from current focus every 200ms; if no focus, burst all
      if (this._burstTimer) clearInterval(this._burstTimer);
      this._burstTimer = setInterval(() => {
        if (!this.fetchProgressModal) return;
        // Find focused row
        const focusIdx = this._chainRows.findIndex(r => r.el.classList.contains('focus'));
        if (focusIdx >= 0) {
          this._spawnBurst(focusIdx, 6);
        } else {
          this._chainRows.forEach((_, i) => this._spawnBurst(i, 2));
        }
      }, 200);
    },

    _stopParticleAnimation() {
      // Gate FIRST so any in-flight RAF/timer callbacks bail on their next run.
      this._particleRunning = false;
      if (this._animFrame) {
        cancelAnimationFrame(this._animFrame);
        this._animFrame = null;
      }
      if (this._burstTimer) {
        clearInterval(this._burstTimer);
        this._burstTimer = null;
      }
      // Cancel every pending burst setTimeout (otherwise late callbacks
      // push particles into the array after we cleared it).
      if (this._particleBurstTimers) {
        this._particleBurstTimers.forEach((tid) => clearTimeout(tid));
        this._particleBurstTimers.clear();
      }
      // Cancel the initStageWithSources retry timer if mid-flight.
      if (this._particleInitTimer) {
        clearTimeout(this._particleInitTimer);
        this._particleInitTimer = null;
      }
      this._particles = [];
      const moniIcon = document.getElementById('moniIcon');
      if (moniIcon) moniIcon.classList.remove('receiving');
      const overlay = document.getElementById('completionOverlay');
      if (overlay) overlay.classList.remove('show');
      const stage = document.getElementById('particleStage');
      const canvas = stage ? stage.querySelector('canvas') : null;
      if (canvas) {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
    },

    // v1.0.13: unified terminal-state handler. Call from any polling branch
    // (single / batch) before the visual completion overlay shows, so the
    // background is quiet by the time the modal reads "完成".
    //
    // `killGsapRows` defaults to false because the single-source "done"
    // path runs _suckIntoMoni() AFTER this — that gsap shrink-into-moni
    // tween is intentional and must NOT be killed. For failed / timeout /
    // stale / partial / close-fetch-progress, pass true.
    _finishFetchAnimation(killGsapRows = false) {
      this._stopParticleAnimation();
      if (killGsapRows && window.gsap) {
        // v1.0.44: window.gsap-qualified; the previous bare `gsap.killTweensOf`
        // inside try/catch still threw ReferenceError before the catch could
        // help if the deferred CDN script hadn't executed yet.
        (this._chainRows || []).forEach((row) => {
          if (row && row.el) window.gsap.killTweensOf(row.el);
        });
        // Don't kill moniIcon tweens — yoyo on _suckIntoMoni lands the
        // bounce; only the closeFetchProgress path should reset it.
      }
    },

    _activeSourcesForModal() {
      // v1.0.13: xwatch (X 关键词监控) gets its own line-art icon
      // — was previously falling through to the 36kr fallback, which
      // mis-identified the source on the fetch-modal chain.
      const iconMap = {
        '36kr_rss': '/static/icons/sources/36kr.png',
        'bestblogs': '/static/icons/sources/bestblog-gzh.png',
        'bestblog-podcast': '/static/icons/sources/bestblogs.png',
        'bestblog-x': '/static/icons/sources/bestblog-x.png',
        'bestblog-yt': '/static/icons/sources/bestblog-yt.png',
        'huxiu': '/static/icons/sources/huxiu.png',
        'mpaypass': '/static/icons/sources/mpaypass.png',
        'qbitai': '/static/icons/sources/qbitai.png',
        'wechat_rss_a': '/static/icons/sources/wechat_rss.png',
        'wechat_rss_b': '/static/icons/sources/wechat_rss_b.png',
        'xwatch': '/static/icons/sources/xwatch.png',
        // v1.0.66: xbot_feed (X-bot 精选) — 蓝色 X 徽标, 跟 xwatch 的钴蓝
        // 同族但更亮一档, 抓取链上两者挨着放。
        'xbot_feed': '/static/icons/sources/xbot_feed.png',
      };
      const labelMap = {
        '36kr_rss': '36氪', 'bestblogs': 'bb公众号', 'bestblog-podcast': 'bb博客',
        'bestblog-x': 'bb X', 'bestblog-yt': 'bb YT',
        'huxiu': '虎嗅', 'mpaypass': '移动支付网', 'qbitai': '量子位',
        'wechat_rss_a': 'wechatrss-A', 'wechat_rss_b': 'wechatrss-B',
        'xwatch': 'X 关键词',
        'xbot_feed': 'X-bot 精选',
      };
      const colorMap = {
        '36kr_rss': [90,140,220], 'bestblogs': [60,180,170], 'bestblog-podcast': [60,180,170],
        'bestblog-x': [60,180,170], 'bestblog-yt': [60,180,170],
        'huxiu': [210,140,60], 'mpaypass': [180,150,60], 'qbitai': [140,80,200],
        'wechat_rss_a': [70,170,100], 'wechat_rss_b': [70,170,100],
        // v1.0.13: X icon is cobalt blue (#1E40AF) line art — keep the
        // particle accent in the same blue-grey family so the receiving
        // burst blends with the source icon. Earlier draft used X-brand
        // red [220,90,90] which clashed with the cobalt stroke.
        'xwatch': [30, 64, 175],
        // v1.0.66: xbot_feed 用天蓝 [56,132,225] — 比 xwatch 的深钴蓝亮
        // 一档, 两者在抓取链上相邻但不撞色。
        'xbot_feed': [56, 132, 225],
      };

      if (this.fetchBatchMode) {
        // Custom sort order for batch fetch chain.
        // v1.0.13: xwatch slotted between bestblog-x and bestblog-yt to
        // keep "X-flavored" sources grouped on the right side of the chain.
        // v1.0.66 审计 S-4: xbot_feed 移到 bestblog-yt 之后单列 —— 它是
        // **导入型**源 (同步上游已筛好的 feed), 不是主动抓取节点, 挨着
        // xwatch (X 关键词监控) 会让人误以为它在抓取链上主动抓。
        const sortOrder = [
          'wechat_rss_a', 'wechat_rss_b', 'bestblogs', 'qbitai', 'mpaypass',
          '36kr_rss', 'bestblog-podcast', 'bestblog-x', 'xwatch',
          'bestblog-yt', 'xbot_feed', 'huxiu',
        ];
        return (this.sources || []).filter(s => s.is_active)
          .sort((a, b) => {
            const ia = sortOrder.indexOf(a.name);
            const ib = sortOrder.indexOf(b.name);
            return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
          })
          .map(s => ({
          label: labelMap[s.name] || s.display_name || s.name,
          dbName: s.name,
          icon: iconMap[s.name] || '/static/icons/sources/36kr.png',
          color: colorMap[s.name] || [140,120,200],
        }));
      }
      // Single-source: resolve the source reference to its actual name
      const srcRef = this.fetchProgress?.source || '';
      // Try multiple ways to find the source object
      let srcObj = (this.sources || []).find(s => s.name === srcRef);
      if (!srcObj) srcObj = (this.sources || []).find(s => s.id === srcRef);
      if (!srcObj) srcObj = (this.sources || []).find(s => s.display_name === srcRef);
      const srcName = srcObj ? srcObj.name : srcRef;
      const label = labelMap[srcName] || (srcObj ? (srcObj.display_name || srcObj.name) : srcRef);
      return [{
        label: label || srcRef || '...',
        dbName: srcName,
        icon: iconMap[srcName] || '/static/icons/sources/36kr.png',
        color: colorMap[srcName] || [140,120,200],
      }];
    },

    _escapeHtml(s) {
      return String(s || '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[c]));
    },

    _layoutChain(activeIdx, animate = true) {
      const stage = document.getElementById('particleStage');
      if (!stage) return;
      const stageH = stage.getBoundingClientRect().height;
      const pairCenterY = stageH * 0.4;
      const duration = animate ? 0.5 : 0;
      const ease = 'power2.inOut';
      this._chainRows.forEach((row, i) => {
        const rowH = i === activeIdx ? 120 : 96;
        let targetCenterY, targetOpacity, targetScale;
        if (i < activeIdx) {
          const dist = activeIdx - i;
          targetCenterY = pairCenterY - 50 - dist * 55;
          targetOpacity = 0;
          targetScale = 0.7;
        } else if (i === activeIdx) {
          targetCenterY = pairCenterY;
          targetOpacity = 1;
          targetScale = 1;
        } else {
          const dist = i - activeIdx;
          targetCenterY = pairCenterY + 120 + (dist - 1) * 90;
          targetOpacity = dist === 1 ? 0.75 : Math.max(0.08, 0.6 - dist * 0.16);
          targetScale = dist === 1 ? 0.85 : Math.max(0.65, 0.82 - dist * 0.06);
        }
        const targetTop = targetCenterY - rowH / 2;
        const targetStyle = {
          y: targetTop,
          opacity: targetOpacity,
          scale: targetScale,
          duration: duration,
          ease: ease,
          overwrite: 'auto',
        };
        // v1.0.44: bare `gsap.to` threw ReferenceError when the deferred CDN
        // script hadn't landed, aborting _layoutChain — the chain rows would
        // stay stuck at their initial (invisible) state. Fall back to setting
        // the same end state instantly so the layout is still correct.
        if (window.gsap) {
          window.gsap.to(row.el, targetStyle);
        } else {
          row.el.style.opacity = String(targetOpacity);
          row.el.style.transform = `translateY(${targetTop}px) scale(${targetScale})`;
        }
        row.el.classList.toggle('focus', i === activeIdx);
      });
    },

    _animateParticles() {
      const stage = document.getElementById('particleStage');
      const canvas = document.getElementById('particleCanvas');
      if (!stage || !canvas || !this._particleRunning) {
        this._animFrame = null;
        return;
      }
      const ctx = canvas.getContext('2d');
      const rect = stage.getBoundingClientRect();
      ctx.clearRect(0, 0, rect.width, rect.height);

      // Update + draw particles
      this._particles = this._particles.filter(p => p.alive);
      this._particles.forEach(p => {
        p.update();
        p.draw(ctx);
      });

      // Only schedule next frame if still running — otherwise the RAF queue
      // could keep the loop alive for one more frame after stop().
      if (this._particleRunning) {
        this._animFrame = requestAnimationFrame(() => this._animateParticles());
      } else {
        this._animFrame = null;
      }
    },

    _spawnBurst(idx, count = 4) {
      // Bail out if animation was stopped (race with terminal status).
      if (!this._particleRunning) return;
      const row = this._chainRows[idx];
      const stage = document.getElementById('particleStage');
      const moniIcon = document.getElementById('moniIcon');
      if (!row || !stage || !moniIcon) return;
      const sr = stage.getBoundingClientRect();
      const ir = row.el.querySelector('.source-icon').getBoundingClientRect();
      const mr = moniIcon.getBoundingClientRect();
      const sx = ir.left - sr.left + ir.width;
      const sy = ir.top - sr.top + ir.height / 2;
      const tx = mr.left - sr.left;
      const ty = mr.top - sr.top + mr.height / 2;
      // Multiply count by 1.3 (overall particle density increase)
      const c = Math.ceil(count * 2.6);
      for (let i = 0; i < c; i++) {
        const tid = setTimeout(() => {
          // Track + clear from set so stop() can cancel pending bursts.
          this._particleBurstTimers.delete(tid);
          // Bail if animation was stopped while this timer was queued.
          if (!this._particleRunning) return;
          // v1.0.45 (sprint-25 A1-a): cap at birth, not in the per-frame
          // filter. Bursts fire every 200 ms and each particle lives ~70-110
          // frames, so the array grew without bound the longer the modal
          // stayed open — every extra particle cost one update() plus up to
          // 7 arc() draw calls per frame, forever. Evicting the OLDEST keeps
          // the visible density stable (the newest ones are the ones still
          // travelling toward moni) while bounding the per-frame cost.
          if (this._particles.length >= MAX_PARTICLES) {
            this._particles.shift();
          }
          const p = new FetchParticle(
            sx + (Math.random() - 0.5) * 4,
            sy + (Math.random() - 0.5) * ir.height * 0.6,
            tx + (Math.random() - 0.5) * 4,
            ty + (Math.random() - 0.5) * mr.height * 0.6
          );
          this._particles.push(p);
        }, i * 60);
        this._particleBurstTimers.add(tid);
      }
    },

    _flipStatus(newText) {
      const el = document.getElementById('fetchStatusText');
      // v1.0.44: no-GSAP path must still land the text (R2: a silent return
      // here drops the status flip entirely).
      if (!el) return;
      if (!window.gsap) {
        el.textContent = newText;
        return;
      }
      window.gsap.to(el, {
        rotateX: 90, opacity: 0, duration: 0.2, ease: 'power2.in',
        onComplete: () => {
          el.textContent = newText;
          window.gsap.fromTo(el,
            { rotateX: -90, opacity: 0 },
            { rotateX: 0, opacity: 1, duration: 0.3, ease: 'back.out(1.5)' }
          );
        }
      });
    },

    _suckIntoMoni() {
      // v1.0.9.8: single-source done - source icon+label shrink into moni with acceleration
      if (this._chainRows.length !== 1) return;
      const row = this._chainRows[0];
      const moniIcon = document.getElementById('moniIcon');
      const stage = document.getElementById('particleStage');
      if (!row || !moniIcon || !stage) return;

      // v1.0.44: no-GSAP degradation — jump straight to the end state (row
      // absorbed, moni icon settled) instead of silently returning with the
      // row still sitting in the stage (R2).
      if (!window.gsap) {
        row.el.style.transform = 'scale(0)';
        row.el.style.opacity = '0';
        return;
      }

      const sr = stage.getBoundingClientRect();
      const rr = row.el.getBoundingClientRect();
      const mr = moniIcon.getBoundingClientRect();
      const rowCx = rr.left - sr.left + rr.width / 2;
      const rowCy = rr.top - sr.top + rr.height / 2;
      const moniCx = mr.left - sr.left + mr.width / 2;
      const moniCy = mr.top - sr.top + mr.height / 2;

      // Get row's natural position (where it would be without transform)
      const origTransform = row.el.style.transform;
      row.el.style.transform = '';
      const naturalRect = row.el.getBoundingClientRect();
      row.el.style.transform = origTransform;
      const naturalCx = naturalRect.left - sr.left + naturalRect.width / 2;
      const naturalCy = naturalRect.top - sr.top + naturalRect.height / 2;

      window.gsap.set(row.el, { x: rowCx - naturalCx, y: rowCy - naturalCy });
      window.gsap.to(row.el, {
        x: moniCx - naturalCx,
        y: moniCy - naturalCy,
        scale: 0.15,
        opacity: 0,
        duration: 0.7,
        ease: 'power4.in',
        onComplete: () => {
          row.el.style.transform = 'scale(0)';
          row.el.style.opacity = '0';
        }
      });
      window.gsap.to(moniIcon, {
        scale: 1.2, duration: 0.15, yoyo: true, repeat: 1,
        ease: 'power2.out', delay: 0.65
      });
    },

    _showCompletion(summary, label = '抓取完成！') {
      const overlay = document.getElementById('completionOverlay');
      const sub = document.getElementById('completionSub');
      if (!overlay) return;
      const title = overlay.querySelector('.completion-text');
      if (title) title.textContent = label;
      if (sub && summary) sub.textContent = summary;
      overlay.classList.add('show');
    },

    _updateFeedInfo(currentSourceName, feedName = '') {
      const fn = document.getElementById('feedName');
      if (fn) fn.textContent = feedName || (currentSourceName ? `当前: ${currentSourceName}` : '');
    },

    _animateArticleCount(count) {
      const els = ['modalArticleCount', 'bottomArticleCount'];
      els.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        const current = parseInt(el.textContent) || 0;
        if (current === count) return;
        // v1.0.44: guard + degradation. A bare `gsap.to` here threw
        // ReferenceError when the deferred CDN script hadn't landed; the
        // counter still has to reach its final value, so set it directly.
        if (!window.gsap) {
          el.textContent = String(count);
          return;
        }
        window.gsap.to({ val: current }, {
          val: count,
          duration: 0.5,
          ease: 'power2.out',
          onUpdate() {
            el.textContent = Math.round(this.targets()[0].val);
          }
        });
      });
    },

    get fetchProgressPercent() {
      const p = this.fetchProgress;
      if (!p || !p.total_feeds) return 0;
      return Math.min(100, Math.round((p.done_feeds / p.total_feeds) * 100));
    },

    get fetchBatchPercent() {
      const p = this.fetchProgress;
      if (!p || !p.total_sources) return 0;
      return Math.min(100, Math.round((p.done_sources / p.total_sources) * 100));
    },

    getSourceIcon(name) {
      // v1.0.13: xwatch needs its own entry (was missing → fell back to
      // 36kr.png on the sources table and the chain panel).
      const map = {
        '36kr_rss': '/static/icons/sources/36kr.png',
        'bestblogs': '/static/icons/sources/bestblog-gzh.png',
        'bestblog-podcast': '/static/icons/sources/bestblogs.png',
        'bestblog-x': '/static/icons/sources/bestblog-x.png',
        'bestblog-yt': '/static/icons/sources/bestblog-yt.png',
        'huxiu': '/static/icons/sources/huxiu.png',
        'mpaypass': '/static/icons/sources/mpaypass.png',
        'qbitai': '/static/icons/sources/qbitai.png',
        'wechat_rss_a': '/static/icons/sources/wechat_rss.png',
        'wechat_rss_b': '/static/icons/sources/wechat_rss_b.png',
        'xwatch': '/static/icons/sources/xwatch.png',
        // v1.0.66: xbot_feed (X-bot 精选)
        'xbot_feed': '/static/icons/sources/xbot_feed.png',
      };
      return map[name] || '/static/icons/sources/36kr.png';
    },

    // v1.0.5.24: copy article URL to clipboard
    copyArticleUrl() {
      if (!this.articleDetail?.url) return;
      navigator.clipboard.writeText(this.articleDetail.url)
        .then(() => { this.copyToast = true; setTimeout(() => { this.copyToast = false; }, 2000); })
        .catch(() => { this._notify('error', '复制失败，请手动复制 URL (见详情页)。'); });
    },

    // v1.0.5.24: import full-text from local .md file
    importFulltext() {
      if (!this.importFilePath || !this.articleDetail) return;
      this.importLoading = true;
      fetch(`/api/articles/${this.articleDetail.id}/import-fulltext`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ file_path: this.importFilePath }),
      })
        .then(r => r.json().then(d => r.ok ? d : Promise.reject(d)))
        .then(data => {
          const articleId = this.articleDetail.id;
          this._notify('success', `全文导入成功，${data.char_count} 字`);
          this.importFilePath = '';
          // content_preview is only 500 chars. Refetch so the reader
          // renders the imported article, not the preview stub.
          return fetch(`/api/articles/${articleId}`, { credentials: 'same-origin' })
            .then(r => r.json())
            .then(article => {
              this.articleDetail = article;
              if (typeof this.readerRenderArticle === 'function') {
                this.readerRendered = this.readerRenderArticle(article.content || '');
                this.readerBodyEmpty = false;
              }
            });
        })
        .catch(err => { this._notify('error', '导入失败: ' + (err.message || err.detail || '未知错误')); })
        .finally(() => { this.importLoading = false; });
    },

    pingSource(id) {
      // Prefer YAML source_name when available (id may be DB src-xxx).
      const src = (this.sources || []).find(s => s.id === id || s.name === id);
      const ref = (src && src.name) ? src.name : id;
      this.setFetchResult('正在测试连通性...', null);
      fetch(`/api/fetch/sources/${encodeURIComponent(ref)}/ping`, { method: 'POST', credentials: 'same-origin' })
        .then(async r => {
          let body = null;
          try { body = await r.json(); } catch (_) { /* ignore */ }
          if (!r.ok) {
            const detail = (body && (body.message || body.detail)) || r.status;
            this.setFetchResult('FAIL - ' + detail, null);
            return;
          }
          const st = (body && body.status) || 'ok';
          const msg = (body && (body.message || '')) || '';
          // v1.0.12: xwatch returns multi-probe detail
          const d = body && body.detail;
          if (d && d.syndication) {
            const synd = d.syndication.ok ? 'synd✓' : 'synd✗';
            const ddg = d.ddg
              ? ` ddg=${d.ddg.status || '?'}`
              : '';
            const arts = d.passive
              ? ` arts=${d.passive.article_count ?? '-'} 48h=${d.passive.articles_last_48h ?? '-'}`
              : '';
            this.setFetchResult(
              `${st.toUpperCase()} ${synd}${ddg}${arts}` + (msg ? ` — ${msg}` : ''),
              null,
            );
            return;
          }
          this.setFetchResult(
            st === 'ok'
              ? ('OK - 连通性正常' + (msg ? ` (${msg})` : ''))
              : `DEGRADED${msg ? ' — ' + msg : ''}`,
            null,
          );
        })
        .catch(err => { this.setFetchResult('FAIL - ' + err.message, null); });
    },

    // ── Share panel ────────────────────────────────────────────────
    allArticles: [],
    // v1.0.5.14: share page source filter + pagination (same pattern
    // as the articles list — the share card grid would otherwise show
    // 228 cards, most of which the user never wants to share).
    allArticlesSource: '',
    allArticlesSources: [],
    allArticlesTag: '',
    allArticlesTags: [],
    allArticlesPage: 1,
    allArticlesPageSize: 24,
    allArticlesPageSizeOptions: [12, 24, 48, 96],
    allArticlesHasMore: false,
    // v1.0.8: share page article source chip.  'pinned' (default, per
    // user decision 3) shows only article_pins data; 'all' is the
    // legacy behaviour.  selectedArticleIds is preserved across
    // modes so a user's in-progress share isn't lost on toggle.
    shareArticleSource: 'pinned',
    get shareArticles() {
      return this.shareArticleSource === 'pinned' ? this.pinnedArticles : this.allArticles;
    },
    selectedArticleIds: [],
    // v1.0.5.14: share format values MUST match the backend
    // moni.share.formats.FormatEnum.  Previously these used friendly
    // short names ("plain", "html", "image", "wechat", "weibo",
    // "audio", "video", "infographic") that the backend rejected
    // with 400 "Unknown format X".  The labels are kept in Chinese
    // for the UI — only the value is aligned with the FormatEnum.
    shareFormat: 'markdown',
    // v1.0.5.14: title is required for html_landing and ppt_slides
    // formats (backend Pydantic models).  We collect it via an
    // input that's only shown when the selected format needs it
    // (see x-show="shareFormatNeedsTitle()" in the HTML).  When
    // the format doesn't need it, this value is still sent in the
    // payload — backend formats like markdown ignore unknown keys
    // thanks to their _extra_forbid config… actually no, we want
    // to AVOID sending `title` for formats that forbid it.  The
    // addToQueue() function does the right thing.
    shareFormatTitle: '',
    // v1.0.5.18: pre-flight confirm-modal state.  addToQueue() now
    // fetches /api/share/estimate first, sets shareEstimate, and
    // shows the modal.  The user clicks 确认 to actually POST
    // /api/share, or 取消 to dismiss.  shareEstimateError is
    // populated if the estimate call itself 4xx/5xx's — in that
    // case the user can still confirm (skipping the preview) or
    // cancel.
    shareEstimate: null,
    shareEstimateError: null,
    shareEstimateSubmitting: false,
    _shareEstimatePayload: null,  // the validated payload to POST on confirm
    _shareEstimateFormat: null,   // v1.0.5.18: stashed shareFormat at the
                                  // moment of addToQueue so the confirm
                                  // step doesn't re-read Alpine state
                                  // (which the user may have changed
                                  // between estimate and confirm).
    // v1.0.5.17: "▶ 立即处理队列" button state.  triggerWorker()
    // hits POST /api/share/worker/run and opens the log-tail modal.
    // workerRunning guards the button so we don't double-spawn.
    // workerModalResult holds the response of the launch (started
    // / already_running / pid / log_path / error).  workerLogLines
    // is the live tail of worker.log, refreshed every 3s while the
    // modal is open.
    workerRunning: false,
    workerModalOpen: false,
    workerModalResult: null,
    workerLogLines: [],
    _workerLogPollInterval: null,
    // v1.0.5.18: 6 share formats (was 10).  Pruned 6 formats that had
    // no real worker implementation.  Added rich_text (structured
    // Markdown variant) and report_png (one-image content report).
    shareFormats: [
      { value: 'plain_text',          label: '纯文本' },
      { value: 'markdown',            label: 'Markdown' },
      { value: 'html_landing',        label: 'HTML 网页' },
      { value: 'info_graphic_png',    label: '信息图 PNG' },
      { value: 'rich_text',           label: '富文本' },  // 🆕
      { value: 'report_png',          label: '报告 PNG' },     // 🆕
      { value: 'pdf',                 label: '报告 PDF' },     // 🆕 v1.0.5.21
    ],
    // v1.0.5.18: 4 user-options (universal across all 6 formats, per
    // PRD §2).  include_abstract defaults to true (most users want
    // the AI summary); include_fact_check defaults to false (slower;
    // explicit opt-in).  provider/model default to '' (use the
    // default profile's provider / model — no override).
    shareIncludeAbstract: true,
    shareIncludeFactCheck: false,
    // v1.0.10: one-click brief
    briefFormat: 'html_landing',  // v1.0.10: offline HTML (full body + fold)
    briefSubmitting: false,
    // v1.0.14: cooldown after successful submit (ms epoch); blocks multi-click storms
    briefCooldownUntil: 0,
    activeBriefId: null,
    briefError: null,
    shareProvider: '',
    shareModel: '',
    // v1.0.5.18: report_png generation approach (only used when
    // shareFormat === 'report_png').  Maps 1:1 to the 4 PRD §4
    // approaches.  Default 'baoyu-infographic' (most mature).
    sharePngApproach: 'baoyu-infographic',
    // v1.0.87: 生图方式选项，每种带一句说明（分段按钮 + 当前项说明同源）
    sharePngApproachOptions: [
      { value: 'baoyu-infographic', label: '信息图', desc: '信息图风格，由 baoyu-infographic skill 生成，最成熟稳定。' },
      { value: 'ljg-card', label: '卡片', desc: '卡片风格，由 ljg-card skill 生成，轻量简洁。' },
      { value: 'kami', label: '排版', desc: '排版风格，由 kami skill 生成，适合多版式。' },
      { value: 'direct', label: '直出', desc: '由模型自行撰写生图 prompt 直出，最灵活但不确定性高。' },
    ],
    pngApproachDesc() {
      const cur = this.sharePngApproach;
      const found = this.sharePngApproachOptions.find(o => o.value === cur);
      return found ? found.desc : '';
    },
    // v1.0.5.25: BestBlogs API 增强选项
    shareBBSummarySource: 'moni_ai',  // 'moni_ai' | 'bestblogs' | 'both' | 'none'
    shareBBEnhanceScore: false,
    shareBBEnhanceMainPoints: false,
    shareBBEnhanceKeyQuotes: false,
    shareBBEnhanceTags: false,
    shareInfoGraphicTitle: '同业追踪日报',
    shareQueue: [],
    _sharePollInterval: null,
    // v1.0.85 (sprint-46): three peer tabs on the share page.
    shareTab: 'brief',   // 'brief' | 'custom' | 'queue'
    // One-click brief format cards.  Values MUST stay in sync with
    // shareFormats / backend FormatEnum — all 7 production formats
    // are listed (report_png included; the design mock only drew 6).
    briefFormatCards: [
      {
        value: 'html_landing', label: 'HTML 网页', badge: '推荐',
        desc: '离线独立单页 · 完整图文与折叠交互 · 适合团队内部分享与归档浏览',
      },
      {
        value: 'markdown', label: 'Markdown 文档', badge: '高通用',
        desc: '纯文本规范文档 · 兼容飞书、Notion 与 GitHub 知识库导入',
      },
      {
        value: 'info_graphic_png', label: '信息图 PNG', badge: '视觉化',
        desc: '高价值视觉长图 · 提炼金句与数据图表 · 适合即时通讯群发送',
      },
      {
        value: 'report_png', label: '报告 PNG', badge: '单图报告',
        desc: '单图数据报告 · 关键指标与结论一图概览 · 适合快速传阅',
      },
      {
        value: 'pdf', label: '报告 PDF', badge: '标准版式',
        desc: 'A4 标准打印排版 · 含封面与结构化排版 · 适合呈送管理层',
      },
      {
        value: 'rich_text', label: '富文本', badge: '即时交流',
        desc: '带格式重点高亮 · 富文本可直接粘贴至邮件或钉钉/企微',
      },
      {
        value: 'plain_text', label: '纯文本', badge: '极简',
        desc: '纯字符紧凑摘要 · 零冗余排版 · 适合快速通读与终端环境阅读',
      },
    ],

    shareFormatLabel(value) {
      const fmt = (this.shareFormats || []).find(f => f.value === value);
      return fmt ? fmt.label : (value || '');
    },

    // Queue counters used by the tab badge and the status bar.
    shareQueueStatusCount(statuses) {
      return (this.shareQueue || []).filter(q => statuses.includes(q.status)).length;
    },
    get shareQueueBadgeCount() {
      return this.shareQueueStatusCount(['pending', 'running']);
    },
    get shareWorkerActive() {
      return (this.shareQueue || []).some(q => q.status === 'running');
    },

    get customShareSummary() {
      const parts = [`已选 ${this.selectedArticleIds.length} 篇`];
      parts.push(this.shareFormatLabel(this.shareFormat));
      if (this.shareIncludeAbstract) parts.push('含 AI 摘要');
      if (this.shareIncludeFactCheck) parts.push('含事实核查');
      return parts.join(' · ');
    },

    // Batch actions operate on the visible page of the active source.
    shareSelectAllOnPage() {
      const pageIds = (this.shareArticles || []).map(a => a.id);
      const set = new Set(this.selectedArticleIds);
      pageIds.forEach(id => set.add(id));
      this.selectedArticleIds = Array.from(set);
    },
    shareClearSelection() {
      this.selectedArticleIds = [];
    },

    get shareArticlePage() {
      return this.shareArticleSource === 'pinned' ? this.pinnedPage : this.allArticlesPage;
    },
    get shareArticleHasMore() {
      return this.shareArticleSource === 'pinned' ? this.pinnedHasMore : this.allArticlesHasMore;
    },
    shareArticlePrevPage() {
      if (this.shareArticleSource === 'pinned') {
        if (this.pinnedPage > 1) { this.pinnedPage -= 1; this.loadPinnedArticles(); }
      } else {
        this.allArticlesPrevPage();
      }
    },
    shareArticleNextPage() {
      if (this.shareArticleSource === 'pinned') {
        if (this.pinnedHasMore) { this.pinnedPage += 1; this.loadPinnedArticles(); }
      } else {
        this.allArticlesNextPage();
      }
    },

    loadAllArticles() {
      // v1.0.5.14: source filter + pagination.  Over-fetch one row to
      // detect "has more".  When the source changes, also refresh the
      // <select> option list (cheap GET, reuses the same endpoint as
      // articles-page loadArticlesFilters()).
      const probe = parseInt(this.allArticlesPageSize, 10) || 50;
      const params = new URLSearchParams();
      if (this.allArticlesSource) params.set('source', this.allArticlesSource);
      if (this.allArticlesTag) params.set('tag', this.allArticlesTag);
      params.set('limit', probe + 1);
      params.set('offset', (this.allArticlesPage - 1) * probe);
      const query = params.toString();
      fetch('/api/articles' + (query ? '?' + query : ''), { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          const list = Array.isArray(data) ? data : (data.articles || []);
          if (list.length > probe) {
            this.allArticlesHasMore = true;
            this.allArticles = list.slice(0, probe);
          } else {
            this.allArticlesHasMore = false;
            this.allArticles = list;
          }
        })
        .catch(err => console.error('loadAllArticles failed:', err));
      // Refresh the <select> option list (sources for share-page filter).
      fetch('/api/config/sources', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          this.allArticlesSources = Array.isArray(data) ? data : (data.sources || []);
        })
        .catch(err => console.error('loadAllArticles (sources list) failed:', err));
      // v1.0.6.7: also load tags for tag filter
      fetch('/api/config/tags', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          this.allArticlesTags = Array.isArray(data) ? data : [];
        })
        .catch(err => console.error('loadAllArticles (sources list) failed:', err));
    },

    allArticlesPrevPage() {
      if (this.allArticlesPage > 1) { this.allArticlesPage -= 1; this.loadAllArticles(); }
    },
    allArticlesNextPage() {
      if (this.allArticlesHasMore) { this.allArticlesPage += 1; this.loadAllArticles(); }
    },
    allArticlesChangePageSize() {
      this.allArticlesPage = 1; this.loadAllArticles();
    },
    onAllArticlesSourceChange() {
      // v1.0.5.14: source filter changed — reset to page 1.
      this.allArticlesPage = 1;
      this.loadAllArticles();
    },
    onAllArticlesTagChange() {
      // v1.0.6.7: tag filter changed — reset to page 1.
      this.allArticlesPage = 1;
      this.loadAllArticles();
    },

    toggleAllArticles(event) {
      this.selectedArticleIds = event.target.checked
        ? this.allArticles.map(a => a.id)
        : [];
    },

    // ── v1.0.10 one-click brief ─────────────────────────────────
    get activeBriefTask() {
      if (!this.activeBriefId || !this.shareQueue) return null;
      return this.shareQueue.find(q => q.id === this.activeBriefId) || null;
    },
    briefStageLabel(stage) {
      const map = {
        queued: '排队中',
        loading_articles: '读取正文',
        fetching: '读取正文',
        fetching_bb_meta: 'BestBlogs 增强',
        generating_summaries: '生成摘要',
        enrich_summary: '生成摘要',
        enrich_fact_check: '事实核查',
        enrich_business_value: '业务价值评估',
        building_prompt: '拼装简报',
        invoking_llm: '渲染产出',
        writing_artifact: '写入文件',
        done: '完成',
        failed: '失败',
      };
      return map[stage] || stage || '…';
    },
    // v1.0.14: active (pending/running) tasks in the share queue
    shareActiveTaskCount() {
      return (this.shareQueue || []).filter(
        q => q.status === 'pending' || q.status === 'running'
      ).length;
    },

    async createBrief(opts) {
      opts = opts || {};
      if (this.briefSubmitting) return;
      if (this.briefCooldownUntil && Date.now() < this.briefCooldownUntil) {
        const sec = Math.ceil((this.briefCooldownUntil - Date.now()) / 1000);
        this._notify('info', `刚提交过简报，请 ${sec}s 后再试（或等当前任务结束）`);
        return;
      }
      const n = this.pinnedTotal || this.pinnedArticles.length || 0;
      if (n <= 0) {
        this._notify('info', '还没有 Pin 文章。请先在「文章查阅」最左列 Pin 至少 1 篇，再生成简报。');
        return;
      }

      // Second+ generation while queue has work → confirm (unless force already set)
      let force = !!opts.force;
      const activeN = this.shareActiveTaskCount();
      if (!force && activeN > 0) {
        const ok = await moniModal.confirm(
          `队列里已有 ${activeN} 个任务在排队/运行。再生成一份会占用 LLM 额度与时间。确定继续？`,
          { title: '再次生成简报', okLabel: '仍要生成', danger: true }
        );
        if (!ok) return;
        force = true;
      }

      this.briefSubmitting = true;
      this.briefError = null;
      this.workerRunning = true;
      fetch('/api/share/brief', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          format: this.briefFormat || 'markdown',
          force: force,
        }),
      })
        .then(async r => {
          const text = await r.text().catch(() => '');
          let data = null;
          try { data = text ? JSON.parse(text) : null; } catch (_) { /* not json */ }
          if (r.status === 409) {
            // Backend duplicate guard — surface existing task, do not throw red error
            const details = (data && data.details) || (data && data.detail && data.detail.details) || {};
            const sid = details.share_id || '';
            const st = details.status || '';
            this._notify(
              'info',
              sid
                ? `已有相同 Pin 的任务在跑（${sid.slice(0, 12)}… · ${st}）。可在下方队列暂停/丢弃后再生成。`
                : ((data && (data.message || data.detail)) || '已有相同任务在排队')
            );
            if (sid) this.activeBriefId = sid;
            this.loadShareQueue && this.loadShareQueue();
            this._startSharePoll && this._startSharePoll();
            return null;
          }
          if (r.status === 400 && data && (data.message === 'articles_not_ready' || (data.details && data.details.articles))) {
            // v1.0.15: pins missing both summary and body
            const details = data.details || {};
            const titles = details.titles || (details.articles || []).map(a => a.title || a.id);
            const nBad = details.count || titles.length || 0;
            const lines = (titles || []).slice(0, 8).map((t, i) => `${i + 1}. ${t}`);
            if (titles.length > 8) lines.push(`…另有 ${titles.length - 8} 篇`);
            const msg = (details.hint || '部分 Pin 缺少摘要与全文，无法生成有价值简报。')
              + (lines.length ? ('\n\n需处理：\n' + lines.join('\n')) : '');
            this.briefError = msg;
            this._notify('error', `有 ${nBad} 篇 Pin 无摘要且无全文，已取消生成`);
            return null;
          }
          if (!r.ok) {
            const detail = (data && (data.detail || data.message || data.error)) || text || r.status;
            throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
          }
          return data;
        })
        .then(data => {
          if (!data) return;
          this.activeBriefId = data.share_id;
          // 15s client cooldown after successful enqueue
          this.briefCooldownUntil = Date.now() + 15000;
          if (data.worker && data.worker.needs_manual_start) {
            this.briefError = '任务已入队，但 worker 未能自动启动，请点下方「立即处理队列」。';
          }
          this._notify('success', '简报任务已入队');
          this.loadShareQueue && this.loadShareQueue();
          this._startSharePoll && this._startSharePoll();
        })
        .catch(err => {
          console.error('createBrief failed:', err);
          this.briefError = err.message || String(err);
        })
        .finally(() => {
          this.briefSubmitting = false;
          setTimeout(() => {
            const t = this.activeBriefTask;
            if (!t || t.status === 'done' || t.status === 'failed' || t.status === 'cancelled' || t.status === 'paused') {
              this.workerRunning = false;
            }
          }, 2000);
        });
    },
    retryBrief() {
      this.createBrief({ force: true });
    },

    // v1.0.14: queue task control (pause / resume / cancel)
    async shareTaskPause(taskId) {
      if (!taskId) return;
      const ok = await moniModal.confirm('暂停该任务？排队中的不会再被 worker 领取。', {
        title: '暂停任务', okLabel: '暂停',
      });
      if (!ok) return;
      this._shareTaskAction(taskId, 'pause', '已暂停');
    },
    async shareTaskResume(taskId) {
      if (!taskId) return;
      this._shareTaskAction(taskId, 'resume', '已重新排队');
    },
    async shareTaskCancel(taskId) {
      if (!taskId) return;
      const ok = await moniModal.confirm('丢弃该任务？进行中的 LLM 调用可能仍会跑完但结果不会写入。', {
        title: '丢弃任务', okLabel: '丢弃', danger: true,
      });
      if (!ok) return;
      this._shareTaskAction(taskId, 'cancel', '已丢弃');
    },
    _shareTaskAction(taskId, action, okMsg) {
      fetch(`/api/share/tasks/${encodeURIComponent(taskId)}/${action}`, {
        method: 'POST',
        credentials: 'same-origin',
      })
        .then(async r => {
          const text = await r.text().catch(() => '');
          let data = null;
          try { data = text ? JSON.parse(text) : null; } catch (_) {}
          if (!r.ok) {
            const detail = (data && (data.message || data.detail || data.error)) || text || r.status;
            throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
          }
          return data;
        })
        .then(() => {
          this._notify('success', okMsg || '已更新');
          this.loadShareQueue && this.loadShareQueue();
        })
        .catch(err => {
          console.error(`shareTask ${action} failed:`, err);
          this._notify('error', err.message || String(err));
        });
    },

    addToQueue() {
      // v1.0.5.16: pre-flight estimate → confirm modal → POST.  The
      // pre-checks (article selection, title required for some
      // formats) are identical to before; the new bit is the modal
      // step that shows the LLM model + token + duration estimate
      // so the user can decide whether to commit the call.
      // v1.0.5.18: also stash shareFormat so the confirm step
      // doesn't accidentally re-read Alpine state (which the user
      // may have changed between estimate and confirm).
      if (this.shareEstimateSubmitting) {
        // Defensive: a second click while the first estimate is in
        // flight.  We just no-op; the user can wait for the modal.
        return;
      }
      if (!this.selectedArticleIds.length) {
        this._notify('info', '请先勾选要分享的文章 (在文章卡片上点 checkbox, 至少 1 个)。');
        return;
      }
      // v1.0.5.14: html_landing / ppt_slides require a `title` field
      // in the payload; other formats forbid extra keys (Pydantic
      // _extra_forbid) so we must NOT send `title` for them.
      // v1.0.5.18: html_landing still needs `title`; ppt_slides was
      // pruned.  rich_text / report_png don't need a title.
      const payload = {
        article_ids: this.selectedArticleIds,
        // v1.0.5.18: 4 universal user-options (per PRD §2).  We
        // always send them; the backend Pydantic models accept None
        // and apply the documented default.
        include_abstract: this.shareIncludeAbstract,
        include_fact_check: this.shareIncludeFactCheck,
      };
      // provider/model: only send if non-empty (empty == use default
      // profile).  report_png generation_approach only when format
      // matches.  This keeps the queue file small and matches what
      // the user actually configured.
      if (this.shareProvider) payload.provider = this.shareProvider;
      if (this.shareModel)    payload.model    = this.shareModel;
      if ((this.shareFormat === 'report_png' || this.shareFormat === 'info_graphic_png') && this.sharePngApproach) {
        payload.generation_approach = this.sharePngApproach;
      }
      // v1.0.5.20: info_graphic_png / pdf 格式传递用户自定义标题
      if ((this.shareFormat === 'info_graphic_png' || this.shareFormat === 'pdf') && this.shareInfoGraphicTitle) {
        payload.title = this.shareInfoGraphicTitle.trim() || '同业追踪日报';
      }
      if (this.shareFormatNeedsTitle()) {
        const t = (this.shareFormatTitle || '').trim();
        if (!t) {
          this._notify('info', '此格式 (' + this.shareFormat + ') 必填 title 字段。请在上方输入框填写页面/演示标题。');
          return;
        }
        payload.title = t;
      }
      // Stash the payload + format so confirmShareEstimate() can
      // re-use it without re-validating on the client (and without
      // re-reading this.shareFormat, which the user may have
      // changed between estimate and confirm).  Server re-validates
      // either way (validate_manifest is the source of truth).
      // v1.0.5.25: BestBlogs API 增强选项
      // bb_enhance: true if any BB option is selected (triggers API calls)
      const bbEnhance = this.shareBBEnhanceScore || this.shareBBEnhanceMainPoints
        || this.shareBBEnhanceKeyQuotes || this.shareBBEnhanceTags
        || this.shareBBSummarySource === 'bestblogs'
        || this.shareBBSummarySource === 'both';
      if (bbEnhance) payload.bb_enhance = true;
      if (this.shareBBSummarySource !== 'moni_ai') payload.bb_summary_source = this.shareBBSummarySource;
      if (this.shareBBEnhanceScore) payload.bb_score = true;
      if (this.shareBBEnhanceMainPoints) payload.bb_main_points = true;
      if (this.shareBBEnhanceKeyQuotes) payload.bb_key_quotes = true;
      if (this.shareBBEnhanceTags) payload.bb_tags = true;

      this._shareEstimatePayload = payload;
      this._shareEstimateFormat = this.shareFormat;
      this.shareEstimateError = null;
      this.shareEstimateSubmitting = true;  // covers fetch + render
      this.shareEstimate = null;  // show "loading" state via modal-backdrop x-show

      fetch('/api/share/estimate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          format: this.shareFormat,
          payload: payload,
        }),
      })
        .then(async r => {
          if (!r.ok) {
            const text = await r.text().catch(() => '');
            throw new Error('HTTP ' + r.status + (text ? ' · ' + text : ''));
          }
          return r.json();
        })
        .then(data => {
          this.shareEstimate = { format: this.shareFormat, ...data };
          this.shareEstimateError = null;
        })
        .catch(err => {
          // v1.0.5.16: 估算失败时仍允许用户"盲点确认"入队 (模态显错
          // 误 + 确认按钮仍 enable).  这是有意的: 估算 4xx 通常是
          // 用户拼错参数, 那确认也会 4xx; 估算 5xx 是后端临时挂,
          // 但真入队的代码路径独立.
          console.error('estimate failed:', err);
          this.shareEstimate = { format: this.shareFormat };
          this.shareEstimateError = '估算接口失败: ' + err.message;
        })
        .finally(() => {
          // Re-enable the button regardless of success/failure so
          // the user can re-trigger after a transient error.  The
          // modal is now open (or has an error message), so this
          // flag is not visually wrong.
          this.shareEstimateSubmitting = false;
        });
    },

    cancelShareEstimate() {
      // User dismissed the modal — × button, 取消 button, backdrop
      // click, or Escape key.  Reset all estimate state.
      this.shareEstimate = null;
      this.shareEstimateError = null;
      this.shareEstimateSubmitting = false;
      this._shareEstimatePayload = null;
      this._shareEstimateFormat = null;  // v1.0.5.18
    },

    confirmShareEstimate() {
      // User clicked 确认加入队列.  We send the SAME format and
      // payload that the estimate was based on (stashed in
      // _shareEstimatePayload + _shareEstimateFormat at
      // addToQueue time) — the server re-validates, but we don't
      // want the modal to be the source of any UI-driven
      // mutation of the request (e.g. user clicks a different
      // format chip while the modal is open).
      const payload = this._shareEstimatePayload;
      const format = this._shareEstimateFormat;
      if (!payload || !format) {
        // Defensive: shouldn't happen because cancelShareEstimate
        // clears it.  If it does, fail loudly so the user sees
        // something rather than a silent no-op.
        this.cancelShareEstimate();
        this._notify('error', '内部状态错误: 请重试。');
        return;
      }
      this.shareEstimateSubmitting = true;
      fetch('/api/share', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          format: format,
          payload: payload,
        }),
      })
        .then(async r => {
          if (!r.ok) {
            const text = await r.text().catch(() => '');
            throw new Error('HTTP ' + r.status + (text ? ' · ' + text : ''));
          }
          return r.json();
        })
        .then(() => {
          this.cancelShareEstimate();
          this.loadShareQueue();
          this.selectedArticleIds = [];
        })
        .catch(err => {
          console.error('addToQueue failed:', err);
          this._notify('error', '加入队列失败: ' + err.message);
          // Close the modal too — keeping it open with an error
          // visible would force the user to dismiss it manually,
          // and the alert already conveys the failure.
          this.cancelShareEstimate();
        });
    },

    // v1.0.5.16: helper for the share-estimate modal — turns the
    // backend's plain int seconds into a "1 分 23 秒" / "45 秒" /
    // "2 分 10 秒" string.  Kept here (not in formatDuration) because
    // the existing formatDuration works on milliseconds, not seconds.
    formatShareDuration(sec) {
      if (sec === null || sec === undefined || sec < 0) return '—';
      sec = Math.max(1, Math.round(sec));
      if (sec < 60) return sec + ' 秒';
      const m = Math.floor(sec / 60);
      const s = sec % 60;
      return s === 0 ? (m + ' 分') : (m + ' 分 ' + s + ' 秒');
    },

    // v1.0.5.17: helpers for the "▶ 立即处理队列" button + the
    // worker log-tail modal.  The button is disabled when there's
    // nothing to process (no pending/running rows); the small
    // "N 个待处理" label tells the user how many would actually
    // be picked up.  shareQueueHasPending() is the disabled-check;
    // shareQueuePendingCount() is the badge text.

    shareQueueHasPending() {
      return (this.shareQueue || []).some(
        q => q.status === 'pending' || q.status === 'running'
      );
    },

    shareQueuePendingCount() {
      return (this.shareQueue || []).filter(
        q => q.status === 'pending' || q.status === 'running'
      ).length;
    },

    // Title shown in the worker-log modal header.  Different copy
    // for the 3 launch outcomes so the user can tell at a glance
    // whether they actually started something or just observed an
    // existing one.
    get workerModalTitle() {
      const r = this.workerModalResult;
      if (!r) return '处理队列';
      if (r.started) return '处理队列 - 已启动 worker';
      if (r.already_running) return '处理队列 - 已有 worker 在跑';
      // v1.0.49.1 (audit F5): the worker ran and exited 0 without
      // taking the lock — the queue was already drained.  Calling
      // that "启动失败" was a lie: it made the user click the button
      // again (which would spawn a second worker for no work).
      if (r.nothing_to_do) return '处理队列 - 队列已处理完';
      return '处理队列 - 启动失败';
    },

    // v1.0.5.17: trigger a worker run.  POSTs to /api/share/worker/run
    // and opens the log-tail modal.  Polls the log every 3s while
    // the modal is open.  The button itself is disabled when
    // workerRunning=true (and we also re-enable it in the
    // response handler so a "already_running" outcome can re-arm
    // the button when the existing worker finishes).
    async triggerWorker() {
      if (this.workerRunning) return;
      this.workerRunning = true;
      this.workerModalResult = null;
      this.workerLogLines = [];
      try {
        const r = await fetch('/api/share/worker/run', {
          method: 'POST',
          credentials: 'same-origin',
        });
        const data = await r.json();
        this.workerModalResult = data;
        this.workerModalOpen = true;
        if (data.error) {
          // Surface the error in the console; the modal also
          // shows it (the red error-text summary-box is x-shown
          // when result.error is truthy).
          console.error('worker launch failed:', data.error);
        }
        // If the worker just started, refresh the queue every 5s
        // (not just 30s) so the user sees pending → running →
        // done transitions without long delays.  We restore the
        // normal 30s cadence when the modal closes.
        this.loadShareQueue();
        this._startWorkerLogPoll();
      } catch (err) {
        console.error('triggerWorker failed:', err);
        this._notify('error', '触发 worker 失败: ' + err.message);
      } finally {
        // We re-enable the button so the user can re-trigger after
        // a transient error.  workerModalResult.started is the
        // signal that the worker is running; the lock file on
        // disk (server-side) is the source of truth for "is
        // another worker up?", so even if the UI re-fires the
        // request, the server will return already_running and
        // the modal title will say so.
        this.workerRunning = false;
      }
    },

    // v1.0.5.17: 3-second log poll while the modal is open.  The
    // interval ID is stashed so closeWorkerModal() can clear it
    // deterministically; the queue-status poll (30s) keeps
    // running independently.
    _startWorkerLogPoll() {
      if (this._workerLogPollInterval) {
        clearInterval(this._workerLogPollInterval);
      }
      this.refreshWorkerLog();  // immediate first fetch
      this._workerLogPollInterval = setInterval(
        () => this.refreshWorkerLog(),
        3000
      );
    },

    async refreshWorkerLog() {
      try {
        const r = await fetch(
          '/api/share/worker/log?tail=200',
          { credentials: 'same-origin' }
        );
        if (!r.ok) return;
        const data = await r.json();
        this.workerLogLines = (data.lines || []).slice();
      } catch (err) {
        // Don't spam the user — the next 3s poll will retry.
        console.error('refreshWorkerLog failed:', err);
      }
    },

    closeWorkerModal() {
      this.workerModalOpen = false;
      if (this._workerLogPollInterval) {
        clearInterval(this._workerLogPollInterval);
        this._workerLogPollInterval = null;
      }
    },

    // v1.0.5.14: helper for the share-format title input.  When the
    // selected format needs a `title` field (html_landing, ppt_slides),
    // the form-row becomes visible.  Hard-coded list of formats —
    // if you add another format with a required title, add it here.
    shareFormatNeedsTitle() {
      return this.shareFormat === 'html_landing'
          || this.shareFormat === 'ppt_slides';
    },

    // v1.0.9.3: count of selected articles that CANNOT get BB
    // enhancement (not bestblogs or any bestblog-* sub-source).
    // The share modal shows an inline warning when this is > 0,
    // so users know why some articles won't get BB blocks in the
    // artifact.  Read the source field from each article in
    // allArticles (the source list is loaded by loadAllArticles).
    get shareBBIneligibleCount() {
      if (!this.selectedArticleIds || !this.selectedArticleIds.length) return 0;
      const selectedSet = new Set(this.selectedArticleIds);
      return this.allArticles.filter(a =>
        selectedSet.has(a.id) &&
        !(a.source === 'bestblogs' || (typeof a.source === 'string' && a.source.startsWith('bestblog-')))
      ).length;
    },

    loadShareQueue() {
      // v1.0.5.11: list endpoint is /api/share/tasks (not /api/share/queue).
      // v1.0.5.14: response is paginated as {items, total, page, page_size};
      // accept both {items, ...} (current) and {tasks: [...]} (legacy)
      // for forward-compat.  Each item now has id / format /
      // article_count / status / created_at — renderable in the
      // queue table.
      fetch('/api/share/tasks', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          let list = [];
          if (data && Array.isArray(data.items)) list = data.items;
          else if (data && Array.isArray(data.tasks)) list = data.tasks;
          else if (Array.isArray(data)) list = data;
          this.shareQueue = list;
        })
        .catch(err => console.error('loadShareQueue failed:', err));
    },

    _startSharePoll() {
      // v1.0.13 (audit fix F3): defensively clear any prior poll first so a
      // second _startSharePoll() (e.g. via SPA back/forward) does not stack
      // intervals.
      this._stopSharePoll();
      this.loadShareQueue();
      // v1.0.10: poll every 5s so brief progress is visible (was 30s)
      this._sharePollInterval = setInterval(() => {
        this.loadShareQueue();
        const t = this.activeBriefTask;
        if (t && (t.status === 'running' || t.status === 'pending')) {
          this.workerRunning = true;
        } else if (t && (t.status === 'done' || t.status === 'failed')) {
          this.workerRunning = false;
        }
      }, 5000);
    },

    // v1.0.13 (audit fix F3): explicit teardown for the share-queue poll.
    // Called from navigate() / onPopState() when leaving /share to prevent
    // the 5s poll from running forever after the first /share visit.
    _stopSharePoll() {
      if (this._sharePollInterval) {
        clearInterval(this._sharePollInterval);
        this._sharePollInterval = null;
      }
    },

    // ── Init ────────────────────────────────────────────────
    init() {
      // Apply saved theme (from theme.js mixin)
      this.initTheme();
      // Load service status for topnav indicator
      this.loadServiceStatus();
      // v1.0.5.10: deep-link from current URL — visiting /crud opens
      // the CRUD tab directly.  Falls back to /articles for unknown paths.
      // v1.0.25: /cron /llmSettings redirect into 配置管理 subtabs via navigate().
      const view = this._pathToView[window.location.pathname] || 'articles';
      // Warm article filters at boot (cheap)
      this.loadArticlesFilters();
      // Use navigate() so loaders + legacy redirects stay in one place
      this.navigate(view);
      // Replace history entry after navigate so deep-link is the current entry
      try {
        const path = window.location.pathname + (window.location.search || '');
        window.history.replaceState(
          { view: this.currentView, configTab: this.configTab },
          '',
          path,
        );
      } catch (e) { /* ignore — older browsers */ }
      // v1.0.5.10: listen for browser back/forward so the SPA follows.
      window.addEventListener('popstate', (e) => this.onPopState(e));
    },
    
    // ── Article detail modal (v1.0.4 — BUG #7) ─────────────────
    articleDetail: null,
    summaryLoading: false,
    factCheckLoading: false,
    caseExtractLoading: false,
    // v1.0.22: 联网核查证据链 — Alpine 必须声明 state 否则 x-for ReferenceError
    articleEvidenceList: [],

    showArticleDetail(article) {
      // v1.0.4: sync fetch (Alpine method 中 async/await 兼容性不可靠)
      const self = this;
      fetch(`/api/articles/${article.id}`, { credentials: 'same-origin' })
        .then(r => {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(data => {
          self.articleDetail = data;
          // v1.0.106: 详情弹窗同样续上在跑的任务。
          self.resumeArticleJobs(data.id);
        })
        .catch(err => { this._notify('error', '加载文章失败: ' + err.message); });
    },

    closeArticleDetail() {
      this.articleDetail = null;
      this.summaryLoading = false;
      this.factCheckLoading = false;
    },

    // ── Pin (v1.0.8.4) ──────────────────────────────────────
    // Single-article toggle.  Sends the POST IMMEDIATELY (like the
    // v1.0.8.1 bulk-unpin fix) and stages a 5-second undo window.
    // Cancelling the undo just re-sends the opposite POST.
    //
    // Bug history (v1.0.8.1–8.3): we used a setTimeout 5s for the
    // POST itself.  The window was a *commit* delay, not an *undo*
    // window.  When the user navigated away (or the Pin tab reloaded)
    // the staging timer was never observed but the visual state
    // stayed "flipped" — so toggles looked like they "didn't stick".
    // v1.0.8.4: fire-and-forget POST, undo lives in memory only.
    togglePin(articleId) {
      if (!articleId) return;
      const wasPinned = this._isArticlePinned(articleId);
      const newPinned = !wasPinned;
      // Apply the visual flip on both lists + the detail modal.
      this._applyPinVisual(articleId, newPinned);
      this._pinPopByAid(articleId, newPinned);
      // Cancel any pending undo for this same article (re-click
      // within 5s restores the previous state by sending the
      // opposite POST).
      const existing = this._pendingPinToggles[articleId];
      if (existing) {
        clearTimeout(existing.timer);
        delete this._pendingPinToggles[articleId];
        // Send the *opposite* POST to restore the previous state.
        this._applyPinVisual(articleId, wasPinned);
        this._pinPopByAid(articleId, wasPinned);
        this._commitPinToggle(articleId, wasPinned);
        return;
      }
      // Send the POST now (no delay).
      this._commitPinToggle(articleId, newPinned);
      // Stage a 5-second undo window: visual stays flipped; if the
      // user clicks again we restore above.
      const timer = setTimeout(() => {
        delete this._pendingPinToggles[articleId];
      }, 5000);
      this._pendingPinToggles[articleId] = { newPinned, timer };
    },

    // Apply the optimistic pin state to BOTH the articles[] list
    // (the "全部" tab) and pinnedArticles[] (the "Pin" tab) plus
    // the detail modal.  Kept in sync so a single click visually
    // updates everywhere regardless of which tab is open.
    _applyPinVisual(articleId, isPinned) {
      const pinnedAt = isPinned ? new Date().toISOString() : null;
      this.articles = this.articles.map(a =>
        a.id === articleId ? { ...a, is_pinned: isPinned, pinned_at: pinnedAt } : a
      );
      // Update pinnedTotal so the tab badge (e.g. "Pin (6)") stays
      // accurate even before the user re-loads the Pin tab.  We
      // detect whether this article was already in pinnedArticles
      // (transition: was-pinned → not-pinned OR was-not-pinned →
      // pinned) to decide whether to +/- 1.
      const wasInPinned = this.pinnedArticles.some(a => a.id === articleId);
      if (isPinned && !wasInPinned) {
        this.pinnedTotal = (this.pinnedTotal || 0) + 1;
      } else if (!isPinned && wasInPinned) {
        this.pinnedTotal = Math.max(0, (this.pinnedTotal || 0) - 1);
      }
      // If the article isn't in articles[] yet (e.g. user is on
      // Pin tab, page 2, and toggled an article we don't have a
      // full copy of), fetch the article from pinnedArticles or
      // synthesise a stub so the Pin tab UI has SOMETHING to display
      // after a re-pin.  Without this the row would vanish and
      // reappear only on the next /pinned refetch.
      if (isPinned) {
        const inPinned = this.pinnedArticles.find(a => a.id === articleId);
        if (!inPinned) {
          const src = this.articles.find(a => a.id === articleId)
                   || this.pinnedArticles.find(a => a.id === articleId)
                   || { id: articleId, is_pinned: true, pinned_at: pinnedAt };
          const enriched = { ...src, id: articleId, is_pinned: true, pinned_at: pinnedAt };
          this.pinnedArticles = [enriched, ...this.pinnedArticles];
        }
      } else {
        this.pinnedArticles = this.pinnedArticles.filter(a => a.id !== articleId);
      }
      if (this.articleDetail && this.articleDetail.id === articleId) {
        this.articleDetail = { ...this.articleDetail, is_pinned: isPinned, pinned_at: pinnedAt };
      }
    },

    // Find the DOM button for an article and trigger the GSAP pop.
    // Uses data-aid so we don't have to keep a separate ref map.
    _pinPopByAid(articleId, filled) {
      // Defer to next tick so the class flip (.is-pinned) lands first.
      requestAnimationFrame(() => {
        const btn = document.querySelector(`.pin-btn[data-aid="${articleId}"]`);
        if (btn && window.pinPop) window.pinPop(btn, filled);
      });
    },

    // Send the actual POST.  Reverts the visual on failure (rare —
    // usually means the article was deleted or 405 because the
    // backend is running the old code).  On success, reconcile
    // the server timestamp into BOTH list arrays so the Pin tab
    // and the "全部" tab stay in sync.
    _commitPinToggle(articleId, isPinned) {
      fetch(`/api/articles/${articleId}/pin?on=${isPinned}`, {
        method: 'POST',
        credentials: 'same-origin',
      })
        .then(r => r.json().then(d => r.ok ? d : Promise.reject(d)))
        .then(data => {
          // Reconcile into BOTH lists (articles[] + pinnedArticles[]).
          const newPinned = data.is_pinned;
          const newPinnedAt = data.pinned_at;
          this.articles = this.articles.map(a =>
            a.id === articleId ? { ...a, is_pinned: newPinned, pinned_at: newPinnedAt } : a
          );
          // Apply the same patch into pinnedArticles[] (the article
          // may be present in either or both, depending on which tab
          // the user is on and the pagination state).
          if (newPinned) {
            const existing = this.pinnedArticles.find(a => a.id === articleId);
            if (existing) {
              this.pinnedArticles = this.pinnedArticles.map(a =>
                a.id === articleId ? { ...a, is_pinned: true, pinned_at: newPinnedAt } : a
              );
            } else {
              const src = this.articles.find(a => a.id === articleId);
              const enriched = src
                ? { ...src, is_pinned: true, pinned_at: newPinnedAt }
                : { id: articleId, is_pinned: true, pinned_at: newPinnedAt };
              this.pinnedArticles = [enriched, ...this.pinnedArticles];
            }
          } else {
            this.pinnedArticles = this.pinnedArticles.filter(a => a.id !== articleId);
          }
          if (this.articleDetail && this.articleDetail.id === articleId) {
            this.articleDetail = {
              ...this.articleDetail,
              is_pinned: newPinned,
              pinned_at: newPinnedAt,
            };
          }
        })
        .catch(err => {
          this._applyPinVisual(articleId, !isPinned);
          let msg = '';
          if (err && err.detail) {
            if (typeof err.detail === 'string') msg = err.detail;
            else if (Array.isArray(err.detail)) {
              msg = err.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
            } else msg = JSON.stringify(err.detail);
          } else if (err && err.message) {
            msg = err.message;
          } else {
            msg = JSON.stringify(err);
          }
          if (err && err.status === 405) {
            msg = '后端不支持 Pin 接口 (405). 请重启 8900 服务 (v1.0.8 部署后). ' + msg;
          }
          this._notify('error', 'Pin 切换失败: ' + msg);
        });
    },

    // v1.0.8.4: check pin state across BOTH lists (and the
    // detail modal) so the toggle works regardless of which tab
    // is active.  v1.0.8.1–8.3 only looked at articles[] which
    // missed the Pin-tab-only case.
    _isArticlePinned(articleId) {
      // 1. The Pin tab's local list (most up-to-date on the Pin tab)
      const pa = (this.pinnedArticles || []).find(x => x.id === articleId);
      if (pa) return !!pa.is_pinned;
      // 2. The "全部" tab's local list
      const a = (this.articles || []).find(x => x.id === articleId);
      if (a) return !!a.is_pinned;
      // 3. The detail modal
      if (this.articleDetail && this.articleDetail.id === articleId) {
        return !!this.articleDetail.is_pinned;
      }
      return false;
    },

    // ── Pin sub-tab (v1.0.8) ─────────────────────────────────────
    // 5-second undo window after a bulk unpin.  Memory-only: if the
    // user refreshes the page, the staged articles stay unpinned
    // (per the user's decision 4 in the plan).
    pinUndoBatch: null,  // { articles: [...], secondsLeft: 5, timer: <interval> }
    // v1.0.8.1: pending single-article toggles.  Map<articleId, {newPinned, timer}>.
    // The user can double-click within 5s to cancel; otherwise the
    // timer fires and the actual POST is sent.  Keeps single-pin
    // behaviour consistent with bulk-unpin.
    _pendingPinToggles: {},
    get pinUndoActive() { return !!this.pinUndoBatch; },

    startPinUndo(stagedArticles) {
      // Cancel any prior undo window — the new batch supersedes it.
      if (this.pinUndoBatch && this.pinUndoBatch.timer) {
        clearInterval(this.pinUndoBatch.timer);
      }
      const batch = {
        articles: stagedArticles,
        secondsLeft: 5,
        timer: null,
      };
      batch.timer = setInterval(() => {
        batch.secondsLeft -= 1;
        if (batch.secondsLeft <= 0) {
          clearInterval(batch.timer);
          this.pinUndoBatch = null;
          this._commitPinUndo(batch.articles);
        } else {
          // Trigger Alpine reactivity by reassigning the object.
          this.pinUndoBatch = { ...batch };
        }
      }, 1000);
      this.pinUndoBatch = batch;
    },

    cancelPinUndo() {
      if (!this.pinUndoBatch) return;
      if (this.pinUndoBatch.timer) clearInterval(this.pinUndoBatch.timer);
      // v1.0.8.1: re-pin via togglePin so the actual POST goes
      // out (we already un-pinned on click).  Each article's
      // single-toggle 5s window starts now.  We don't restore to
      // pinnedArticles[] here — togglePin will add them back
      // optimistically.
      const restored = this.pinUndoBatch.articles.slice();
      this.pinUndoBatch = null;
      this._repinBatch(restored);
    },

    setArticlesSubTab(tab) {
      if (tab === this.articlesSubTab) return;
      this.articlesSubTab = tab;
      this.pinnedSelectedIds = [];  // reset selection on tab switch
      if (tab === 'pinned') this.loadPinnedArticles();
    },

    pinnedPrevPage() {
      if (this.pinnedPage > 1) { this.pinnedPage -= 1; this.loadPinnedArticles(); }
    },

    pinnedNextPage() {
      if (this.pinnedHasMore) { this.pinnedPage += 1; this.loadPinnedArticles(); }
    },

    pinnedChangePageSize() {
      this.pinnedPage = 1;
      this.loadPinnedArticles();
    },

    loadPinnedArticles() {
      const params = new URLSearchParams();
      params.set('page', this.pinnedPage);
      params.set('page_size', this.pinnedPageSize);
      params.set('sort', this.pinnedSort);
      if (this.articlesSource) params.set('sources', this.articlesSource);
      if (this.articlesTag) params.set('tags', this.articlesTag);
      fetch(`/api/articles/pinned?${params}`, { credentials: 'same-origin' })
        .then(r => r.json().then(d => r.ok ? d : Promise.reject(d)))
        .then(data => {
          this.pinnedArticles = data.items || [];
          this.pinnedTotal = data.total || 0;
          this.pinnedHasMore = (this.pinnedPage * this.pinnedPageSize) < this.pinnedTotal;
          this.refreshPinnedQuality();
        })
        .catch(err => console.error('loadPinnedArticles failed:', err));
    },

    // ── v1.0.96 Pin quality column ───────────────────────────────
    // The server never writes on GET; rows whose formula is missing or
    // outdated are refreshed once per page load.
    pinQualityCell(a) {
      const q = a.quality || {};
      if (q.status === 'failed') return '—';
      if (q.formula === 'pin_heuristic_v3' && q.status === 'ready' && q.score != null) {
        return Number(q.score).toFixed(1);
      }
      return '…';
    },

    pinQualityTip(a) {
      const q = a.quality || {};
      if (q.status === 'failed') return q.error || '质量分计算失败';
      if (q.formula !== 'pin_heuristic_v3' || !q.breakdown) return '尚未计算';
      const items = q.breakdown.items || [];
      const rows = [
        '本地算的完整度，5 分制，不评内容好坏',
        '四项全满才到 5.0，满分即 5.0',
      ];
      const byKey = {};
      items.forEach(i => { byKey[i.key] = i; });
      const fmt = d => `${d >= 0 ? '+' : ''}${Number(d).toFixed(1)}`;
      if (byKey.length) rows.push(`正文长度：${byKey.length.detail || '—'}，${fmt(byKey.length.delta)}`);
      if (byKey.substance) rows.push(`数字与结构：${byKey.substance.detail || '无'}，${fmt(byKey.substance.delta)}`);
      if (byKey.publisher) rows.push(`发布方：${byKey.publisher.delta ? fmt(byKey.publisher.delta) : '无'}`);
      if (byKey.published_at) rows.push(`发布时间：${byKey.published_at.delta ? fmt(byKey.published_at.delta) : '无'}`);
      rows.push(`底分 ${Number(q.breakdown.base || 2.5).toFixed(1)}`);
      return rows.join('\n');
    },

    refreshPinnedQuality() {
      if (this._qualityRefreshing) return;
      // Only rows whose formula is missing/outdated are refreshed; `failed`
      // rows keep the dash + error tip instead of looping the endpoint.
      const stale = (this.pinnedArticles || []).filter(
        a => (a.quality || {}).formula !== 'pin_heuristic_v3'
      );
      if (!stale.length) return;
      this._qualityRefreshing = true;
      fetch('/api/articles/pins/quality-refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ article_ids: stale.map(a => a.id) }),
      })
        .then(r => r.json())
        .then(data => {
          if ((data.updated || []).length) {
            this.loadPinnedArticles();
          }
        })
        .catch(err => console.warn('quality-refresh failed:', err))
        .finally(() => { this._qualityRefreshing = false; });
    },

    // v1.0.8: share page chip handler.  selectedArticleIds is NOT
    // reset on toggle — the user's in-progress share is preserved
    // even if they switch modes to grab more articles.
    setShareArticleSource(src) {
      if (src === this.shareArticleSource) return;
      this.shareArticleSource = src;
      // Re-page both lists to be safe (the share page may have a
      // different filter context than the articles page).
      if (src === 'pinned') this.loadPinnedArticles();
      else this.loadAllArticles();
    },

    // Bulk-action helpers for the Pin tab.
    pinnedAllSelectedOnPage() {
      if (!this.pinnedArticles || this.pinnedArticles.length === 0) return false;
      return this.pinnedArticles.every(a => this.pinnedSelectedIds.includes(a.id));
    },
    pinnedToggleAllOnPage(ev) {
      const wantSelected = ev.target.checked;
      const pageIds = this.pinnedArticles.map(a => a.id);
      if (wantSelected) {
        // Add all page ids that aren't already in the selection.
        const set = new Set(this.pinnedSelectedIds);
        pageIds.forEach(id => set.add(id));
        this.pinnedSelectedIds = Array.from(set);
      } else {
        // Remove page ids from the selection.
        const set = new Set(this.pinnedSelectedIds);
        pageIds.forEach(id => set.delete(id));
        this.pinnedSelectedIds = Array.from(set);
      }
    },
    pinnedSelectAll() {
      const set = new Set(this.pinnedSelectedIds);
      this.pinnedArticles.forEach(a => set.add(a.id));
      this.pinnedSelectedIds = Array.from(set);
    },
    pinnedSelectNone() {
      this.pinnedSelectedIds = [];
    },

    // Bulk unpin — v1.0.8.1: send the actual POST IMMEDIATELY (not
    // delayed) so that switching tabs and back doesn't show stale
    // "still pinned" rows.  The 5-second undo window re-pins the
    // articles if the user clicks 撤销.  Net effect on the backend
    // is the same as the old flow, but the visual state is honest.
    bulkUnpin() {
      if (this.pinnedSelectedIds.length === 0) return;
      const ids = [...this.pinnedSelectedIds];
      const staged = this.pinnedArticles.filter(a => ids.includes(a.id));
      // Optimistically remove from the visible pinned list.
      this.pinnedArticles = this.pinnedArticles.filter(a => !ids.includes(a.id));
      this.pinnedTotal = Math.max(0, this.pinnedTotal - ids.length);
      this.pinnedSelectedIds = [];
      // Fire the actual bulk-unpin POST right away.  The undo
      // window re-pins via per-article POST if the user clicks 撤销.
      this._commitBulkUnpin(ids);
      // Start the 5-second undo window so the user can re-pin.
      this.startPinUndo(staged);
    },

    _commitBulkUnpin(ids) {
      fetch('/api/articles/pins/bulk-unpin', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ article_ids: ids }),
      })
        .then(r => r.json().then(d => r.ok ? d : Promise.reject(d)))
        .catch(err => {
          console.error('bulk unpin POST failed:', err);
          // Don't alert: the user can manually re-pin via single-toggle.
        });
    },

    // Re-pin each article in the staged batch.  Used by the undo
    // button.  Each article goes through togglePin so the GSAP
    // pop + 5s single-undo window applies individually.
    _repinBatch(articles) {
      if (!articles || articles.length === 0) return;
      for (const a of articles) {
        // Use the existing togglePin pipeline (it has the 5s undo
        // window per article).  We re-insert into pinnedArticles and
        // articles[] first so the optimistic state is correct.
        if (!this.articles.find(x => x.id === a.id)) {
          this.articles = [a, ...this.articles];
        }
        this.togglePin(a.id);
      }
    },

    generateSummary() {
      if (!this.articleDetail) return;
      const self = this;
      self.summaryLoading = true;
      fetch(`/api/articles/${this.articleDetail.id}/summary`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ article_id: this.articleDetail.id }),
      })
        .then(r => {
          if (!r.ok) {
            return r.json().catch(() => ({})).then(err => { throw new Error(err.message || 'HTTP ' + r.status); });
          }
          return r.json();
        })
        .then(data => {
          // v1.0.5.1: server now returns 202 + job_id instead of the
          // summary text.  Jump to the LLM jobs panel so the user can
          // see the job's status (pending → running → done/failed) in
          // real time.  We don't close the modal so the user can keep
          // reading the source article while the LLM works.
          self.enqueueLlmJob('summary', data.job_id, self.articleDetail.id);
          // Optimistically mark the article as "in flight" so the user
          // can see the job is queued even before the panel polls.
          self.articleDetail = {
            ...self.articleDetail,
            ...self._articleJobPlaceholder('summary', data.job_id),
          };
          self.watchArticleJob(data.job_id, self.articleDetail.id, 'summary');
        })
        .catch(err => { this._notify('error', '生成摘要失败: ' + err.message); })
        .finally(() => { self.summaryLoading = false; });
    },

    generateFactCheck() {
      if (!this.articleDetail) return;
      const self = this;
      self.factCheckLoading = true;
      fetch(`/api/articles/${this.articleDetail.id}/fact-check`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ article_id: this.articleDetail.id }),
      })
        .then(r => {
          if (!r.ok) {
            return r.json().catch(() => ({})).then(err => { throw new Error(err.message || 'HTTP ' + r.status); });
          }
          return r.json();
        })
        .then(data => {
          // v1.0.5.1: same async treatment as generateSummary().
          self.enqueueLlmJob('fact_check', data.job_id, self.articleDetail.id);
          self.articleDetail = {
            ...self.articleDetail,
            ...self._articleJobPlaceholder('fact_check', data.job_id),
          };
          self.watchArticleJob(data.job_id, self.articleDetail.id, 'fact_check');
        })
        .catch(err => {
          const raw = String(err.message || '').trim();
          this._notify(
            'error',
            this._llmErrorText(err.message, '事实核查没能排上队，可以稍后重试。'),
            raw && raw !== 'unknown' ? { detail: raw } : undefined
          );
        })
        .finally(() => { self.factCheckLoading = false; });
    },

    // v1.0.22: 联网核查 (web_search, 证据链写 fact_check_evidence 表)
    enqueueLlmJobFactCheckPlus(articleId) {
      const id = articleId || (this.articleDetail && this.articleDetail.id);
      if (!id) return;
      const self = this;
      self.factCheckLoading = true;
      fetch(`/api/articles/${id}/fact-check-plus`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({}),
      })
        .then(r => {
          if (!r.ok) {
            return r.json().catch(() => ({})).then(err => { throw new Error(err.message || 'HTTP ' + r.status); });
          }
          return r.json();
        })
        .then(data => {
          // v1.0.22: same async treatment as generateFactCheck().
          self.enqueueLlmJob('fact_check_plus', data.job_id, id);
          self.articleDetail = {
            ...self.articleDetail,
            ...self._articleJobPlaceholder('fact_check_plus', data.job_id),
          };
          self.watchArticleJob(data.job_id, id, 'fact_check_plus');
        })
        .catch(err => {
          const raw = String(err.message || '').trim();
          this._notify(
            'error',
            this._llmErrorText(err.message, '联网核查没能排上队，可以稍后重试。'),
            raw ? { detail: raw } : undefined
          );
        })
        .finally(() => { self.factCheckLoading = false; });
    },

    // 把 Hermes / LLM 的原始报错翻成人话；原文放进 toast 的「查看原因」。
    // 熔断、会话异常、超时、解析失败各有不同说法，避免用户看到 RuntimeError 栈。
    _llmErrorText(raw, fallback) {
      const text = String(raw || '');
      if (/breaker OPEN|failover exhausted/i.test(text)) {
        return '模型服务正在熔断恢复中，暂时排不上新请求。已保留上次核查结果，约 10 分钟后可重试。';
      }
      if (/session_replay_gap/i.test(text)) {
        return '模型会话异常，这次核查没跑完。已保留上次核查结果，可以稍后重试。';
      }
      if (/timeout|timed out/i.test(text)) {
        return '模型在时限内没返回，这次核查没跑完。已保留上次核查结果，可以稍后重试。';
      }
      if (/parse failed|no evidence/i.test(text)) {
        return '模型返回的内容不完整，这次核查没跑完。已保留上次核查结果，可以稍后重试。';
      }
      return fallback;
    },

    // v1.0.106: 文章级 LLM 任务统一跟随（摘要 / 快核 / 联网核查）。
    //
    // 钉住的失败模式：摘要与快核设完「⏳ …」占位就再没人管；联网核查只轮询
    // 60 秒（线上实测 2~5 分钟），超时后占位永久留在页面上，用户只能手动
    // 刷新或重进页面。现在统一成一个跟随器：
    //   · 间隔按耗时退避，预算 30 分钟，覆盖最慢的真实任务；
    //   · done / failed / cancelled 都回读文章详情，占位一定被服务端真值替换；
    //   · 打开文章时按 article_id 查活跃任务续上，重进页面自动同步。
    _articleJobWatchers: new Set(),

    _isArticleJobType(taskType) {
      return taskType === 'summary' || taskType === 'fact_check' || taskType === 'fact_check_plus';
    },

    _articleJobLabel(taskType) {
      const labels = {
        summary: '摘要生成',
        fact_check: '事实核查',
        fact_check_plus: '联网核查',
      };
      return labels[taskType] || '任务';
    },

    _articleJobPlaceholder(taskType, jobId) {
      const text = `⏳ ${this._articleJobLabel(taskType)}中... (job ${String(jobId).slice(0, 8)})`;
      return taskType === 'summary' ? { summary: text } : { fact_check_conclusion: text };
    },

    watchArticleJob(jobId, articleId, taskType) {
      if (!jobId) return;
      if (!this._articleJobWatchers) this._articleJobWatchers = new Set();
      if (this._articleJobWatchers.has(jobId)) return;   // 同一任务只跟一次
      this._articleJobWatchers.add(jobId);
      const self = this;
      const startedAt = Date.now();
      const budgetMs = 30 * 60 * 1000;
      const settle = () => { self._articleJobWatchers.delete(jobId); };
      const tick = () => {
        fetch(`/api/llm/jobs/${jobId}`, { credentials: 'same-origin' })
          .then(r => {
            if (r.status === 404) {
              const gone = new Error('job not found');
              gone.status = 404;
              throw gone;
            }
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
          })
          .then(job => {
            if (job.status === 'done') {
              settle();
              if (taskType === 'fact_check_plus') self.loadArticleEvidence(articleId);
              self._notify('success', self._articleJobLabel(taskType) + '完成');
              self._reloadArticleAfterJob(articleId);
              return;
            }
            if (job.status === 'failed' || job.status === 'cancelled') {
              settle();
              const raw = String(job.error || '').trim();
              self._notify(
                'error',
                self._llmErrorText(raw, self._articleJobLabel(taskType) + '没跑完，可以稍后重试。'),
                raw && raw !== 'unknown' ? { detail: raw } : undefined
              );
              self._reloadArticleAfterJob(articleId);
              return;
            }
            const elapsed = Date.now() - startedAt;
            if (elapsed > budgetMs) {
              settle();
              self._reloadArticleAfterJob(articleId);
              self._notify('warn', self._articleJobLabel(taskType) + '仍在后台运行，稍后再进本文会自动同步。');
              return;
            }
            setTimeout(tick, elapsed < 60000 ? 3000 : elapsed < 180000 ? 6000 : 15000);
          })
          .catch(err => {
            if (err && err.status === 404) {
              // 任务被清掉了（例如清理脚本或误传 jobId）：不要再空转 30 分钟。
              settle();
              self._reloadArticleAfterJob(articleId);
              return;
            }
            if (Date.now() - startedAt > budgetMs) { settle(); return; }
            setTimeout(tick, 5000);
          });
      };
      setTimeout(tick, 2000);
    },

    /** 任务收尾后回读服务端真值：占位一定被真实结论替换，不会卡住。 */
    _reloadArticleAfterJob(articleId) {
      const self = this;
      return fetch(`/api/articles/${articleId}`, { credentials: 'same-origin' })
        .then(r => r.ok ? r.json() : null)
        .then(detail => { if (detail) self.readerRefreshDetail(articleId, detail); })
        .catch(() => {});
    },

    /** 打开文章时把还在跑的摘要/核查任务续上，重进页面也能自动同步。 */
    resumeArticleJobs(articleId) {
      if (!articleId) return;
      const self = this;
      const load = status => fetch(
        `/api/llm/jobs?article_id=${encodeURIComponent(articleId)}&status=${status}&limit=20`,
        { credentials: 'same-origin' },
      )
        .then(r => r.ok ? r.json() : [])
        .catch(() => []);
      // 两个 status 合并成一次赋值：并发回调各自展开 articleDetail 会互相覆盖。
      Promise.all([load('pending'), load('running')]).then(results => {
        if (!self.isCurrentReaderArticle(articleId)) return;
        const jobs = [].concat(...results.filter(Array.isArray)).filter(
          job => job && job.id && self._isArticleJobType(job.task_type) && !self._articleJobWatchers.has(job.id),
        );
        if (!jobs.length) return;
        const placeholders = {};
        jobs.forEach(job => Object.assign(placeholders, self._articleJobPlaceholder(job.task_type, job.id)));
        self.articleDetail = { ...self.articleDetail, ...placeholders };
        jobs.forEach(job => self.watchArticleJob(job.id, articleId, job.task_type));
      }).catch(() => {});
    },

    /** 这条异步结果是否还属于阅读器里正在看的那篇。 */
    isCurrentReaderArticle(articleId) {
      return !!this.articleDetail && this.articleDetail.id === articleId;
    },

    /** 迟到的核查结果不能盖掉用户已经翻开的另一篇。 */
    readerRefreshDetail(articleId, detail) {
      if (!detail) return;
      // 阅读器已关（articleDetail 为 null）时也不能把模态弹回来。
      if (!this.isCurrentReaderArticle(articleId)) return;
      this.articleDetail = detail;
    },

    // v1.0.22: 拉取联网核查证据链 (后端已按 evidence_seq 升序)
    loadArticleEvidence(articleId) {
      const self = this;
      fetch(`/api/fact-check-plus/evidence/${articleId}`, {
        method: 'GET',
        credentials: 'same-origin',
      })
        .then(r => {
          if (!r.ok) {
            return r.json().catch(() => ({})).then(err => { throw new Error(err.message || 'HTTP ' + r.status); });
          }
          return r.json();
        })
        .then(data => {
          // 换篇后迟到的证据链不能写给上一篇，否则 B 的证据区显示 A 的条目。
          if (!self.isCurrentReaderArticle(articleId)) return;
          self.articleEvidenceList = data.items;
        })
        .catch(err => { this._notify('error', '加载核查证据失败: ' + err.message); });
    },

    // v1.0.21: 从当前文章手动提取同业案例 (F6 UI 触发, sprint 05 遗留补全)
    extractCase() {
      if (!this.articleDetail) return;
      const self = this;
      self.caseExtractLoading = true;
      fetch(`/api/cases/retry/${encodeURIComponent(this.articleDetail.id)}`, {
        method: 'POST',
        credentials: 'same-origin',
      })
        .then(r => {
          if (!r.ok) {
            return r.json().catch(() => ({})).then(err => { throw new Error(err.detail || 'HTTP ' + r.status); });
          }
          return r.json();
        })
        .then(data => {
          if (data.status === 'completed' && data.case_count > 0) {
            this._notify('success', `提取完成: ${data.case_count} 个案例 → 案例库`);
          } else if (data.status === 'completed' || data.status === 'no_cases') {
            this._notify('info', '该文章没有识别到同业案例');
          } else {
            this._notify('error', '案例提取失败: ' + (data.error || data.status));
          }
        })
        .catch(err => { this._notify('error', '提取案例失败: ' + err.message); })
        .finally(() => { self.caseExtractLoading = false; });
    },

    // ── LLM jobs panel (v1.0.5.1) ─────────────────────────────────
    //
    // The "LLM 任务" view is a single rolling dashboard of all jobs
    // the worker has ever handled.  A 1-second poll updates the table
    // while there is at least one pending/running row; once the queue
    // is empty the poll backs off to 5 seconds.
    llmJobs: [],
    llmJobsStats: { pending: 0, running: 0, done: 0, failed: 0, cancelled: 0 },
    llmJobsLoading: false,
    llmJobsPollHandle: null,
    llmJobsLastFetched: 0,

    enqueueLlmJob(taskType, jobId, articleId) {
      // Called by generateSummary / generateFactCheck when the user
      // kicks off a job.  Switch to the LLM-jobs panel, scroll to the
      // new row, and start polling.
      this.navigate('llmJobs');
      this.loadLlmJobs();
      // The poll loop is started by loadLlmJobs() — we don't need to
      // call it again here.
    },

    loadLlmJobs() {
      const self = this;
      if (self.llmJobsLoading) return;
      self.llmJobsLoading = true;
      Promise.all([
        fetch('/api/llm/jobs?limit=100', { credentials: 'same-origin' }).then(r => r.json()),
        fetch('/api/llm/jobs/stats/summary', { credentials: 'same-origin' }).then(r => r.json()),
      ])
        .then(([jobs, stats]) => {
          self.llmJobs = Array.isArray(jobs) ? jobs : [];
          self.llmJobsStats = stats || self.llmJobsStats;
          self.llmJobsLastFetched = Date.now();
        })
        .catch(err => { console.error('loadLlmJobs failed:', err); })
        .finally(() => {
          self.llmJobsLoading = false;
          // Schedule the next poll.  Use a tighter interval while
          // there is in-flight work; back off when the queue is empty.
          const inFlight = (self.llmJobsStats.pending || 0) + (self.llmJobsStats.running || 0);
          const delay = inFlight > 0 ? 1000 : 5000;
          if (self.llmJobsPollHandle) clearTimeout(self.llmJobsPollHandle);
          self.llmJobsPollHandle = setTimeout(() => self.loadLlmJobs(), delay);
        });
    },

    async cancelLlmJob(jobId) {
      if (!(await moniModal.confirm('取消这个 LLM 任务? 已生成的内容会保留, 但状态会标记为 cancelled.', { title: '取消 LLM 任务', okLabel: '取消任务', danger: true }))) return;
      const self = this;
      fetch(`/api/llm/jobs/${jobId}/cancel`, {
        method: 'POST',
        credentials: 'same-origin',
      })
        .then(r => {
          if (!r.ok) {
            return r.json().catch(() => ({})).then(err => { throw new Error(err.message || 'HTTP ' + r.status); });
          }
          return r.json();
        })
        .then(() => { self.loadLlmJobs(); })
        .catch(err => { this._notify('error', '取消失败: ' + err.message); });
    },

    llmJobStatusBadge(status) {
      // Map backend status to a short Chinese label + CSS class.
      const map = {
        pending:   { label: '排队中', cls: 'badge-pending' },
        running:   { label: '运行中', cls: 'badge-running' },
        done:      { label: '完成',   cls: 'badge-done' },
        failed:    { label: '失败',   cls: 'badge-failed' },
        cancelled: { label: '已取消', cls: 'badge-cancelled' },
      };
      return map[status] || { label: status, cls: 'badge-unknown' };
    },

    formatDuration(ms) {
      if (ms == null) return '-';
      if (ms < 1000) return ms + 'ms';
      const s = Math.floor(ms / 1000);
      if (s < 60) return s + 's';
      const m = Math.floor(s / 60);
      const rem = s % 60;
      return m + 'm' + rem + 's';
    },

    // ── v1.0.5.2: per-row expand/collapse for result-cell ─────────────
    //
    // The job table truncates long LLM outputs (and long error
    // messages) by default to keep the table compact.  Each row has a
    // "查看全文" / "收起" toggle; the state is local to the SPA
    // session, not persisted.
    expandedJobIds: new Set(),

    isJobExpanded(jobId) {
      return this.expandedJobIds.has(jobId);
    },

    toggleJobExpand(jobId) {
      if (this.expandedJobIds.has(jobId)) {
        this.expandedJobIds.delete(jobId);
      } else {
        this.expandedJobIds.add(jobId);
      }
      // Force Alpine to re-render the bound classes.  Alpine 3
      // generally tracks Set mutations but Set.add / Set.delete
      // without re-assignment can sometimes be missed; trigger
      // reactivity by reassigning the property.
      this.expandedJobIds = new Set(this.expandedJobIds);
    },

    // ── v1.0.5.14: LLM job detail modal ───────────────────────────
    // Replaces the inline "查看全文" cell-expand: opening a row's "查看详情"
    // / "查看错误" button now opens a wide modal showing the full result
    // (or error) in a scrollable pre-formatted block, with a copy-to-
    // clipboard button. The legacy expandedJobIds Set is kept on the
    // object for backwards compatibility but is no longer wired to UI.
    jobDetail: null,
    jobDetailCopied: false,
    jobDetailFactCheck: null,

    openJobDetail(job) {
      this.jobDetail = job;
      this.jobDetailCopied = false;
      this.jobDetailFactCheck = null;
      // v1.0.35.13: 联网核查 done — 拉 detail 拿结构化证据 (fact_check 字段),
      // 用于用户友好的卡片渲染 (而非直接把 LLM 原始 JSON 甩给用户)。
      if (job && job.task_type === 'fact_check_plus' && job.status === 'done' && job.id) {
        const self = this;
        fetch(`/api/llm/jobs/${encodeURIComponent(job.id)}`, {
          method: 'GET', credentials: 'same-origin',
        })
          .then(r => r.json())
          .then(detail => {
            if (detail && detail.fact_check) {
              self.jobDetailFactCheck = detail.fact_check;
            }
          })
          .catch(err => {
            // 非致命: 拿不到结构化数据就退回原始 JSON 展示
            console.warn('load fact_check detail failed:', err);
          });
      }
    },

    closeJobDetail() {
      this.jobDetail = null;
      this.jobDetailCopied = false;
      this.jobDetailFactCheck = null;
    },

    async copyJobDetail() {
      if (!this.jobDetail) return;
      const text = this.jobDetail.status === 'failed'
        ? (this.jobDetail.error || '')
        : (this.jobDetail.result || '');
      if (!text) return;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
        } else {
          // Fallback for non-secure contexts (http://127.0.0.1 is fine,
          // but this also covers file:// or older browsers).
          const ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
        }
        this.jobDetailCopied = true;
        setTimeout(() => { this.jobDetailCopied = false; }, 1500);
      } catch (err) {
        console.error('copyJobDetail failed:', err);
        this._notify('error', '复制失败: ' + (err && err.message ? err.message : String(err)));
      }
    },

    // ── LLM Settings panel ───────────────────────────────────────
    llmTab: 'gateway',
    llmProviders: [],
    llmModels: [],
    llmGateway: null,
    llmGatewayFilter: '',
    llmSettings: {},
    llmSettingsDraft: {
      llm_model_mode: 'profile',
      active_summary_model_id: '',
      active_fact_check_model_id: '',
    },

    // Provider form modal state
    llmProviderFormOpen: false,
    llmProviderForm: { id: null, name: '', hermes_provider: '', notes: '' },
    llmProviderFormSaving: false,
    llmProviderFormError: '',

    // Model form modal state
    llmModelFormOpen: false,
    llmModelForm: { id: null, provider_id: '', model_id: '', display_name: '', use_for_text: '' },
    llmModelFormSaving: false,
    llmModelFormError: '',

    // Usage stats state
    llmUsage: null,
    llmUsageDays: 30,
    llmUsageGroupBy: 'module',

    switchLlmTab(tab) {
      this.llmTab = tab;
      if (tab === 'gateway') {
        this.loadLlmGateway();
      }
      if (tab === 'usage') {
        this.loadLlmUsage();
      }
    },

    loadLlmGateway() {
      fetch('/api/llm/gateway', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => { this.llmGateway = data; })
        .catch(err => {
          console.error('loadLlmGateway failed:', err);
          this.llmGateway = null;
        });
    },

    llmProviderName(providerId) {
      const p = this.llmProviders.find(x => x.id === providerId);
      return p ? p.name : providerId;
    },

    loadLlmProviders() {
      fetch('/api/llm/providers', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          const items = Array.isArray(data) ? data : (data.items || []);
          // Initialize reactive _testing flag for each provider
          items.forEach(p => { p._testing = false; });
          this.llmProviders = items;
        })
        .catch(err => { console.error('loadLlmProviders failed:', err); this.llmProviders = []; });
    },

    loadLlmModels() {
      fetch('/api/llm/models', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => { this.llmModels = Array.isArray(data) ? data : (data.items || []); })
        .catch(err => { console.error('loadLlmModels failed:', err); this.llmModels = []; });
    },

    loadLlmSettings() {
      fetch('/api/llm/settings', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          this.llmSettings = data || {};
          this.llmSettingsDraft = {
            llm_model_mode: data.llm_model_mode || 'profile',
            active_summary_model_id: data.active_summary_model_id || '',
            active_fact_check_model_id: data.active_fact_check_model_id || '',
          };
        })
        .catch(err => { console.error('loadLlmSettings failed:', err); });
    },

    loadLlmUsage() {
      const params = new URLSearchParams();
      params.set('days', this.llmUsageDays);
      params.set('group_by', this.llmUsageGroupBy);
      fetch('/api/llm/usage?' + params.toString(), { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => { this.llmUsage = data; })
        .catch(err => { console.error('loadLlmUsage failed:', err); this.llmUsage = null; });
    },

    llmBarWidth(item, groupKey) {
      // Calculate bar width as percentage of max value in the group
      const items = this.llmUsage?.[groupKey] || [];
      if (items.length === 0) return 0;
      const maxVal = Math.max(...items.map(x => x.total_tokens ?? x.tokens ?? 0));
      if (maxVal === 0) return 0;
      const val = item.total_tokens ?? item.tokens ?? 0;
      return Math.max(1, Math.round((val / maxVal) * 100));
    },

    // ── Provider CRUD ────────────────────────────────────────────
    openLlmProviderForm(provider) {
      this.llmProviderFormError = '';
      this.llmProviderFormSaving = false;
      if (provider) {
        this.llmProviderForm = {
          id: provider.id,
          name: provider.name || '',
          hermes_provider: provider.hermes_provider || '',
          notes: provider.notes || '',
        };
      } else {
        this.llmProviderForm = { id: null, name: '', hermes_provider: '', notes: '' };
      }
      this.llmProviderFormOpen = true;
    },

    closeLlmProviderForm() {
      this.llmProviderFormOpen = false;
      this.llmProviderFormError = '';
    },

    saveLlmProvider() {
      if (!this.llmProviderForm.name.trim() || !this.llmProviderForm.hermes_provider.trim()) {
        this.llmProviderFormError = '显示名和 hermes_provider 不能为空';
        return;
      }
      this.llmProviderFormSaving = true;
      this.llmProviderFormError = '';

      const isEdit = !!this.llmProviderForm.id;
      const body = {
        name: this.llmProviderForm.name.trim(),
        hermes_provider: this.llmProviderForm.hermes_provider.trim(),
        notes: this.llmProviderForm.notes.trim() || null,
      };
      const url = isEdit ? `/api/llm/providers/${this.llmProviderForm.id}` : '/api/llm/providers';
      const method = isEdit ? 'PUT' : 'POST';

      fetch(url, {
        method,
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
        .then(async r => {
          const data = await r.json().catch(() => ({}));
          if (!r.ok) {
            const msg = (data && (data.detail || data.message)) || `${r.status} ${r.statusText}`;
            throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
          }
          return data;
        })
        .then(() => {
          this.loadLlmProviders();
          this.closeLlmProviderForm();
        })
        .catch(err => {
          this.llmProviderFormError = '保存失败: ' + err.message;
        })
        .finally(() => { this.llmProviderFormSaving = false; });
    },

    toggleLlmProviderActive(provider) {
      fetch(`/api/llm/providers/${provider.id}`, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: !provider.is_active }),
      })
        .then(r => {
          if (!r.ok) throw new Error('Toggle failed');
          this.loadLlmProviders();
        })
        .catch(err => this._notify('error', '操作失败: ' + err.message));
    },

    async deleteLlmProvider(provider) {
      if (!(await moniModal.confirm(`确定删除 Provider "${provider.name}"?`, { title: '删除 Provider', okLabel: '删除', danger: true }))) return;
      fetch(`/api/llm/providers/${provider.id}`, {
        method: 'DELETE',
        credentials: 'same-origin',
      })
        .then(async r => {
          if (!r.ok) {
            if (r.status === 409) {
              const data = await r.json().catch(() => ({}));
              throw new Error(data.detail || '该 Provider 下仍有 Model, 请先删除相关 Model');
            }
            throw new Error('Delete failed: HTTP ' + r.status);
          }
          this.loadLlmProviders();
        })
        .catch(err => this._notify('error', '删除失败: ' + err.message));
    },

    async testLlmProvider(provider) {
      // Single confirmation — warns about token usage
      const viaProfile = (this.llmSettings?.llm_model_mode || 'profile') === 'profile';
      const what = viaProfile
        ? `当前模型来源是「跟随 profile」，本次实际测的是 profile 的默认模型（magpie 路由组），不是该 Provider 的模型。`
        : `测试 "${provider.name}" (${provider.hermes_provider}) 连通性？`;
      if (!(await moniModal.confirm(`${what}\n\n⚠️ 将消耗约 5-10 tokens`, { title: '测试 LLM Provider', okLabel: '测试', danger: true }))) return;

      provider._testing = true;

      fetch(`/api/llm/providers/${provider.id}/test`, {
        method: 'POST',
        credentials: 'same-origin',
      })
        .then(r => r.json())
        .then(data => {
          provider._testing = false;
          const via = data.tested === 'profile'
            ? '实际走 profile 当前模型'
            : '实际走该 Provider 的模型';
          if (data.ok) {
            this._notify('success', `✅ 连通成功 — ${via}: ${data.provider} / ${data.model} · 耗时 ${data.duration_ms}ms`, { duration: 6000 });
          } else {
            this._notify('error', `❌ 连通失败 — ${via}: ${data.provider} / ${data.model} · ${data.error || '未知错误'}`);
          }
        })
        .catch(err => {
          provider._testing = false;
          this._notify('error', '测试失败: ' + err.message);
        });
    },

    // ── Model CRUD ───────────────────────────────────────────────
    openLlmModelForm(model) {
      this.llmModelFormError = '';
      this.llmModelFormSaving = false;
      if (model) {
        this.llmModelForm = {
          id: model.id,
          provider_id: model.provider_id || '',
          model_id: model.model_id || '',
          display_name: model.display_name || '',
          use_for_text: (model.use_for || []).join(', '),
        };
      } else {
        this.llmModelForm = {
          id: null,
          provider_id: this.llmProviders[0]?.id || '',
          model_id: '',
          display_name: '',
          use_for_text: '',
        };
      }
      this.llmModelFormOpen = true;
    },

    closeLlmModelForm() {
      this.llmModelFormOpen = false;
      this.llmModelFormError = '';
    },

    saveLlmModel() {
      if (!this.llmModelForm.provider_id || !this.llmModelForm.model_id.trim() || !this.llmModelForm.display_name.trim()) {
        this.llmModelFormError = 'Provider、model_id 和显示名 不能为空';
        return;
      }
      this.llmModelFormSaving = true;
      this.llmModelFormError = '';

      const isEdit = !!this.llmModelForm.id;
      const useFor = this.llmModelForm.use_for_text
        .split(',')
        .map(s => s.trim())
        .filter(Boolean);

      if (isEdit) {
        // PUT only accepts display_name and enabled
        const body = { display_name: this.llmModelForm.display_name.trim() };
        fetch(`/api/llm/models/${this.llmModelForm.id}`, {
          method: 'PUT',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
          .then(async r => {
            const data = await r.json().catch(() => ({}));
            if (!r.ok) {
              const msg = (data && (data.detail || data.message)) || `${r.status}`;
              throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
            }
            return data;
          })
          .then(() => {
            this.loadLlmModels();
            this.closeLlmModelForm();
          })
          .catch(err => { this.llmModelFormError = '保存失败: ' + err.message; })
          .finally(() => { this.llmModelFormSaving = false; });
      } else {
        const body = {
          provider_id: this.llmModelForm.provider_id,
          model_id: this.llmModelForm.model_id.trim(),
          display_name: this.llmModelForm.display_name.trim(),
          use_for: useFor,
        };
        fetch('/api/llm/models', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
          .then(async r => {
            const data = await r.json().catch(() => ({}));
            if (!r.ok) {
              const msg = (data && (data.detail || data.message)) || `${r.status}`;
              throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
            }
            return data;
          })
          .then(() => {
            this.loadLlmModels();
            this.closeLlmModelForm();
          })
          .catch(err => { this.llmModelFormError = '保存失败: ' + err.message; })
          .finally(() => { this.llmModelFormSaving = false; });
      }
    },

    toggleLlmModelEnabled(model) {
      fetch(`/api/llm/models/${model.id}`, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !model.enabled }),
      })
        .then(r => {
          if (!r.ok) throw new Error('Toggle failed');
          this.loadLlmModels();
        })
        .catch(err => this._notify('error', '操作失败: ' + err.message));
    },

    // ── Settings save ────────────────────────────────────────────
    saveLlmSetting(key, value) {
      const body = {};
      body[key] = value || null;
      fetch('/api/llm/settings', {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
        .then(r => {
          if (!r.ok) throw new Error('Save failed');
          // Reload settings to stay in sync
          this.loadLlmSettings();
        })
        .catch(err => this._notify('error', '保存设置失败: ' + err.message));
    },
  };
  if (window.moniReader) {
    // Object.assign evaluates getters once. Copy descriptors so
    // articleFilterCount / filteredArticleSources stay live.
    const descriptors = Object.getOwnPropertyDescriptors(window.moniReader);
    for (const key of Object.keys(descriptors)) {
      Object.defineProperty(api, key, descriptors[key]);
    }
  }
  return api;
}
