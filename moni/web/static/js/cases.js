/* cases.js — 同业案例库 Alpine 组件
 * v1.0.17: F6 同业案例库
 * 用法: 在 app() 作用域内挂到 this 上, 通过 x-data 里的方法调用
 */

function casesModule() {
  return {
    // ── 状态 ────────────────────────────────────────────────
    cases: [],                   // 当前案例列表
    casesTotal: 0,
    casesFilterType: '',          // '' / 'AI' / 'PI'
    casesFilterAction: '',        // '' / launch / pilot / scale / transform / exit
    casesFilterReference: '',     // '' / high / medium / low
    casesLoading: false,
    casesSearch: '',              // FTS5 全文检索
    casesSearchResults: [],
    casesDetail: null,            // 选中案例详情
    casesStats: null,

    // ── 类型/动作/参考价值 配置 ──────────────────────────────
    caseTypes: [
      { value: '', label: '全部类型' },
      { value: 'AI', label: 'AI 应用', color: '#5b8ccf' },
      { value: 'PI', label: '产品创新', color: '#d9a861' },
    ],

    caseActions: [
      { value: '', label: '全部动作' },
      { value: 'launch', label: '上线', color: '#5fa98f' },
      { value: 'pilot', label: '试点', color: '#d9a861' },
      { value: 'scale', label: '规模化', color: '#5b8ccf' },
      { value: 'transform', label: '转型', color: '#9b87c4' },
      { value: 'exit', label: '退出', color: '#9aa39a' },
    ],

    caseReferences: [
      { value: '', label: '全部参考价值' },
      { value: 'high', label: '高', color: '#d97a7a' },
      { value: 'medium', label: '中', color: '#d9a861' },
      { value: 'low', label: '低', color: '#9aa39a' },
    ],

    caseTypeColor(type) {
      const t = this.caseTypes.find(x => x.value === type);
      return t ? t.color : '#9aa39a';
    },
    caseTypeLabel(type) {
      const t = this.caseTypes.find(x => x.value === type);
      return t ? t.label : type;
    },
    caseActionLabel(action) {
      const a = this.caseActions.find(x => x.value === action);
      return a ? a.label : action;
    },
    caseReferenceLabel(reference) {
      const r = this.caseReferences.find(x => x.value === reference);
      return r ? r.label : reference;
    },
    caseReferenceColor(reference) {
      const r = this.caseReferences.find(x => x.value === reference);
      return r ? r.color : '#9aa39a';
    },

    // ── 数据加载 ────────────────────────────────────────────
    async loadCases() {
      this.casesLoading = true;
      try {
        const params = new URLSearchParams();
        params.set('limit', '100');
        if (this.casesFilterType) params.set('case_type', this.casesFilterType);
        if (this.casesFilterAction) params.set('case_action', this.casesFilterAction);
        if (this.casesFilterReference) params.set('reference_value', this.casesFilterReference);
        const resp = await fetch(`/api/cases?${params.toString()}`);
        const data = await resp.json();
        this.cases = data.items || [];
        this.casesTotal = data.total || 0;
      } catch (e) {
        console.error('加载案例失败:', e);
      } finally {
        this.casesLoading = false;
      }
    },

    async loadCasesStats() {
      try {
        const resp = await fetch('/api/cases/stats');
        this.casesStats = await resp.json();
      } catch (e) {
        console.error('加载案例统计失败:', e);
      }
    },

    async loadCasesSearch() {
      const q = this.casesSearch.trim();
      if (!q) {
        this.casesSearchResults = [];
        return;
      }
      try {
        const resp = await fetch(`/api/cases/search?q=${encodeURIComponent(q)}`);
        const data = await resp.json();
        this.casesSearchResults = data.items || [];
      } catch (e) {
        console.error('案例搜索失败:', e);
        this.casesSearchResults = [];
      }
    },

    casesOnFilterChange() {
      this.loadCases();
    },

    casesOpenDetail(caseItem) {
      this.casesDetail = caseItem;
    },
    casesCloseDetail() {
      this.casesDetail = null;
    },

    // 查看原文 (复用 articles showArticleDetail)
    casesGoToArticle(articleId) {
      if (!articleId) return;
      this.showArticleDetail({ id: articleId });
    },

    initCasesPage() {
      this.loadCases();
      this.loadCasesStats();
    },
  };
}