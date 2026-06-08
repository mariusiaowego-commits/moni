// Root Alpine component — single source of truth for all state and methods
function app() {
  return {
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
      config:   '/config',
    },
    _pathToView: {
      '/articles': 'articles',
      '/crud':     'crud',
      '/cron':     'cron',
      '/fetch':    'fetch',
      '/llmJobs':  'llmJobs',
      '/share':    'share',
      '/config':   'config',
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
        this.loadArticles();
        this.loadArticlesFilters();  // v1.0.5.10: refresh source/tag <select> option lists
      }
      else if (view === 'crud') this.loadCrudArticles();
      else if (view === 'cron') this.loadCronRuns();
      else if (view === 'fetch') this.loadSources();
      else if (view === 'llmJobs') this.loadLlmJobs();  // v1.0.5.1
      else if (view === 'share') { this.loadAllArticles(); this._startSharePoll(); }
      else if (view === 'config') {
        this.configTab = 'sources';
        this.configLoadSources();
        this.configLoadTags();
        this.configLoadFilters();
      }
    },

    // v1.0.5.10: handle browser back/forward.  We only update state, not the
    // URL (the URL already changed via the browser's own history machinery).
    onPopState(event) {
      const view = (event.state && event.state.view) || this._pathToView[window.location.pathname] || 'articles';
      this.currentView = view;
      // Re-run the loaders for the target view.
      if (view === 'articles') {
        this.loadArticles();
        this.loadArticlesFilters();
      }
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
    },

    // ── Config panel (v1.0.2-F) ────────────────────────────────────
    configTab: 'sources',   // 'sources' | 'tags' | 'filters'
    configTags: [],
    configSources: [],
    configFilters: [],
    configFilterSourceFilter: '',
    configModalOpen: false,
    configModalType: null,  // 'tag' | 'source' | 'sourceTags' | 'filter'
    configModalTarget: null,
    configModalTargetOriginal: null,  // for tag edit: original name (immutable)

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
    formatTagTooltip(tagName) {
      const info = this.tagTooltipMap[tagName];
      if (!info) {
        return tagName + ' — 尚未加载 tag 描述';
      }
      const desc = info.description || '(无描述)';
      const kws = Array.isArray(info.keywords) && info.keywords.length > 0
        ? info.keywords.join(', ')
        : '(无关键词)';
      return `${tagName}\n${desc}\n关键词: ${kws}\n(OR 匹配: 任一关键词命中即通过)`;
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
      const query = params.toString();
      fetch('/api/articles' + (query ? '?' + query : ''), { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => { this.articles = Array.isArray(data) ? data : (data.articles || []); })
        .catch(err => console.error('loadArticles failed:', err));
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
      fetch(`/api/articles/${article.id}`, { method: 'DELETE', credentials: 'same-origin' })
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
      this.fetchResult = '正在抓取...';
      fetch(`/api/fetch/sources/${id}/run`, { method: 'POST', credentials: 'same-origin' })
        .then(r => r.json().catch(() => ({ result: 'success' })))
        .then(data => { this.fetchResult = JSON.stringify(data, null, 2); })
        .catch(err => { this.fetchResult = '抓取失败: ' + err.message; });
    },

    pingSource(id) {
      this.fetchResult = '正在测试连通性...';
      fetch(`/api/fetch/sources/${id}/ping`, { method: 'POST', credentials: 'same-origin' })
        .then(r => { this.fetchResult = r.ok ? 'OK - 连通性正常' : 'FAIL - ' + r.status; })
        .catch(err => { this.fetchResult = 'FAIL - ' + err.message; });
    },

    // ── Share panel ───────────────────────────────────────────────
    allArticles: [],
    selectedArticleIds: [],
    shareFormat: 'markdown',
    shareFormats: [
      // v1.0 11 format manifest (PRD §4.5 / moni/share/formats.py)
      { value: 'markdown',  label: 'Markdown' },
      { value: 'plain',     label: '纯文本' },
      { value: 'html',      label: 'HTML' },
      { value: 'pdf',       label: 'PDF 单文件' },
      { value: 'pdf_bound', label: 'PDF 合订' },
      { value: 'audio',     label: '音频 (TTS)' },
      { value: 'video',     label: '视频' },
      { value: 'image',     label: '单图摘要' },
      { value: 'infographic', label: '信息图' },
      { value: 'wechat',    label: '微信公众号' },
      { value: 'weibo',     label: '微博' },
    ],
    shareQueue: [],
    _sharePollInterval: null,

    loadAllArticles() {
      fetch('/api/articles', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => { this.allArticles = Array.isArray(data) ? data : (data.articles || []); })
        .catch(err => console.error('loadAllArticles failed:', err));
    },

    toggleAllArticles(event) {
      this.selectedArticleIds = event.target.checked
        ? this.allArticles.map(a => a.id)
        : [];
    },

    addToQueue() {
      if (!this.selectedArticleIds.length) { alert('请先选择文章'); return; }
      // v1.0 manifest pipeline: POST /api/share 接受单 article_id 一次
      // 当前是批量: 第一篇文章先建 task, 其余 article_ids 后续走 manifest 扩展
      // 这里走 v1.0 batch endpoint /api/share/queue
      fetch('/api/share/queue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ article_ids: this.selectedArticleIds, format: this.shareFormat }),
      })
        .then(r => {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(() => { this.loadShareQueue(); this.selectedArticleIds = []; })
        .catch(err => { alert('加入队列失败: ' + err.message); });
    },

    loadShareQueue() {
      fetch('/api/share/queue', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => { this.shareQueue = Array.isArray(data) ? data : (data.queue || []); })
        .catch(err => console.error('loadShareQueue failed:', err));
    },

    _startSharePoll() {
      if (this._sharePollInterval) return;
      this.loadShareQueue();
      this._sharePollInterval = setInterval(() => this.loadShareQueue(), 30000);
    },

    // ── Init ────────────────────────────────────────────────
    init() {
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
      if (view === 'articles') this.loadArticles();
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
  };
}
