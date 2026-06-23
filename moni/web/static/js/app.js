// Root Alpine component — single source of truth for all state and methods
function app() {
  return {
    // ── Theme engine (from theme.js) ──
    ...themeMixin(),

    // ── Service control ──
    serviceRunning: false,
    servicePid: null,

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

    restartService() {
      if (!confirm('确定重启服务？页面会短暂不可用 (~3秒)')) return;
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

    deleteBasketItem() {
      if (!this.basketDetail) return;
      if (!confirm('确定从菜篮子删除？')) return;
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

    // ── Navigation ────────────────────────────────────────────────
    currentView: 'articles',

    // v1.0.5.10: map view name <-> URL path.  Used by navigate() to
    // pushState, and by init() to deep-link from location.pathname.
    _viewToPath: {
      articles: '/articles',
      crud:     '/crud',
      cron:     '/cron',
      fetch:    '/fetch',
      llmJobs:  '/llmJobs',
      share:    '/share',
      basket:   '/basket',
      config:   '/config',
      llmSettings: '/llmSettings',
    },
    _pathToView: {
      '/articles': 'articles',
      '/crud':     'crud',
      '/cron':     'cron',
      '/fetch':    'fetch',
      '/llmJobs':  'llmJobs',
      '/share':    'share',
      '/basket':   'basket',
      '/config':   'config',
      '/llmSettings': 'llmSettings',
    },

    navigate(view) {
      this.currentView = view;
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
      }
      else if (view === 'crud') this.loadCrudArticles();
      else if (view === 'cron') this.loadCronRuns();
      else if (view === 'fetch') this.loadSources();
      else if (view === 'llmJobs') this.loadLlmJobs();  // v1.0.5.1
      else if (view === 'share') { this.loadAllArticles(); this._startSharePoll(); }
      else if (view === 'basket') { this.loadBasket(); this.loadBasketSources(); }
      else if (view === 'config') {
        this.configTab = 'sources';
        this.configLoadSources();
        this.configLoadTags();
        this.configLoadFilters();
      }
      else if (view === 'llmSettings') {
        this.llmTab = 'providers';
        this.loadLlmProviders();
        this.loadLlmModels();
        this.loadLlmSettings();
      }
    },

    // v1.0.5.10: handle browser back/forward.  We only update state, not the
    // URL (the URL already changed via the browser's own history machinery).
    onPopState(event) {
      const view = (event.state && event.state.view) || this._pathToView[window.location.pathname] || 'articles';
      this.currentView = view;
      // Re-run the loaders for the target view.
      if (view === 'articles') {
        this._loadArticlesWithPaging();
        this.loadArticlesFilters();
      }
      else if (view === 'crud') this.loadCrudArticles();
      else if (view === 'cron') this.loadCronRuns();
      else if (view === 'fetch') this.loadSources();
      else if (view === 'llmJobs') this.loadLlmJobs();
      else if (view === 'share') { this.loadAllArticles(); this._startSharePoll(); }
      else if (view === 'basket') { this.loadBasket(); this.loadBasketSources(); }
      else if (view === 'config') {
        this.configTab = 'sources';
        this.configLoadSources();
        this.configLoadTags();
        this.configLoadFilters();
      }
      else if (view === 'llmSettings') {
        this.llmTab = 'providers';
        this.loadLlmProviders();
        this.loadLlmModels();
        this.loadLlmSettings();
      }
    },

    // ── Config panel (v1.0.2-F) ────────────────────────────────────
    configTab: 'sources',   // 'sources' | 'tags' | 'filters'
    configTags: [],
    configSources: [],
    configFilters: [],
    configFilterSourceFilter: '',
    configModalOpen: false,
    configModalType: null,  // 'tag' | 'source' | 'sourceTags' | 'filter' | 'fetchConfig'
    configModalTarget: null,
    configModalTargetOriginal: null,  // for tag edit: original name (immutable)
    fetchConfigData: { source_id: '', source_name: '', merged: {}, defaults: {}, has_custom: false },

    // v1.0.5.10: name -> {description, keywords} map for hover tooltips
    // on the articles page.  Built from configTags on demand.
    tagTooltipMap: {},

    switchConfigTab(tab) {
      this.configTab = tab;
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
          for (const t of tags) {
            m[t.name] = { description: t.description, keywords: t.keywords || [] };
          }
          this.tagTooltipMap = m;
        })
        .catch(err => { console.error('Failed to load tags:', err); this.configTags = []; });
    },
    configCreateTag() { this.openConfigModal('tag', null); },
    configEditTag(tag) { this.openConfigModal('tag', tag); },
    configDeleteTag(tag) {
      if (!confirm(`确定删除 tag "${tag.name}"? (会级联清除 source_tags 和 article_tags)`)) return;
      fetch(`/api/config/tags/${encodeURIComponent(tag.name)}`, {
    method: 'DELETE', credentials: 'same-origin',
      })
    .then(r => { if (!r.ok) throw new Error('Delete failed'); this.configLoadTags(); })
    .catch(err => alert('删除失败: ' + err.message));
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
      }).catch(err => alert('保存失败: ' + err.message));
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
    configDeleteSource(source) {
      if (!confirm(`确定删除 source "${source.name}"?`)) return;
      fetch(`/api/config/sources/${encodeURIComponent(source.id)}`, {
        method: 'DELETE', credentials: 'same-origin',
      })
        .then(r => { if (!r.ok) throw new Error('Delete failed'); this.configLoadSources(); })
        .catch(err => alert('删除失败: ' + err.message));
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
      }).catch(err => alert('保存失败: ' + err.message));
    },
    configEditSourceTags(source) { this.openConfigModal('sourceTags', source); },
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
      }).catch(err => alert('保存 tags 失败: ' + err.message));
    },

    // ── Fetch Config ─────────────────────────────────────────────
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
        })
        .catch(err => alert('加载抓取配置失败: ' + err.message));
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
      }).catch(err => alert('保存抓取配置失败: ' + err.message));
    },

    // ── Filters CRUD ───────────────────────────────────────────────
    configLoadFilters() {
      const sourceId = this.configFilterSourceFilter || '';
      const url = sourceId
        ? `/api/config/filters?source_id=${encodeURIComponent(sourceId)}`
        : '/api/config/filters';
      fetch(url, { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => { this.configFilters = Array.isArray(data) ? data : []; })
        .catch(err => { console.error('Failed to load filters:', err); this.configFilters = []; });
    },
    configCreateFilter() { this.openConfigModal('filter', null); },
    configEditFilter(filter) { this.openConfigModal('filter', filter); },
    configDeleteFilter(filter) {
      if (!confirm(`确定删除 filter id=${filter.id}?`)) return;
      fetch(`/api/config/filters/${filter.id}`, {
        method: 'DELETE', credentials: 'same-origin',
      })
        .then(r => { if (!r.ok) throw new Error('Delete failed'); this.configLoadFilters(); })
        .catch(err => alert('删除失败: ' + err.message));
    },
    configSaveFilter(body, isEdit) {
      const url = isEdit
        ? `/api/config/filters/${body.id}`
        : '/api/config/filters';
      const method = isEdit ? 'PUT' : 'POST';
      return fetch(url, {
        method, credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then(r => {
        if (!r.ok) return r.text().then(t => { throw new Error(t || `HTTP ${r.status}`); });
        return r.json();
      }).then(() => {
        this.configLoadFilters();
        this.closeConfigModal();
      }).catch(err => alert('保存失败: ' + err.message));
    },

    // ── Modal helpers ───────────────────────────────────────────────
    openConfigModal(type, target) {
      this.configModalType = type;
      // Deep clone to avoid mutating the table row
      this.configModalTarget = target ? JSON.parse(JSON.stringify(target)) : {};
      // Track original identifiers for PUT semantics (tag name, source id)
      if (target && type === 'tag') {
        this.configModalTargetOriginal = target.name;
        // v1.0.5.4: the tag carries an optional ``keywords`` list; the
        // modal shows them as one-per-line text.  Pre-populate the
        // textarea so the user sees (and can edit) what's saved.
        const kws = Array.isArray(target.keywords) ? target.keywords : [];
        this.configModalTarget.keywordsText = kws.join('\n');
      } else if (target && type === 'source' && target.id) {
        this.configModalTargetOriginal = target.id;
      } else if (target && type === 'filter' && target.id) {
        this.configModalTargetOriginal = target.id;
      } else {
        this.configModalTargetOriginal = null;
      }
      if (type === 'source' && !target) {
        this.configModalTarget = {
          id: '', name: '', display_name: '', source_type: 'html', url: '',
          fetch_interval_min: 30, is_active: true, notes: '',
        };
      } else if (type === 'filter' && !target) {
        this.configModalTarget = {
          source_id: this.configSources[0]?.id || '',
          mode: 'blacklist', match_field: 'title+content',
          keywords: [], is_active: true, notes: '',
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
             `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
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

    loadArticles() {
      const params = new URLSearchParams();
      if (this.articlesSearch) params.set('keyword', this.articlesSearch);
      // v1.0.5.10: source/tag filters — empty string means "all"
      if (this.articlesSource) params.set('source', this.articlesSource);
      if (this.articlesTag) params.set('tag', this.articlesTag);
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
      fetch('/api/articles', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => { this.crudArticles = Array.isArray(data) ? data : (data.articles || []); })
        .catch(err => console.error('loadCrudArticles failed:', err));
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
          this.crudArticles = this.crudArticles.filter(a => a.id !== article.id);
          this.articles      = this.articles.filter(a => a.id !== article.id);
          this.allArticles   = this.allArticles.filter(a => a.id !== article.id);
          this.closeModal();
        })
        .catch(err => { alert('删除失败: ' + err.message); this.closeModal(); });
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

      return lines;
    },
    // v1.0.5.24: date-range modal for bestblogs
    fetchDateModal: false,
    fetchTargetSource: '',
    fetchDateRange: 'days',  // 'auto' | 'days' | 'custom'
    fetchDateFrom: '',
    // v1.0.7.6: fetch progress modal + 5s polling state
    fetchProgressModal: false,
    fetchProgress: null,
    _fetchPollTimer: null,
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
        .catch(err => { console.error('loadSources failed:', err); this.fetchResult = '加载失败: ' + err.message; });
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
      this.fetchResult = '正在抓取...';
      this.fetchResultObj = null;
      // v1.0.7.6: async fetch with progress modal + 5s polling
      this.startAsyncFetch(id);
    },

    fetchAllSources() {
      this.fetchingAll = true;
      this.fetchAllResult = null;
      this.fetchResult = '';
      this.fetchResultObj = null;
      fetch('/api/fetch/all/run', { method: 'POST', credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          this.fetchAllResult = data;
          this.fetchingAll = false;
          this.loadSources(); // refresh source list
        })
        .catch(err => {
          this.fetchingAll = false;
          this.showToast('全部抓取失败: ' + err.message);
        });
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
      const srcName = this.sources.find(s => s.id === this.fetchTargetSource)?.name || '';
      this.fetchResult = `正在抓取 ${srcName}...`;
      this.fetchResultObj = null;
      // v1.0.7.6: async fetch with progress modal + 5s polling
      this.startAsyncFetch(this.fetchTargetSource, params);
    },

    // ── v1.0.7.6: async fetch + progress polling ──────────────────────────

    async startAsyncFetch(sourceRef, queryParams = '') {
      // POST /run returns {job_id, source, status:running} immediately.
      // Open modal, then poll /api/fetch/jobs/{job_id} every 5s until
      // terminal status (done | failed | timeout).
      this.fetchProgressModal = true;
      this.fetchProgress = { source: sourceRef, status: 'running', done_feeds: 0, total_feeds: 0, current_feed: '(初始化中)', articles_so_far: 0, elapsed_seconds: 0 };

      try {
        const r = await fetch(`/api/fetch/sources/${sourceRef}/run${queryParams}`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const data = await r.json();
        if (r.status === 409) {
          // Already running — resume polling the existing job
          this.fetchProgress = {
            source: sourceRef,
            status: 'running',
            done_feeds: 0,
            total_feeds: 0,
            current_feed: '已有抓取任务在进行中',
            articles_so_far: 0,
            elapsed_seconds: 0,
            error: data?.detail?.message || '另一个抓取任务正在进行',
          };
          if (data?.detail?.running_job_id) {
            this._pollFetchJob(data.detail.running_job_id);
          }
          return;
        }
        if (!r.ok || !data.job_id) {
          this.fetchProgress.status = 'failed';
          this.fetchProgress.error = data?.detail?.message || data?.error || `HTTP ${r.status}`;
          return;
        }
        this.fetchProgress.source = data.source;
        this.fetchProgress.job_id = data.job_id;
        this._pollFetchJob(data.job_id);
      } catch (err) {
        this.fetchProgress.status = 'failed';
        this.fetchProgress.error = '启动抓取失败: ' + err.message;
      }
    },

    _pollFetchJob(jobId) {
      // Poll every 5s. Stop when status is terminal.
      if (this._fetchPollTimer) {
        clearInterval(this._fetchPollTimer);
      }
      const tick = async () => {
        try {
          const r = await fetch(`/api/fetch/jobs/${jobId}`, { credentials: 'same-origin' });
          if (!r.ok) return; // transient — try again next tick
          const data = await r.json();
          this.fetchProgress = data;
          if (['done', 'failed', 'timeout'].includes(data.status)) {
            if (this._fetchPollTimer) {
              clearInterval(this._fetchPollTimer);
              this._fetchPollTimer = null;
            }
            // When done, refresh the source list so the UI reflects new state
            if (data.status === 'done') {
              this.loadSources();
            }
          }
        } catch (err) {
          // network blip — keep polling
        }
      };
      // First poll immediately, then every 5s
      tick();
      this._fetchPollTimer = setInterval(tick, 5000);
    },

    closeFetchProgress() {
      this.fetchProgressModal = false;
      if (this._fetchPollTimer) {
        clearInterval(this._fetchPollTimer);
        this._fetchPollTimer = null;
      }
    },

    get fetchProgressPercent() {
      const p = this.fetchProgress;
      if (!p || !p.total_feeds) return 0;
      return Math.min(100, Math.round((p.done_feeds / p.total_feeds) * 100));
    },

    // v1.0.5.24: copy article URL to clipboard
    copyArticleUrl() {
      if (!this.articleDetail?.url) return;
      navigator.clipboard.writeText(this.articleDetail.url)
        .then(() => { this.copyToast = true; setTimeout(() => { this.copyToast = false; }, 2000); })
        .catch(() => { alert('复制失败，请手动复制:\n' + this.articleDetail.url); });
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
          this.articleDetail = { ...this.articleDetail, content: data.content_preview };
          alert(`全文导入成功，${data.char_count} 字`);
          this.importFilePath = '';
        })
        .catch(err => { alert('导入失败: ' + (err.message || err.detail || '未知错误')); })
        .finally(() => { this.importLoading = false; });
    },

    pingSource(id) {
      this.fetchResult = '正在测试连通性...';
      fetch(`/api/fetch/sources/${id}/ping`, { method: 'POST', credentials: 'same-origin' })
        .then(r => { this.fetchResult = r.ok ? 'OK - 连通性正常' : 'FAIL - ' + r.status; })
        .catch(err => { this.fetchResult = 'FAIL - ' + err.message; });
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
    allArticlesPageSize: 50,
    allArticlesPageSizeOptions: [20, 50, 100, 200],
    allArticlesHasMore: false,
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
      { value: 'rich_text',           label: '富媒体文本' },  // 🆕
      { value: 'report_png',          label: '报告 PNG' },     // 🆕
      { value: 'pdf',                 label: 'PDF 日报' },     // 🆕 v1.0.5.21
    ],
    // v1.0.5.18: 4 user-options (universal across all 6 formats, per
    // PRD §2).  include_abstract defaults to true (most users want
    // the AI summary); include_fact_check defaults to false (slower;
    // explicit opt-in).  provider/model default to '' (use the
    // default profile's provider / model — no override).
    shareIncludeAbstract: true,
    shareIncludeFactCheck: false,
    shareProvider: '',
    shareModel: '',
    // v1.0.5.18: report_png generation approach (only used when
    // shareFormat === 'report_png').  Maps 1:1 to the 4 PRD §4
    // approaches.  Default 'baoyu-infographic' (most mature).
    sharePngApproach: 'baoyu-infographic',
    // v1.0.5.25: BestBlogs API 增强选项
    shareBBSummarySource: 'moni_ai',  // 'moni_ai' | 'bestblogs' | 'both' | 'none'
    shareBBEnhanceScore: false,
    shareBBEnhanceMainPoints: false,
    shareBBEnhanceKeyQuotes: false,
    shareBBEnhanceTags: false,
    shareInfoGraphicTitle: '同业追踪日报',
    shareQueue: [],
    _sharePollInterval: null,

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
        alert('请先勾选要分享的文章 (在文章卡片上点 checkbox, 至少 1 个)。\n\n这是浏览器本地检查, 不是 HTTP 错误。');
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
          alert('此格式 (' + this.shareFormat + ') 必填 title 字段。\n\n请在上方输入框填写 页面/演示标题。\n\n这是浏览器本地检查, 不是 HTTP 错误。');
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
        alert('内部状态错误: 请重试。');
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
          alert('加入队列失败: ' + err.message);
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
        alert('触发 worker 失败: ' + err.message);
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
      if (this._sharePollInterval) return;
      this.loadShareQueue();
      this._sharePollInterval = setInterval(() => this.loadShareQueue(), 30000);
    },

    // ── Init ────────────────────────────────────────────────
    init() {
      // Apply saved theme (from theme.js mixin)
      this.initTheme();
      // Load service status for topnav indicator
      this.loadServiceStatus();
      // v1.0.5.10: deep-link from current URL — visiting /crud opens
      // the CRUD tab directly.  Falls back to /articles for unknown paths.
      const view = this._pathToView[window.location.pathname] || 'articles';
      this.currentView = view;
      // Replace the history entry so the URL the user landed on is the
      // current one (prevents an extra back-button step).
      const path = this._viewToPath[view] || '/articles';
      try {
        window.history.replaceState({ view }, '', path);
      } catch (e) { /* ignore — older browsers */ }
      // v1.0.5.10: refresh source/tag filter option lists at boot
      // (cheap; safe to call even when we land on /crud — keeps the
      // article-list filters warm for when the user navigates back).
      this.loadArticlesFilters();
      // v1.0.5.10: also load the data for the current view, otherwise
      // /crud /cron /fetch /llmJobs /share /config land on an empty panel.
      // navigate() already calls the right loader; we can reuse it.
      // But we already set currentView above, so navigate() will just
      // re-load.  The cheapest path: call only the loader for the boot view.
      if (view === 'articles') this._loadArticlesWithPaging();
      else if (view === 'crud') this.loadCrudArticles();
      else if (view === 'cron') this.loadCronRuns();
      else if (view === 'fetch') this.loadSources();
      else if (view === 'llmJobs') this.loadLlmJobs();
      else if (view === 'share') { this.loadAllArticles(); this._startSharePoll(); }
      else if (view === 'config') {
        this.configTab = 'sources';
        this.configLoadSources();
        this.configLoadTags();
        this.configLoadFilters();
      }
      // v1.0.5.10: listen for browser back/forward so the SPA follows.
      window.addEventListener('popstate', (e) => this.onPopState(e));
    },
    
    // ── Article detail modal (v1.0.4 — BUG #7) ─────────────────
    articleDetail: null,
    summaryLoading: false,
    factCheckLoading: false,

    showArticleDetail(article) {
      // v1.0.4: sync fetch (Alpine method 中 async/await 兼容性不可靠)
      const self = this;
      fetch(`/api/articles/${article.id}`, { credentials: 'same-origin' })
        .then(r => {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(data => { self.articleDetail = data; })
        .catch(err => { alert('加载文章失败: ' + err.message); });
    },

    closeArticleDetail() {
      this.articleDetail = null;
      this.summaryLoading = false;
      this.factCheckLoading = false;
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
            summary: '⏳ 摘要生成中... (job ' + data.job_id.slice(0, 8) + ')',
          };
        })
        .catch(err => { alert('生成摘要失败: ' + err.message); })
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
            fact_check_conclusion: '⏳ 核查中... (job ' + data.job_id.slice(0, 8) + ')',
          };
        })
        .catch(err => { alert('事实核查失败: ' + err.message); })
        .finally(() => { self.factCheckLoading = false; });
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

    cancelLlmJob(jobId) {
      if (!confirm('取消这个 LLM 任务? 已生成的内容会保留, 但状态会标记为 cancelled.')) return;
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
        .catch(err => { alert('取消失败: ' + err.message); });
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

    openJobDetail(job) {
      this.jobDetail = job;
      this.jobDetailCopied = false;
    },

    closeJobDetail() {
      this.jobDetail = null;
      this.jobDetailCopied = false;
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
        alert('复制失败: ' + (err && err.message ? err.message : String(err)));
      }
    },

    // ── LLM Settings panel ───────────────────────────────────────
    llmTab: 'providers',
    llmProviders: [],
    llmModels: [],
    llmSettings: {},
    llmSettingsDraft: { active_summary_model_id: '', active_fact_check_model_id: '' },

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
      if (tab === 'usage' && !this.llmUsage) {
        this.loadLlmUsage();
      }
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
        .catch(err => alert('操作失败: ' + err.message));
    },

    deleteLlmProvider(provider) {
      if (!confirm(`确定删除 Provider "${provider.name}"?`)) return;
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
        .catch(err => alert('删除失败: ' + err.message));
    },

    testLlmProvider(provider) {
      // Single confirmation — warns about token usage
      if (!confirm(`测试 "${provider.name}" (${provider.hermes_provider}) 连通性？\n\n⚠️ 将消耗约 5-10 tokens`)) return;

      provider._testing = true;

      fetch(`/api/llm/providers/${provider.id}/test`, {
        method: 'POST',
        credentials: 'same-origin',
      })
        .then(r => r.json())
        .then(data => {
          provider._testing = false;
          if (data.ok) {
            alert(`✅ 连通成功\n\nProvider: ${data.provider}\nModel: ${data.model}\n耗时: ${data.duration_ms}ms\n回复: ${data.reply_preview}`);
          } else {
            alert(`❌ 连通失败\n\nProvider: ${data.provider}\nModel: ${data.model}\n错误: ${data.error}`);
          }
        })
        .catch(err => {
          provider._testing = false;
          alert('测试失败: ' + err.message);
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
        .catch(err => alert('操作失败: ' + err.message));
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
        .catch(err => alert('保存设置失败: ' + err.message));
    },
  };
}
