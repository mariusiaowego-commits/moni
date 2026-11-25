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
    casesSearch: '',              // FTS5 全文检索 / 跳转时的主体筛选
    casesSearchActive: false,     // true = 用户在搜, 显示 FTS 覆盖层
    casesSearchResults: [],
    casesDetail: null,            // 选中案例详情
    casesStats: null,
    casesReviewBusy: false,       // confirm/reject/delete in-flight
    casesEditing: false,
    casesEditBusy: false,
    casesEditForm: { subject: '', counterparty: '', case_action: '', reference_value: '', summary: '' },
    casesTags: [],
    casesTagInput: '',
    casesTagBusy: false,

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
    caseActionOptions() {
      return this.caseActions.filter((a) => a.value);
    },
    caseReferenceOptions() {
      return this.caseReferences.filter((r) => r.value);
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
        // 看板跳转: 搜索框有值但未进 FTS 覆盖层 → 当 subject LIKE 筛选
        if (this.casesSearch.trim() && !this.casesSearchActive) {
          params.set('subject', this.casesSearch.trim());
        }
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
        this.casesSearchActive = false;
        this.loadCases();
        return;
      }
      this.casesSearchActive = true;
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
      this.casesEditing = false;
      this.casesTagInput = '';
      this.casesTags = [];
      this.casesDetail = caseItem;
      if (caseItem && caseItem.case_id) this.loadCaseTags(caseItem.case_id);
    },
    casesCloseDetail() {
      this.casesDetail = null;
      this.casesEditing = false;
      this.casesTags = [];
      this.casesTagInput = '';
    },

    // 查看原文 (复用 articles showArticleDetail)
    casesGoToArticle(articleId) {
      if (!articleId) return;
      this.showArticleDetail({ id: articleId });
    },

    initCasesPage() {
      const params = new URLSearchParams(window.location.search);
      const ref = params.get('reference_value');
      const subject = params.get('subject');
      const act = params.get('case_action');
      const hasQuery = params.has('reference_value') || params.has('subject') || params.has('case_action');
      if (hasQuery) {
        this.casesFilterReference = ref || '';
        this.casesFilterAction = act || '';
        this.casesSearch = subject || '';
        this.casesSearchActive = false;
      } else {
        this.casesFilterReference = '';
        this.casesFilterAction = '';
        this.casesFilterType = '';
        this.casesSearch = '';
        this.casesSearchActive = false;
      }
      this.loadCases();
      this.loadCasesStats();
    },

    _casesApiError(body, status) {
      if (body && typeof body.message === 'string' && body.message) return body.message;
      if (body && typeof body.detail === 'string' && body.detail) return body.detail;
      return 'HTTP ' + status;
    },

    async casesConfirm(caseId, ev) {
      if (ev) ev.stopPropagation();
      if (!caseId || this.casesReviewBusy) return;
      this.casesReviewBusy = true;
      try {
        const r = await fetch(`/api/cases/${encodeURIComponent(caseId)}/confirm`, {
          method: 'POST',
          credentials: 'same-origin',
        });
        const body = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(this._casesApiError(body, r.status));
        if (typeof this._notify === 'function') this._notify('success', '已核验通过');
        this._applyCaseReviewLocal(caseId, {
          review_status: body.review_status || 'confirmed',
          case_lifecycle: body.case_lifecycle || 'verified',
        });
        await Promise.all([this.loadCases(), this.loadCasesStats()]);
        if (this.casesSearchActive) this.loadCasesSearch();
      } catch (e) {
        if (typeof this._notify === 'function') this._notify('error', '核验失败: ' + e.message);
        else console.error('核验失败:', e);
      } finally {
        this.casesReviewBusy = false;
      }
    },

    async casesReject(caseId, ev) {
      if (ev) ev.stopPropagation();
      if (!caseId || this.casesReviewBusy) return;
      const reason = window.prompt('驳回原因（可留空）');
      if (reason === null) return;
      this.casesReviewBusy = true;
      try {
        const q = reason.trim() ? `?reason=${encodeURIComponent(reason.trim())}` : '';
        const r = await fetch(`/api/cases/${encodeURIComponent(caseId)}/reject${q}`, {
          method: 'POST',
          credentials: 'same-origin',
        });
        const body = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(this._casesApiError(body, r.status));
        if (typeof this._notify === 'function') this._notify('success', '已驳回');
        this._applyCaseReviewLocal(caseId, {
          review_status: body.review_status || 'rejected',
        });
        await Promise.all([this.loadCases(), this.loadCasesStats()]);
        if (this.casesSearchActive) this.loadCasesSearch();
      } catch (e) {
        if (typeof this._notify === 'function') this._notify('error', '驳回失败: ' + e.message);
        else console.error('驳回失败:', e);
      } finally {
        this.casesReviewBusy = false;
      }
    },

    _applyCaseReviewLocal(caseId, patch) {
      const apply = (item) => item && item.case_id === caseId ? Object.assign({}, item, patch) : item;
      this.cases = (this.cases || []).map(apply);
      this.casesSearchResults = (this.casesSearchResults || []).map(apply);
      if (this.casesDetail && this.casesDetail.case_id === caseId) {
        this.casesDetail = Object.assign({}, this.casesDetail, patch);
      }
    },

    _removeCaseLocal(caseId) {
      this.cases = (this.cases || []).filter((c) => c.case_id !== caseId);
      this.casesSearchResults = (this.casesSearchResults || []).filter((c) => c.case_id !== caseId);
      this.casesTotal = Math.max(0, (this.casesTotal || 1) - 1);
    },

    casesStartEdit() {
      const d = this.casesDetail || {};
      this.casesEditForm = {
        subject: d.subject || '',
        counterparty: d.counterparty || '',
        case_action: d.case_action || '',
        reference_value: d.reference_value || '',
        summary: d.summary || '',
      };
      this.casesEditing = true;
    },

    casesCancelEdit() {
      this.casesEditing = false;
    },

    async casesSaveEdit() {
      const id = this.casesDetail && this.casesDetail.case_id;
      if (!id || this.casesEditBusy) return;
      const subject = String(this.casesEditForm.subject || '').trim();
      if (!subject) {
        if (typeof this._notify === 'function') this._notify('error', '主体不能为空');
        return;
      }
      this.casesEditBusy = true;
      try {
        const r = await fetch(`/api/cases/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            subject,
            counterparty: String(this.casesEditForm.counterparty || '').trim() || null,
            case_action: this.casesEditForm.case_action,
            reference_value: this.casesEditForm.reference_value,
            summary: String(this.casesEditForm.summary || '').trim(),
          }),
        });
        const body = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(this._casesApiError(body, r.status));
        this._applyCaseReviewLocal(id, {
          subject: body.subject != null ? body.subject : subject,
          counterparty: body.counterparty !== undefined ? body.counterparty : this.casesEditForm.counterparty,
          case_action: body.case_action || this.casesEditForm.case_action,
          reference_value: body.reference_value || this.casesEditForm.reference_value,
          summary: body.summary != null ? body.summary : this.casesEditForm.summary,
        });
        this.casesEditing = false;
        if (typeof this._notify === 'function') this._notify('success', '已保存');
      } catch (e) {
        if (typeof this._notify === 'function') this._notify('error', '保存失败: ' + e.message);
        else console.error('保存失败:', e);
      } finally {
        this.casesEditBusy = false;
      }
    },

    async casesDelete(caseId, ev) {
      if (ev) ev.stopPropagation();
      if (!caseId || this.casesReviewBusy) return;
      const ok = await window.moniModal.confirm(
        '删除后从列表和看板隐藏（软删除，可恢复）。确定删除该案例？',
        { title: '删除案例', okLabel: '删除', danger: true }
      );
      if (!ok) return;
      this.casesReviewBusy = true;
      try {
        const r = await fetch(`/api/cases/${encodeURIComponent(caseId)}`, {
          method: 'DELETE',
          credentials: 'same-origin',
        });
        if (!r.ok && r.status !== 204) {
          const body = await r.json().catch(() => ({}));
          throw new Error(this._casesApiError(body, r.status));
        }
        this._removeCaseLocal(caseId);
        if (this.casesDetail && this.casesDetail.case_id === caseId) this.casesCloseDetail();
        if (typeof this._notify === 'function') this._notify('success', '已删除');
        this.loadCasesStats();
      } catch (e) {
        if (typeof this._notify === 'function') this._notify('error', '删除失败: ' + e.message);
        else console.error('删除失败:', e);
      } finally {
        this.casesReviewBusy = false;
      }
    },

    async loadCaseTags(caseId) {
      this.casesTags = [];
      if (!caseId) return;
      try {
        const r = await fetch(`/api/cases/${encodeURIComponent(caseId)}/keywords`, {
          credentials: 'same-origin',
        });
        if (!r.ok) return;
        const d = await r.json();
        this.casesTags = Array.isArray(d) ? d : (d.items || d.tags || []);
      } catch (e) {
        console.error('加载案例标签失败:', e);
      }
    },

    async casesAddTag() {
      const kw = String(this.casesTagInput || '').trim();
      const id = this.casesDetail && this.casesDetail.case_id;
      if (!kw || !id || this.casesTagBusy) return;
      this.casesTagBusy = true;
      try {
        const r = await fetch(`/api/cases/${encodeURIComponent(id)}/keywords`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ keyword: kw, weight: 1 }),
        });
        const body = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(this._casesApiError(body, r.status));
        this.casesTagInput = '';
        if (body && (body.keyword || body.id)) {
          this.casesTags = this.casesTags.concat([body]);
        } else {
          await this.loadCaseTags(id);
        }
        if (typeof this._notify === 'function') this._notify('success', '已添加标签');
      } catch (e) {
        if (typeof this._notify === 'function') this._notify('error', '添加标签失败: ' + e.message);
        else console.error('添加标签失败:', e);
      } finally {
        this.casesTagBusy = false;
      }
    },

    async casesRemoveTag(kw) {
      const id = this.casesDetail && this.casesDetail.case_id;
      if (!id || !kw || this.casesTagBusy) return;
      const keyword = kw.keyword || kw;
      this.casesTagBusy = true;
      try {
        const r = await fetch(
          `/api/cases/${encodeURIComponent(id)}/keywords/${encodeURIComponent(keyword)}`,
          { method: 'DELETE', credentials: 'same-origin' }
        );
        if (!r.ok && r.status !== 204) {
          const body = await r.json().catch(() => ({}));
          throw new Error(this._casesApiError(body, r.status));
        }
        this.casesTags = (this.casesTags || []).filter(
          (t) => t !== kw && t.id !== kw.id && t.keyword !== kw.keyword
        );
      } catch (e) {
        if (typeof this._notify === 'function') this._notify('error', '删除标签失败: ' + e.message);
        else console.error('删除标签失败:', e);
      } finally {
        this.casesTagBusy = false;
      }
    },
  };
}
