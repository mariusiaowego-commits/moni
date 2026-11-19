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
  // AI answers quote the article with 「」. Those quotes become citation
  // chips that point at the paragraph holding the same text, so a claim can
  // be checked against the source instead of being taken on trust.
  const QUOTE_SPLIT_RE = /(「[^」]{4,160}」)/g;
  const BLOCK_SELECTOR = 'p, li, h2, h3, h4, blockquote';
  const YES_RE = /^(✓|✔|√|是|支持)$/;
  const NO_RE = /^(✗|✘|×|✕|否|不支持)$/;

  let articleBlocks = [];

  /** Readable text left after rendering: used to flag image-only articles. */
  function plainTextLength(html) {
    const box = document.createElement('div');
    box.innerHTML = html;
    return (box.textContent || '').replace(/\s+/g, '').length;
  }

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
      table.classList.add('ai-table');
      table.querySelectorAll('td, th').forEach((cell) => {
        const text = cell.textContent.trim();
        if (YES_RE.test(text)) cell.classList.add('is-yes');
        else if (NO_RE.test(text)) cell.classList.add('is-no');
      });
      const wrap = document.createElement('div');
      wrap.className = 'table-scroll';
      table.parentNode.insertBefore(wrap, table);
      wrap.appendChild(table);
    });
    return box.innerHTML;
  }

  /** Number every block in the rendered article so quotes can link to it. */
  function indexArticleBlocks(root) {
    articleBlocks = Array.from(root.querySelectorAll(BLOCK_SELECTOR));
    articleBlocks.forEach((el, i) => { el.id = 'p' + (i + 1); });
  }

  /**
   * Turn 「…」 in an assistant answer into numbered citation chips and append
   * a source footer. A quote only gets a chip when the article body actually
   * contains that text; unmatched quotes stay as plain text.
   */
  function addCitations(html) {
    if (!articleBlocks.length || !html.includes('「')) return html;
    const holder = document.createElement('div');
    holder.innerHTML = html;
    const refs = [];
    const walker = document.createTreeWalker(holder, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach((node) => {
      const value = node.nodeValue || '';
      if (!value.includes('「')) return;
      const parts = value.split(QUOTE_SPLIT_RE);
      if (parts.length === 1) return;
      const frag = document.createDocumentFragment();
      parts.forEach((part) => {
        const quote = part.slice(1, -1);
        const hit = part.startsWith('「')
          ? articleBlocks.findIndex((el) => (el.textContent || '').includes(quote))
          : -1;
        if (hit < 0) {
          frag.appendChild(document.createTextNode(part));
          return;
        }
        let ref = refs.findIndex((r) => r.index === hit);
        if (ref < 0) {
          refs.push({ index: hit, quote });
          ref = refs.length - 1;
        }
        const link = document.createElement('a');
        link.className = 'cite-chip';
        link.href = '#p' + (hit + 1);
        link.dataset.quote = quote;
        link.textContent = String(ref + 1);
        link.title = '跳到原文对应段落';
        frag.appendChild(document.createTextNode(part));
        frag.appendChild(link);
      });
      node.parentNode.replaceChild(frag, node);
    });
    if (!refs.length) return html;
    const footer = document.createElement('div');
    footer.className = 'cite-footer';
    refs.forEach((ref, i) => {
      const row = document.createElement('a');
      row.className = 'cite-ref';
      row.href = '#p' + (ref.index + 1);
      row.dataset.quote = ref.quote;
      const excerpt = (articleBlocks[ref.index].textContent || '').trim().slice(0, 46);
      row.innerHTML = `<span class="cite-chip">${i + 1}</span>`
        + `<span class="cite-ref-text"></span>`
        + `<span class="cite-ref-pos"></span>`;
      row.querySelector('.cite-ref-text').textContent = excerpt;
      row.querySelector('.cite-ref-pos').textContent = `段落 ${ref.index + 1}`;
      footer.appendChild(row);
    });
    holder.appendChild(footer);
    return holder.innerHTML;
  }

  function renderMarkdown(text) {
    const parser = md();
    if (!parser) return '';
    return sanitize(parser.parse(normalizeTables(String(text || ''))));
  }

  /**
   * GFM needs a blank line before a table, and a table row has to start the
   * line. Answers often glue the first row onto the previous sentence
   * (`…结论。| 层级 | 说明 |`), which would otherwise render as literal pipes.
   */
  function normalizeTables(text) {
    return text.split('\n').map((line) => {
      if (line.trimStart().startsWith('|')) return line;
      const pipes = (line.match(/\|/g) || []).length;
      if (pipes < 3 || !line.trimEnd().endsWith('|')) return line;
      const cut = line.indexOf('|');
      return cut > 0 ? line.slice(0, cut).trimEnd() + '\n\n' + line.slice(cut) : line;
    }).join('\n');
  }

  function renderArticle(text) {
    let value = String(text || '');
    value = value.replace(FRONTMATTER_RE, '');
    // The bank metadata line lives in the sticky bar, not in the body.
    value = value.replace(BANK_RE, '');
    value = value.replace(MD_IMAGE_RE, '').replace(HTML_IMAGE_RE, '');
    if (!md()) {
      const escaped = value
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      return escaped.split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
    }
    return renderMarkdown(value);
  }

  /**
   * Sprint 51: hermes 的 bullet 回答常挤成一整行（"1. … 2. …"），
   * marked 把它渲染成一段文字。这里在序号前插换行让 marked 生成真实 <ol>。
   * 只在「序号前是句末/引号/空白」时切，避免拆断引文或百分比数字。
   */
  function structureListMarkdown(text) {
    const value = String(text || '');
    if (value.includes('\n')) return value;
    const re = /(\d{1,2})\.\s+/g;
    const cuts = [];
    let m;
    while ((m = re.exec(value))) {
      const i = m.index;
      if (i > 0) {
        const prev = value[i - 1];
        if (/[\u3002\uff01\uff1f\uff1b\uff1a\u201c\u201d\u2018\u2019\u300d\u300f\uff09)\s\n]/.test(prev)) {
          cuts.push(i);
        }
      }
      if (cuts.length >= 19) break;
    }
    if (cuts.length < 2) return value;
    let out = '';
    let prev = 0;
    for (const cut of cuts) {
      out += value.slice(prev, cut) + '\n';
      prev = cut;
    }
    return out + value.slice(prev);
  }

  /**
   * 把渲染出的顶层 <ol>/<ul> 标成 .ai-structured-list，并去掉 li 里
   * 自带的 "1. " 字面序号，避免与 CSS 徽标重复。
   */
  function structureRenderedLists(html) {
    const holder = document.createElement('div');
    holder.innerHTML = html;
    holder.querySelectorAll('ol, ul').forEach((list) => {
      const lis = list.querySelectorAll(':scope > li');
      if (lis.length < 2) return;
      if (list.closest('li')) return;
      list.classList.add('ai-structured-list');
      if (list.tagName === 'OL') {
        lis.forEach((li) => {
          const first = li.firstChild;
          if (first && first.nodeType === Node.TEXT_NODE) {
            first.nodeValue = first.nodeValue.replace(/^\s*\d{1,2}\.\s*/, '');
          }
        });
      }
    });
    return holder.innerHTML;
  }

  /** Drop the previous citation mark so only one passage stays lit. */
  function clearReaderFlash() {
    document.querySelectorAll('.reader-body .reader-flash').forEach((el) => {
      if (el.tagName === 'MARK') {
        const parent = el.parentNode;
        while (el.firstChild) parent.insertBefore(el.firstChild, el);
        parent.removeChild(el);
        parent.normalize();
      } else {
        el.classList.remove('reader-flash');
      }
    });
  }

  /** Paint the quoted sentence, or the whole block when the quote spans tags. */
  function flashQuotedText(target, quote) {
    clearReaderFlash();
    const needle = String(quote || '').trim();
    if (needle) {
      const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
      let node = walker.nextNode();
      while (node) {
        const idx = node.nodeValue.indexOf(needle);
        if (idx >= 0) {
          const range = document.createRange();
          range.setStart(node, idx);
          range.setEnd(node, idx + needle.length);
          const mark = document.createElement('mark');
          mark.className = 'reader-flash';
          try {
            range.surroundContents(mark);
            return mark;
          } catch (err) {
            break;
          }
        }
        node = walker.nextNode();
      }
    }
    target.classList.add('reader-flash');
    return target;
  }

  function scrollReaderTo(node) {
    const scroll = document.querySelector('.reader-scroll');
    if (!scroll) {
      node.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
    const top = scroll.scrollTop
      + node.getBoundingClientRect().top
      - scroll.getBoundingClientRect().top
      - 24;
    scroll.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  }

  // Citation chips and footer rows are anchors (`#pN`) inside the AI panel.
  // Keep the existing jump, and leave the quoted sentence highlighted until
  // the next citation so the landing spot stays obvious.
  document.addEventListener('click', (event) => {
    const link = event.target.closest?.('a.cite-chip, a.cite-ref');
    if (!link) return;
    const href = link.getAttribute('href') || '';
    const id = href.startsWith('#') ? href.slice(1) : '';
    const target = id ? document.getElementById(id) : null;
    const body = document.querySelector('.reader-body');
    if (!target || !body || !body.contains(target)) return;
    event.preventDefault();
    const marked = flashQuotedText(target, link.dataset.quote || '');
    scrollReaderTo(marked);
  });

  /** Parse a `[bank:icbc type:media target:yicai-finance]` marker. */
  function parseBankMeta(text) {
    BANK_RE.lastIndex = 0;
    const match = BANK_RE.exec(String(text || ''));
    if (!match) return [];
    const parts = [];
    for (const token of match[1].split(/\s+/).filter(Boolean)) {
      const idx = token.indexOf(':');
      if (idx > 0) parts.push({ k: token.slice(0, idx), v: token.slice(idx + 1) });
      else parts.push({ k: 'bank', v: token });
    }
    return parts;
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
    // 正文 / 摘要 / 事实核查 三个内容页签（与窄屏的「对话」页签正交）。
    readerContentTab: 'read',
    readerFactReportOpen: false,
    // 窄屏才有的「整屏对话」状态；宽屏右侧常驻 AI，不需要这套切换。
    readerIsNarrow: false,
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
    // 用户是否贴着底部：贴着才跟着流式输出滚动，上滑阅读时不被拽回。
    readerAiPinned: true,
    readerBankParts: [],
    readerRawOpen: false,
    readerImportOpen: false,
    readerRendered: '',
    readerBodyEmpty: false,
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
      this.readerContentTab = 'read';
      this.readerFactReportOpen = false;
      this.readerSyncViewport();
      fetch(`/api/articles/${article.id}`, { credentials: 'same-origin' })
        .then((r) => {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then((data) => {
          this.articleDetail = data;
          this.readerRendered = renderArticle(data.content || '');
          this.readerBodyEmpty = plainTextLength(this.readerRendered) < 80;
          this.readerBankParts = parseBankMeta(data.content || '');
          // 证据链此前只在核查任务跑完的那一次会话里加载，直接打开文章会看到空列表。
          this.articleEvidenceList = [];
          if (data.has_web_fact_check) this.loadArticleEvidence(data.id);
          this.readerLoadPrompts();
          this.readerLoadConversation();
        })
        .catch((err) => this._notify('error', '加载文章失败: ' + err.message));
    },

    closeArticleDetail() {
      this.readerCloseStream();
      this.articleDetail = null;
      this.readerBodyEmpty = false;
      this.readerBankParts = [];
      this.readerRawOpen = false;
      this.readerImportOpen = false;
      this.readerSelectionVisible = false;
      this.readerExpanded = false;
      this.readerMobileTab = 'read';
      this.readerContentTab = 'read';
      this.readerFactReportOpen = false;
      this.summaryLoading = false;
      this.factCheckLoading = false;
    },

    /** Esc 先退出全屏，再关闭阅读器。 */
    readerHandleEscape() {
      if (this.readerExpanded) {
        this.readerExpanded = false;
        return;
      }
      this.closeArticleDetail();
    },

    readerSwitchContentTab(tab) {
      this.readerContentTab = tab;
      // 窄屏下「对话」是整屏视图，切内容页签必须把整屏切回来，
      // 否则页签高亮了但格子里还是 AI 面板。
      this.readerMobileTab = 'read';
      this.readerFactReportOpen = false;
      this.$nextTick(() => {
        const box = this.$refs?.readerScroll || document.querySelector('.reader-scroll');
        if (box) box.scrollTop = 0;
      });
    },

    readerSwitchToAi() {
      this.readerMobileTab = 'ai';
      this.readerScrollToBottom();
    },

    readerSyncViewport() {
      this.readerIsNarrow = window.matchMedia('(max-width: 1100px)').matches;
    },

    /** 窄屏切到「对话」时，内容页签不再算选中（否则 tablist 里两个 aria-selected=true）。 */
    get readerAiFocused() {
      return this.readerIsNarrow && this.readerMobileTab === 'ai';
    },

    /**
     * 历史联网核查结论是整篇 markdown 报告（## 核查结论 + ## 逐条核查）。
     * 结论段直接显示，逐条核查折叠起来，否则页签里只剩一个按钮。
     */
    get readerFactSplit() {
      const text = String(this.articleDetail?.fact_check_conclusion || '');
      if (!text || text.length <= 200) return { preview: text, rest: '' };
      const cut = text.search(/\n##\s+\S/);
      if (cut > 0) return { preview: text.slice(0, cut).trim(), rest: text.slice(cut).trim() };
      const para = text.indexOf('\n\n');
      if (para > 0) return { preview: text.slice(0, para).trim(), rest: text.slice(para).trim() };
      return { preview: text.slice(0, 160).trim() + '…', rest: text };
    },

    get readerFactPreviewText() {
      return this.readerFactSplit.preview;
    },

    get readerFactRestText() {
      return this.readerFactSplit.rest;
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
          this.readerMessages = Array.isArray(data.messages)
            ? data.messages.map((m) => this.readerInitMessage(m))
            : [];
          if (this.readerConversation) {
            this.readerMarkRead();
            this.readerResumeStream();
            this.readerAiPinned = true;
            this.readerScrollToBottom();
          }
        })
        .catch((err) => console.error('readerLoadConversation failed:', err));
    },

    /**
     * settled / tail 必须一开始就存在 —— Alpine 3 对后加的属性不响应。
     * settled = 已经成型的块（渲染一次就冻结），tail = 还在写的最后一块。
     */
    readerInitMessage(m) {
      const msg = { ...m, settled: '', tail: '' };
      if (msg.role === 'assistant') msg.settled = String(msg.content || '');
      return msg;
    },

    /**
     * 在空行处切一刀，且只切代码围栏成对的位置 —— 否则 settled 里会留下
     * 一个没闭合的 ```，整段会被渲染成代码块。
     */
    readerSplitStream(msg) {
      const text = String(msg.content || '');
      const fences = [];
      for (let i = text.indexOf('```'); i !== -1; i = text.indexOf('```', i + 3)) fences.push(i);
      let idx = text.lastIndexOf('\n\n');
      while (idx > 0) {
        let before = 0;
        while (before < fences.length && fences[before] < idx) before += 1;
        if (before % 2 === 0) {
          msg.settled = text.slice(0, idx).trimEnd();
          msg.tail = text.slice(idx).trimStart();
          return;
        }
        idx = text.lastIndexOf('\n\n', idx - 1);
      }
      msg.settled = '';
      msg.tail = text;
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
      this.readerMessages = Array.isArray(data.messages)
        ? data.messages.map((m) => this.readerInitMessage(m))
        : [];
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
          settled: '',
          tail: '',
          job_id: data.job_id,
          status: 'pending',
          reasoning: this.readerReasoning,
          mode: this.readerContextLabel,
          startedAt: performance.now(),
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
      const resumed = message();
      if (resumed && !resumed.startedAt) resumed.startedAt = performance.now();

      source.addEventListener('snapshot', (ev) => {
        try {
          const data = JSON.parse(ev.data);
          const msg = message();
          if (msg) {
            msg._pending = '';
            msg.content = data.content || '';
            msg.status = data.status || 'streaming';
            this.readerSplitStream(msg);
          }
          this.readerFollowStream();
        } catch (e) { /* ignore */ }
      });
      source.addEventListener('delta', (ev) => {
        try {
          const data = JSON.parse(ev.data);
          const msg = message();
          if (msg) this.readerPushDelta(msg, data.text || '');
        } catch (e) { /* ignore */ }
      });
      source.addEventListener('done', (ev) => {
        try {
          const data = JSON.parse(ev.data);
          const msg = message();
          if (msg) {
            this.readerFlushDelta(msg);
            msg.status = 'done';
            msg.settled = String(msg.content || '');
            msg.tail = '';
            msg.outputTokens = data.output_tokens || 0;
          }
          this.readerStreaming = false;
          this.readerStreamJobId = null;
          this.readerStreamMsgId = null;
          this.readerCloseStream();
          this.readerMarkRead();
          this.readerFollowStream();
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
            this.readerFlushDelta(msg);
            msg.status = data.status || 'failed';
            msg.error = data.message || '生成失败';
            this.readerFinaliseMessage(msg);
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

    // SSE delivers 40–80 character bursts; revealing them a few characters at
    // a time keeps the answer reading as typing rather than as jumps.
    readerPushDelta(msg, text) {
      msg._pending = (msg._pending || '') + text;
      if (!msg.thoughtMs && msg.startedAt) {
        msg.thoughtMs = Math.max(0, performance.now() - msg.startedAt);
      }
      if (msg._typing) return;
      msg._typing = true;
      const step = () => {
        if (!msg._pending) {
          msg._typing = false;
          return;
        }
        msg.content = (msg.content || '') + msg._pending.slice(0, 3);
        msg._pending = msg._pending.slice(3);
        this.readerSplitStream(msg);
        this.readerFollowStream();
        setTimeout(step, 12);
      };
      step();
    },

    /** 结束（完成 / 失败 / 取消）后把全文定稿，避免 tail 半截留在流式容器里。 */
    readerFinaliseMessage(msg) {
      msg.settled = String(msg.content || '');
      msg.tail = '';
      msg._typing = false;
    },

    readerFlushDelta(msg) {
      if (msg._pending) {
        msg.content = (msg.content || '') + msg._pending;
        msg._pending = '';
      }
      msg._typing = false;
      this.readerSplitStream(msg);
    },

    readerStop() {
      if (!this.readerStreamJobId) return;
      fetch(`/api/reader/jobs/${this.readerStreamJobId}/cancel`, {
        method: 'POST',
        credentials: 'same-origin',
      }).catch(() => {});
      const active = (this.readerMessages || []).find(
        (m) => m.job_id === this.readerStreamJobId && m.role === 'assistant');
      if (active) {
        this.readerFlushDelta(active);
        active.status = 'cancelled';
        this.readerFinaliseMessage(active);
      }
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
      if (this._scrollFrame) return;
      this._scrollFrame = requestAnimationFrame(() => {
        this._scrollFrame = 0;
        const box = this.$refs?.aiMessages || document.querySelector('.ai-messages');
        if (box) box.scrollTop = box.scrollHeight;
      });
    },

    /** 用户上滑阅读时不再被流式输出拽回底部。 */
    readerTrackAiScroll(ev) {
      const box = ev.target;
      this.readerAiPinned = (box.scrollHeight - box.scrollTop - box.clientHeight) < 48;
    },

    readerFollowStream() {
      if (!this.readerAiPinned) return;
      this.readerScrollToBottom();
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
      // x-html writes the string after this getter returns; index the blocks
      // once the browser has them so citation chips can resolve.
      queueMicrotask(() => {
        const body = document.querySelector('.reader-body');
        if (body) indexArticleBlocks(body);
      });
      return this.readerRendered || '';
    },

    readerRenderMarkdown(text) {
      let html = renderMarkdown(structureListMarkdown(text));
      html = structureRenderedLists(html);
      return addCitations(html);
    },

    /** 核查结论等「模型产物」不挂正文引用 chip：那些「」不是文章里的引文。 */
    readerRenderMarkdownPlain(text) {
      let html = renderMarkdown(structureListMarkdown(text));
      return structureRenderedLists(html);
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
