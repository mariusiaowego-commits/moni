// Root Alpine component — single source of truth for all state and methods
function app() {
  return {
    // ── Navigation ────────────────────────────────────────────────
    currentView: 'articles',

    navigate(view) {
      this.currentView = view;
      if (view === 'articles') this.loadArticles();
      else if (view === 'crud') this.loadCrudArticles();
      else if (view === 'cron') this.loadCronJobs();
      else if (view === 'fetch') this.loadSources();
      else if (view === 'share') { this.loadAllArticles(); this._startSharePoll(); }
      else if (view === 'config') {
        this.configTab = 'sources';
        configLoadSources();
        configLoadTags();
        configLoadFilters();
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

    switchConfigTab(tab) {
      this.configTab = tab;
    },

    filterFiltersBySource() {
      configLoadFilters();
    },

    // ── Shared helpers ────────────────────────────────────────────
    formatTime(ts) {
      if (!ts) return '-';
      const d = new Date(ts * 1000);
      if (isNaN(d)) return '-';
      const pad = n => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ` +
             `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    },

    isNew(fetchedAt) {
      if (!fetchedAt) return false;
      return (Math.floor(Date.now() / 1000) - fetchedAt) < 86400;
    },

    // ── Articles panel ─────────────────────────────────────────────
    articles: [],
    articlesSearch: '',

    loadArticles() {
      const params = new URLSearchParams();
      if (this.articlesSearch) params.set('keyword', this.articlesSearch);
      const query = params.toString();
      fetch('/api/articles' + (query ? '?' + query : ''), { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => { this.articles = Array.isArray(data) ? data : (data.articles || []); })
        .catch(err => console.error('loadArticles failed:', err));
    },

    // ── CRUD panel ────────────────────────────────────────────────
    crudArticles: [],
    showModal: false,
    deleteTarget: null,

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

    // ── Cron panel ────────────────────────────────────────────────
    cronJobs: [],

    loadCronJobs() {
      fetch('/api/cron/jobs', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => { this.cronJobs = Array.isArray(data) ? data : (data.jobs || []); })
        .catch(err => console.error('loadCronJobs failed:', err));
    },

    // ── Fetch panel ───────────────────────────────────────────────
    sources: [],
    fetchResult: '',

    loadSources() {
      fetch('/api/fetch/sources', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => { this.sources = Array.isArray(data) ? data : (data.sources || []); })
        .catch(err => console.error('loadSources failed:', err));
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
      fetch('/api/share/queue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ article_ids: this.selectedArticleIds, format: this.shareFormat }),
      })
        .then(r => r.json())
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

    // ── Init ─────────────────────────────────────────────────────
    init() {
      this.loadArticles();
    },
  };
}
