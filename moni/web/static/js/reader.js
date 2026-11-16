/* Sprint 47: article reader — filters, bulk delete, split reader, AI panel.
 *
 * Loaded after app.js and merged into the root Alpine component via
 * Object.assign in app().  Markdown is rendered with vendored marked and
 * sanitized with vendored DOMPurify; raw article HTML never reaches
 * x-html without the allowlist.
 */
(function () {
  'use strict';

  const ALLOWED_TAGS = [
    'a', 'p', 'br', 'strong', 'em', 'b', 'i', 'code', 'pre', 'ul', 'ol', 'li',
    'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'thead', 'tbody',
    'tr', 'th', 'td', 'hr', 'span', 'div', 'del', 'sup', 'sub',
  ];
  const ALLOWED_ATTR = [
    'href', 'title', 'class', 'colspan', 'rowspan', 'target',
    'rel', 'width', 'height',
  ];
  const FRONTMATTER_RE = /^---\s*\n[\s\S]*?\n---\s*\n?/;
  const BANK_RE = /\[bank:([^\]]+)\]/gi;
  // Scraped bodies carry third-party images; the reader shows text only so a
  // hotlinked or misattributed picture can never be republished from moni.
  const MD_IMAGE_RE = /!\[[^\]]*\]\([^)]*\)/g;
  const HTML_IMAGE_RE = /<img\b[^>]*>/gi;

  function md() {
    if (!window.marked) return null;
    if (!window.__moniMarkedReady) {
      window.marked.setOptions({ gfm: true, breaks: true, headerIds: false, mangle: false });
      window.__moniMarkedReady = true;
    }
    return window.marked;
  }

  function sanitize(html) {
    if (!window.DOMPurify) return '';
    const clean = window.DOMPurify.sanitize(html, {
      ALLOWED_TAGS,
      ALLOWED_ATTR,
      FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'form', 'input', 'button'],
      FORBID_ATTR: ['onerror', 'onload', 'onclick', 'style'],
    });
    const box = document.createElement('div');
    box.innerHTML = clean;
    box.querySelectorAll('a').forEach((a) => {
      a.setAttribute('target', '_blank');
      a.setAttribute('rel', 'noopener noreferrer');
    });
    box.querySelectorAll('table').forEach((table) => {
      const wrap = document.createElement('div');
      wrap.className = 'table-scroll';
      table.parentNode.insertBefore(wrap, table);
      wrap.appendChild(table);
    });
    return box.innerHTML;
  }

  function renderMarkdown(text) {
    const parser = md();
    if (!parser) return '';
    return sanitize(parser.parse(String(text || '')));
  }

  function renderArticle(text) {
    let value = String(text || '');
    value = value.replace(FRONTMATTER_RE, '');
    value = value.replace(BANK_RE, '\n\n<span class="bank-marker">bank:$1</span>\n\n');
    value = value.replace(MD_IMAGE_RE, '').replace(HTML_IMAGE_RE, '');
    if (!md()) {
      const escaped = value
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      return escaped.split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
    }
    return renderMarkdown(value);
  }

  window.moniReader = {
    // ── filter state ─────────────────────────────────────────────
    articleFacets: { tags: [], sources: [] },
    articleTagsSelected: [],
    articleSourcesSelected: [],
    articleTagMode: 'or',
    articleSourceOpen: false,
    articleSourceQuery: '',
    articlesSort: 'fetched_at_desc',
    articlesTotal: 0,
    articleSelectedIds: [],
    articleBulkDeleteOpen: false,
    articleBulkDeletePreview: '',
    _articleFacetsLoaded: false,

    get articleFilterCount() {
      return this.articleTagsSelected.length + this.articleSourcesSelected.length
        + (this.articlesSearch ? 1 : 0);
    },

    get filteredArticleSources() {
      const q = (this.articleSourceQuery || '').trim().toLowerCase();
      const list = this.articleFacets.sources || [];
      if (!q) return list;
      return list.filter((s) =>
        String(s.name || '').toLowerCase().includes(q)
        || String(s.display_name || '').toLowerCase().includes(q));
    },

    get filteredReaderPrompts() {
      const q = (this.readerPromptQuery || '').trim().toLowerCase();
      if (!q) return this.readerPrompts;
      return this.readerPrompts.filter((p) =>
        String(p.name || '').toLowerCase().includes(q)
        || String(p.body || '').toLowerCase().includes(q));
    },

    get readerContextLabel() {
      const n = (this.articleDetail?.content || '').length;
      if (this.readerQuote) return '选区';
      if (n > 20000) return '长文分段';
      return '全文';
    },

    get readerContextTitle() {
      const n = (this.articleDetail?.content || '').length;
      if (this.readerQuote) return '只把选中文本和它所在的段落交给模型';
      if (n > 20000) return '超过 2 万字：分段读完全文，再合并成一次回答';
      return '一次调用带上全文';
    },

    sourceLabel(name) {
      const hit = (this.articleFacets.sources || []).find((s) => s.name === name);
      return hit ? (hit.display_name || hit.name) : name;
    },

    loadArticleFacets() {
      fetch('/api/articles/facets', { credentials: 'same-origin' })
        .then((r) => r.json())
        .then((data) => {
          this.articleFacets = {
            tags: Array.isArray(data.tags) ? data.tags : [],
            sources: Array.isArray(data.sources) ? data.sources : [],
          };
          this.articlesSources = this.articleFacets.sources;
          this.articlesTags = this.articleFacets.tags;
        })
        .catch((err) => {
          this._articleFacetsLoaded = false;
          console.error('loadArticleFacets failed:', err);
        });
    },

    toggleArticleTag(name) {
      const set = new Set(this.articleTagsSelected);
      if (set.has(name)) set.delete(name);
      else set.add(name);
      this.articleTagsSelected = Array.from(set);
      this.onArticlesFilterChange();
    },

    clearArticleTags() {
      this.articleTagsSelected = [];
      this.onArticlesFilterChange();
    },

    setArticleTagMode(mode) {
      this.articleTagMode = mode;
      this.onArticlesFilterChange();
    },

    toggleArticleSource(name, checked) {
      const set = new Set(this.articleSourcesSelected);
      if (checked) set.add(name);
      else set.delete(name);
      this.articleSourcesSelected = Array.from(set);
      this.onArticlesFilterChange();
    },

    selectAllArticleSources() {
      this.articleSourcesSelected = this.articleFacets.sources.map((s) => s.name);
      this.onArticlesFilterChange();
    },

    clearArticleSources() {
      this.articleSourcesSelected = [];
      this.onArticlesFilterChange();
    },

    clearArticleFilters() {
      this.articlesSearch = '';
      this.articleTagsSelected = [];
      this.articleSourcesSelected = [];
      this.articleTagMode = 'or';
      this.onArticlesFilterChange();
    },

    _articleQuery(extra) {
      const params = new URLSearchParams();
      if (this.articlesSearch) params.set('keyword', this.articlesSearch);
      if (this.articleTagsSelected.length) {
        params.set('tags', this.articleTagsSelected.join(','));
        params.set('tag_mode', this.articleTagMode);
      }
      if (this.articleSourcesSelected.length) {
        params.set('sources', this.articleSourcesSelected.join(','));
      }
      params.set('sort', this.articlesSort || 'fetched_at_desc');
      if (extra) Object.entries(extra).forEach(([k, v]) => params.set(k, v));
      return params.toString();
    },

    ensureArticleFacets() {
      if (this._articleFacetsLoaded) return;
      this._articleFacetsLoaded = true;
      this.loadArticleFacets();
      this.readerLoadPrompts();
      this.readerStartUnreadPoll();
    },

    loadArticles() {
      this.ensureArticleFacets();
      const query = this._articleQuery({
        limit: this.articlesPageSize,
        offset: (this.articlesPage - 1) * this.articlesPageSize,
      });
      fetch('/api/articles?' + query, { credentials: 'same-origin' })
        .then((r) => {
          const total = parseInt(r.headers.get('X-Total-Count') || '0', 10);
          if (!Number.isNaN(total)) this.articlesTotal = total;
          return r.json();
        })
        .then((data) => {
          this.articles = Array.isArray(data) ? data : (data.articles || []);
          this.articlesHasMore = this.articlesTotal
            ? (this.articlesPage * this.articlesPageSize < this.articlesTotal)
            : this.articles.length >= this.articlesPageSize;
        })
        .catch((err) => console.error('loadArticles failed:', err));
    },

    _loadArticlesWithPaging() {
      // The articles view calls this on first paint, not loadArticles(),
      // so the chip facets have to be kicked off from here too.
      this.ensureArticleFacets();
      const probe = parseInt(this.articlesPageSize, 10) || 50;
      const query = this._articleQuery({
        limit: probe + 1,
        offset: (this.articlesPage - 1) * probe,
      });
      fetch('/api/articles?' + query, { credentials: 'same-origin' })
        .then((r) => {
          const total = parseInt(r.headers.get('X-Total-Count') || '0', 10);
          if (!Number.isNaN(total)) this.articlesTotal = total;
          return r.json();
        })
        .then((data) => {
          const list = Array.isArray(data) ? data : (data.articles || []);
          this.articlesHasMore = this.articlesTotal
            ? ((this.articlesPage - 1) * probe + Math.min(list.length, probe) < this.articlesTotal)
            : list.length > probe;
          this.articles = list.slice(0, probe);
        })
        .catch((err) => console.error('_loadArticlesWithPaging failed:', err));
    },

    onArticlesFilterChange() {
      this.articlesPage = 1;
      this.articleSelectedIds = [];
      this._loadArticlesWithPaging();
    },

    // ── bulk selection / delete ──────────────────────────────────
    articleAllSelectedOnPage() {
      const list = this.articles || [];
      return list.length > 0 && list.every((a) => this.articleSelectedIds.includes(a.id));
    },

    articleToggleAllOnPage(ev) {
      const ids = (this.articles || []).map((a) => a.id);
      if (ev.target.checked) {
        this.articleSelectedIds = Array.from(new Set([...this.articleSelectedIds, ...ids]));
      } else {
        this.articleSelectedIds = this.articleSelectedIds.filter((id) => !ids.includes(id));
      }
    },

    articleSelectAllOnPage() {
      this.articleSelectedIds = (this.articles || []).map((a) => a.id);
    },

    articleClearSelection() {
      this.articleSelectedIds = [];
    },

    openArticleBulkDelete() {
      if (!this.articleSelectedIds.length) return;
      const selected = (this.articles || []).filter((a) => this.articleSelectedIds.includes(a.id));
      const names = selected.slice(0, 3).map((a) => a.title).join('、');
      this.articleBulkDeletePreview = names + (selected.length > 3 ? ` 等 ${selected.length} 篇` : '');
      this.articleBulkDeleteOpen = true;
    },

    confirmArticleBulkDelete() {
      const ids = [...this.articleSelectedIds];
      if (!ids.length) return;
      fetch('/api/articles/bulk-delete', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      })
        .then((r) => r.json().then((d) => (r.ok ? d : Promise.reject(d))))
        .then((data) => {
          const deleted = Number(data.deleted || 0);
          this.articles = (this.articles || []).filter((a) => !ids.includes(a.id));
          this.articlesTotal = Math.max(0, this.articlesTotal - deleted);
          this.articleSelectedIds = [];
          this.articleBulkDeleteOpen = false;
          this._notify('success', `已删除 ${deleted} 篇`);
          if (!this.articles.length && this.articlesPage > 1) this.articlesPage -= 1;
          this._loadArticlesWithPaging();
        })
        .catch((err) => this._notify('error', '批量删除失败: ' + (err.message || JSON.stringify(err))));
    },

    // ── reader detail ───────────────────────────────────────────
    readerExpanded: false,
    readerMobileTab: 'read',
    readerMessages: [],
    readerConversation: null,
    readerPrompts: [],
    readerPromptQuery: '',
    readerPromptManagerOpen: false,
    readerPromptForm: { id: null, name: '', body: '' },
    readerInput: '',
    readerQuote: '',
    readerReasoning: 'low',
    readerStreaming: false,
    readerStreamJobId: null,
    readerStreamMsgId: null,
    readerStreamSource: null,
    readerRendered: '',
    readerProgress: 0,
    readerSelectionVisible: false,
    readerSelectionPos: '',
    readerUnreadCount: 0,
    _readerSelectionText: '',
    _readerUnreadTimer: null,

    showArticleDetail(article) {
      if (!article || !article.id) return;
      this.readerCloseStream();
      this.readerMessages = [];
      this.readerConversation = null;
      this.readerQuote = '';
      this.readerInput = '';
      this.readerMobileTab = 'read';
      fetch(`/api/articles/${article.id}`, { credentials: 'same-origin' })
        .then((r) => {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then((data) => {
          this.articleDetail = data;
          this.readerRendered = renderArticle(data.content || '');
          this.readerLoadPrompts();
          this.readerLoadConversation();
        })
        .catch((err) => this._notify('error', '加载文章失败: ' + err.message));
    },

    closeArticleDetail() {
      this.readerCloseStream();
      this.articleDetail = null;
      this.readerSelectionVisible = false;
      this.summaryLoading = false;
      this.factCheckLoading = false;
    },

    readerLoadPrompts() {
      fetch('/api/reader/prompts', { credentials: 'same-origin' })
        .then((r) => r.json())
        .then((data) => { this.readerPrompts = Array.isArray(data) ? data : []; })
        .catch((err) => console.error('readerLoadPrompts failed:', err));
    },

    readerLoadConversation() {
      if (!this.articleDetail?.id) return;
      fetch(`/api/reader/conversations?article_id=${encodeURIComponent(this.articleDetail.id)}`, { credentials: 'same-origin' })
        .then((r) => r.json())
        .then((data) => {
          this.readerConversation = data.conversation || null;
          this.readerMessages = Array.isArray(data.messages) ? data.messages : [];
          if (this.readerConversation) {
            this.readerMarkRead();
            this.readerResumeStream();
            this.readerScrollToBottom();
          }
        })
        .catch((err) => console.error('readerLoadConversation failed:', err));
    },

    readerResumeStream() {
      const active = (this.readerMessages || []).find(
        (m) => m.role === 'assistant' && m.job_id && (m.status === 'pending' || m.status === 'streaming'));
      if (active) this.readerStartStream(active.job_id, active.id);
    },

    async readerEnsureConversation() {
      if (this.readerConversation) return this.readerConversation;
      const res = await fetch('/api/reader/conversations', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ article_id: this.articleDetail.id }),
      });
      if (!res.ok) throw new Error('创建会话失败');
      const data = await res.json();
      this.readerConversation = data.conversation;
      this.readerMessages = Array.isArray(data.messages) ? data.messages : [];
      return this.readerConversation;
    },

    readerEnter(ev) {
      if (ev.shiftKey) return;
      ev.preventDefault();
      this.readerSend();
    },

    readerSend() {
      const text = (this.readerInput || '').trim();
      if (!text) return;
      this.readerSubmit({ text });
    },

    readerRunPrompt(prompt) {
      if (!prompt || this.readerStreaming) return;
      this.readerSubmit({ promptId: prompt.id, displayText: prompt.body });
    },

    async readerSubmit({ text = '', promptId = null, displayText = '' }) {
      if (this.readerStreaming || !this.articleDetail?.id) return;
      this.readerStreaming = true;
      try {
        await this.readerEnsureConversation();
        const payload = {
          text,
          quote_text: this.readerQuote || null,
          reasoning: this.readerReasoning,
        };
        if (promptId) payload.prompt_id = promptId;
        const res = await fetch(`/api/reader/conversations/${this.readerConversation.id}/messages`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.message || data.detail || '发送失败');
        const prompt = promptId ? this.readerPrompts.find((p) => p.id === promptId) : null;
        this.readerMessages.push({
          id: data.user_message_id,
          role: 'user',
          content: text || displayText || prompt?.body || '',
          quote_text: this.readerQuote || null,
          status: 'done',
        });
        this.readerMessages.push({
          id: data.assistant_message_id,
          role: 'assistant',
          content: '',
          job_id: data.job_id,
          status: 'pending',
          reasoning: this.readerReasoning,
        });
        this.readerInput = '';
        this.readerQuote = '';
        this.readerScrollToBottom();
        this.readerStartStream(data.job_id, data.assistant_message_id);
      } catch (err) {
        this.readerStreaming = false;
        this._notify('error', 'AI 请求失败: ' + (err.message || err));
      }
    },

    readerStartStream(jobId, messageId) {
      this.readerCloseStream();
      this.readerStreaming = true;
      this.readerStreamJobId = jobId;
      this.readerStreamMsgId = messageId;
      const source = new EventSource(`/api/reader/jobs/${jobId}/stream`);
      this.readerStreamSource = source;
      const message = () => this.readerMessages.find((m) => m.id === messageId);

      source.addEventListener('snapshot', (ev) => {
        try {
          const data = JSON.parse(ev.data);
          const msg = message();
          if (msg) {
            msg.content = data.content || '';
            msg.status = data.status || 'streaming';
          }
          this.readerScrollToBottom();
        } catch (e) { /* ignore */ }
      });
      source.addEventListener('delta', (ev) => {
        try {
          const data = JSON.parse(ev.data);
          const msg = message();
          if (msg) msg.content += data.text || '';
          this.readerScrollToBottom();
        } catch (e) { /* ignore */ }
      });
      source.addEventListener('done', (ev) => {
        try {
          const data = JSON.parse(ev.data);
          const msg = message();
          if (msg) msg.status = 'done';
          this.readerStreaming = false;
          this.readerStreamJobId = null;
          this.readerStreamMsgId = null;
          this.readerCloseStream();
          this.readerMarkRead();
          this.readerScrollToBottom();
          if (data.input_tokens || data.output_tokens) {
            this._notify('info', `AI 完成 · ${data.input_tokens || 0} in / ${data.output_tokens || 0} out`);
          }
        } catch (e) { /* ignore */ }
      });
      source.addEventListener('error', (ev) => {
        // Custom SSE error carries data; network errors have no data and
        // EventSource will retry automatically.
        if (!ev.data) return;
        try {
          const data = JSON.parse(ev.data);
          const msg = message();
          if (msg) {
            msg.status = data.status || 'failed';
            msg.error = data.message || '生成失败';
          }
          this.readerStreaming = false;
          this.readerStreamMsgId = null;
          this.readerCloseStream();
        } catch (e) { /* ignore */ }
      });
    },

    readerCloseStream() {
      if (this.readerStreamSource) {
        try { this.readerStreamSource.close(); } catch (e) { /* ignore */ }
      }
      this.readerStreamSource = null;
      this.readerStreamMsgId = null;
    },

    readerStop() {
      if (!this.readerStreamJobId) return;
      fetch(`/api/reader/jobs/${this.readerStreamJobId}/cancel`, {
        method: 'POST',
        credentials: 'same-origin',
      }).catch(() => {});
      const active = (this.readerMessages || []).find(
        (m) => m.job_id === this.readerStreamJobId && m.role === 'assistant');
      if (active) active.status = 'cancelled';
      this.readerStreaming = false;
      this.readerStreamMsgId = null;
      this.readerCloseStream();
    },

    readerMarkRead() {
      if (!this.readerConversation?.id) return;
      fetch(`/api/reader/conversations/${this.readerConversation.id}/read`, {
        method: 'POST',
        credentials: 'same-origin',
      }).catch(() => {});
    },

    readerCopyMessage(text) {
      const value = String(text || '');
      if (!value) return;
      navigator.clipboard.writeText(value)
        .then(() => this._notify('info', '已复制回答'))
        .catch(() => this._notify('error', '复制失败'));
    },

    readerScrollToBottom() {
      setTimeout(() => {
        const box = this.$refs?.aiMessages || document.querySelector('.ai-messages');
        if (box) box.scrollTop = box.scrollHeight;
      }, 40);
    },

    readerUpdateProgress(ev) {
      const el = ev.target;
      const max = el.scrollHeight - el.clientHeight;
      this.readerProgress = max > 0 ? Math.min(100, Math.max(0, (el.scrollTop / max) * 100)) : 0;
    },

    readerHandleSelection() {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount) {
        this.readerSelectionVisible = false;
        return;
      }
      const text = sel.toString().trim();
      const body = this.$refs?.readerBody || document.querySelector('.reader-body');
      const range = sel.getRangeAt(0);
      if (!text || !body || !body.contains(range.commonAncestorContainer)) {
        this.readerSelectionVisible = false;
        return;
      }
      const rect = range.getBoundingClientRect();
      this.readerSelectionPos = `left:${Math.min(window.innerWidth - 130, Math.max(12, rect.left))}px;top:${Math.max(12, rect.top - 44)}px;`;
      this._readerSelectionText = text.slice(0, 2000);
      this.readerSelectionVisible = true;
    },

    readerQuoteSelection() {
      const text = (this._readerSelectionText || '').trim();
      if (!text) return;
      this.readerQuote = text.slice(0, 2000);
      this.readerSelectionVisible = false;
      this.readerMobileTab = 'ai';
      setTimeout(() => {
        const input = document.querySelector('.ai-input');
        if (input) input.focus();
      }, 30);
    },

    readerArticleHtml() {
      return this.readerRendered || '';
    },

    readerRenderMarkdown(text) {
      return renderMarkdown(text);
    },

    readerRenderArticle(text) {
      return renderArticle(text);
    },

    // ── prompt manager ──────────────────────────────────────────
    readerOpenPromptManager() {
      this.readerPromptManagerOpen = true;
      this.readerPromptQuery = '';
      this.readerResetPromptForm();
      this.readerLoadPrompts();
    },

    readerResetPromptForm() {
      this.readerPromptForm = { id: null, name: '', body: '' };
    },

    readerEditPrompt(prompt) {
      this.readerPromptForm = { id: prompt.id, name: prompt.name, body: prompt.body };
    },

    readerUsePrompt(prompt) {
      this.readerPromptManagerOpen = false;
      this.readerRunPrompt(prompt);
    },

    readerSavePrompt() {
      const form = this.readerPromptForm;
      const name = (form.name || '').trim();
      const body = (form.body || '').trim();
      if (!name || !body) {
        this._notify('error', '名称和提示词都不能为空');
        return;
      }
      const isEdit = !!form.id;
      const url = isEdit ? `/api/reader/prompts/${form.id}` : '/api/reader/prompts';
      fetch(url, {
        method: isEdit ? 'PATCH' : 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, body }),
      })
        .then((r) => r.json().then((d) => (r.ok ? d : Promise.reject(d))))
        .then(() => {
          this.readerResetPromptForm();
          this.readerLoadPrompts();
          this._notify('success', isEdit ? '提示词已保存' : '提示词已添加');
        })
        .catch((err) => this._notify('error', '保存失败: ' + (err.message || JSON.stringify(err))));
    },

    readerDeletePrompt(prompt) {
      if (!prompt || prompt.is_builtin) return;
      if (!window.confirm(`删除提示词「${prompt.name}」？`)) return;
      fetch(`/api/reader/prompts/${prompt.id}`, {
        method: 'DELETE',
        credentials: 'same-origin',
      })
        .then((r) => r.json().then((d) => (r.ok ? d : Promise.reject(d))))
        .then(() => {
          this.readerLoadPrompts();
          this._notify('success', '提示词已删除');
        })
        .catch((err) => this._notify('error', '删除失败: ' + (err.message || JSON.stringify(err))));
    },

    readerRestorePrompts() {
      fetch('/api/reader/prompts/restore', {
        method: 'POST',
        credentials: 'same-origin',
      })
        .then((r) => r.json())
        .then(() => {
          this.readerLoadPrompts();
          this._notify('success', '默认提示词已恢复');
        })
        .catch(() => this._notify('error', '恢复默认失败'));
    },

    // ── unread polling ──────────────────────────────────────────
    llmJobTypeLabel(job) {
      if (!job) return '';
      if (job.task_type === 'reader_chat') {
        return job.prompt_name ? ('阅读 · ' + job.prompt_name) : '阅读对话';
      }
      return job.task_type === 'summary' ? '摘要' : '事实核查';
    },

    readerStartUnreadPoll() {
      if (this._readerUnreadTimer) return;
      this._readerUnreadTimer = setInterval(() => {
        if (document.visibilityState !== 'visible') return;
        fetch('/api/reader/unread-count', { credentials: 'same-origin' })
          .then((r) => r.json())
          .then((data) => {
            const next = Number(data.count || 0);
            if (next > this.readerUnreadCount && this.currentView === 'articles' && !this.articleDetail) {
              this._loadArticlesWithPaging();
            }
            this.readerUnreadCount = next;
          })
          .catch(() => {});
      }, 15000);
    },
  };
})();
