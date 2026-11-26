/* cases.js — 同业案例库 Alpine 组件（V3 · 2026-11）
 * 列表 / 概览带 / 六维筛选 / 沉浸详情。
 * 保留：核验、驳回、编辑、标签、删除、分享。
 */

function casesModule() {
  const DIMS = [
    { key: 'enterprise', label: '企业' },
    { key: 'industry', label: '行业' },
    { key: 'scenario', label: '场景' },
    { key: 'category', label: '类型' },
    { key: 'value', label: '价值' },
    { key: 'month', label: '时间' },
  ];

  const ACTION_LABELS = {
    launch: '上线', pilot: '试点', scale: '规模化', transform: '转型', exit: '退出',
  };
  const VALUE_LABELS = { high: '高价值', medium: '中价值', low: '低价值', none: '未评级' };
  const VALUE_SHORT = { high: '高', medium: '中', low: '低', none: '未评级' };
  const METRIC_LABELS = {
    dau: '日活用户', mau: '月活用户', funding: '融资额',
    contract_value: '合同/预算金额', deal_value: '交易金额', penalty: '罚没金额',
    revenue: '营收', valuation: '估值', investment: '投资额',
    cost_saving: '降本', txn_volume: '交易额', txn_count: '交易笔数',
    user_count: '用户数', coverage: '覆盖规模', headcount: '人员规模',
    efficiency: '效率提升', other: '其他',
  };
  const UNIT_SUFFIX = {
    percent: '%', user: '人', CNY: '元', USD: '美元', EUR: '欧元',
    GBP: '英镑', JPY: '日元', HKD: '港元', x: '倍', institution: '家',
  };

  return {
    // ── V3 状态 ──────────────────────────────────────────────
    cvDims: DIMS,
    cvQ: '',
    cvSel: { enterprise: [], industry: [], scenario: [], category: [], value: [], month: [] },
    cvItems: [],
    cvTotal: 0,
    cvLoading: false,
    cvStats: null,
    cvFacets: null,
    cvPanel: null,
    cvDetail: null,
    cvDetailLoading: false,
    cvExtractBusy: false,
    _cvLoadSeq: 0,
    _cvFacetSeq: 0,
    _cvDetailSeq: 0,
    _cvFocusReturn: null,

    // ── 保留的编辑 / 标签状态 ────────────────────────────────
    casesReviewBusy: false,
    casesEditing: false,
    casesEditBusy: false,
    casesEditForm: { subject: '', counterparty: '', case_action: '', reference_value: '', summary: '' },
    casesTags: [],
    casesTagInput: '',
    casesTagBusy: false,

    caseTypes: [
      { value: '', label: '全部类型' },
      { value: 'AI', label: 'AI 应用' },
      { value: 'PI', label: '产品创新' },
    ],
    caseActions: [
      { value: '', label: '全部动作' },
      { value: 'launch', label: '上线' },
      { value: 'pilot', label: '试点' },
      { value: 'scale', label: '规模化' },
      { value: 'transform', label: '转型' },
      { value: 'exit', label: '退出' },
    ],
    caseReferences: [
      { value: '', label: '全部参考价值' },
      { value: 'high', label: '高' },
      { value: 'medium', label: '中' },
      { value: 'low', label: '低' },
    ],

    // ── 标签映射 ────────────────────────────────────────────
    caseTypeLabel(type) {
      const t = this.caseTypes.find((x) => x.value === type);
      return t ? t.label : (type || '未分类');
    },
    caseActionLabel(action) {
      return ACTION_LABELS[action] || action || '未标注';
    },
    caseReferenceLabel(reference) {
      return VALUE_SHORT[reference] || '未评级';
    },
    cvRefLabel(reference) {
      return VALUE_LABELS[reference] || '未评级';
    },
    caseActionOptions() {
      return this.caseActions.filter((a) => a.value);
    },
    caseReferenceOptions() {
      return this.caseReferences.filter((r) => r.value);
    },

    // ── 卡片辅助 ────────────────────────────────────────────
    cvEnt(cs) {
      return cs.enterprise_name || cs.subject || '未归类';
    },
    cvIndustry(cs) {
      return cs.industry_l1 || '其他';
    },
    cvMonth(cs) {
      const d = cs.case_date || '';
      return /^\d{4}-\d{2}/.test(d) ? d.slice(0, 7) : '';
    },
    cvMetrics(cs) {
      const rows = Array.isArray(cs.metrics) ? cs.metrics : [];
      return rows.slice(0, 2).map((m) => ({
        name: METRIC_LABELS[m.metric_type] || m.metric_type || m.name || '指标',
        value: m.value_raw || (m.value_num != null ? String(m.value_num) : (m.value_approx || '—')),
        unit: m.value_raw ? '' : (UNIT_SUFFIX[m.unit] || ''),
      }));
    },

    // ── 筛选 ────────────────────────────────────────────────
    cvHasFilters() {
      return !!this.cvQ.trim() || DIMS.some((d) => (this.cvSel[d.key] || []).length > 0);
    },
    cvActiveCount(dim) {
      return (this.cvSel[dim] || []).length;
    },
    cvIsSel(dim, value) {
      return (this.cvSel[dim] || []).includes(value);
    },
    cvTokens() {
      const out = [];
      DIMS.forEach((d) => {
        (this.cvSel[d.key] || []).forEach((v) => {
          out.push({ dim: d.key, dimLabel: d.label, value: v, text: this.cvOptionLabel(d.key, v) });
        });
      });
      return out;
    },
    cvOptionLabel(dim, value) {
      if (dim === 'value') return VALUE_SHORT[value] || value;
      if (dim === 'enterprise' && value === 'other') return '其他/未归类';
      const list = this.cvFacets && this.cvFacets[dim];
      const hit = Array.isArray(list) ? list.find((o) => o.value === value) : null;
      if (hit && hit.label) return hit.label;
      if (dim === 'industry') return value;
      return value;
    },
    cvPanelOptions() {
      const dim = this.cvPanel;
      if (!dim || !this.cvFacets) return [];
      const rows = this.cvFacets[dim] || [];
      return rows
        .filter((o) => (o.count || 0) > 0 || this.cvIsSel(dim, o.value))
        .map((o) => ({
          value: o.value,
          label: dim === 'value'
            ? (VALUE_LABELS[o.value] || o.label)
            : (dim === 'action' ? (ACTION_LABELS[o.value] || o.label) : o.label),
          count: o.count || 0,
        }));
    },
    cvOpenPanel(dim) {
      this.cvPanel = this.cvPanel === dim ? null : dim;
    },
    cvToggle(dim, value) {
      const cur = this.cvSel[dim] || [];
      const next = cur.includes(value) ? cur.filter((v) => v !== value) : cur.concat([value]);
      this.cvSel = Object.assign({}, this.cvSel, { [dim]: next });
      this.cvLoad();
      this.cvLoadFacets();
      this.cvSyncURL();
    },
    cvRemoveToken(dim, value) {
      const next = (this.cvSel[dim] || []).filter((v) => v !== value);
      this.cvSel = Object.assign({}, this.cvSel, { [dim]: next });
      this.cvLoad();
      this.cvLoadFacets();
      this.cvSyncURL();
    },
    cvClear() {
      this.cvQ = '';
      this.cvSel = { enterprise: [], industry: [], scenario: [], category: [], value: [], month: [] };
      this.cvPanel = null;
      this.cvLoad();
      this.cvLoadFacets();
      this.cvSyncURL();
    },
    cvOnSearch() {
      this.cvLoad();
      this.cvLoadFacets();
      this.cvSyncURL();
    },

    _cvParams() {
      const p = new URLSearchParams();
      if (this.cvQ.trim()) p.set('q', this.cvQ.trim());
      DIMS.forEach((d) => {
        const vals = this.cvSel[d.key] || [];
        if (vals.length) p.set(d.key, vals.join(','));
      });
      return p;
    },

    cvLoad(append = false) {
      const seq = ++this._cvLoadSeq;
      const p = this._cvParams();
      p.set('limit', '100');
      if (append) p.set('offset', String((this.cvItems || []).length));
      this.cvLoading = true;
      return fetch(`/api/cases?${p.toString()}`, { credentials: 'same-origin' })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
        .then((data) => {
          if (seq !== this._cvLoadSeq) return;
          const items = data.items || [];
          this.cvItems = append ? (this.cvItems || []).concat(items) : items;
          this.cvTotal = data.total || 0;
        })
        .catch((e) => console.error('加载案例失败:', e))
        .finally(() => { if (seq === this._cvLoadSeq) this.cvLoading = false; });
    },
    cvLoadMore() {
      if (this.cvLoading) return null;
      return this.cvLoad(true);
    },

    cvLoadStats() {
      return fetch('/api/cases/stats', { credentials: 'same-origin' })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
        .then((data) => { this.cvStats = data; })
        .catch((e) => console.error('加载案例统计失败:', e));
    },

    // ── 按需抽取（演示：单次最多 10 条）──────────────────────
    async cvExtractRun() {
      if (this.cvExtractBusy) return;
      this.cvExtractBusy = true;
      const notify = (kind, msg) => {
        if (typeof this._notify === 'function') this._notify(kind, msg);
        else console.log('[案例抽取]', kind, msg);
      };
      try {
        const baseline = await this._cvLatestExtractRunId();
        const r = await fetch('/api/cron/jobs/extract_cases_demo/run', {
          method: 'POST',
          credentials: 'same-origin',
        });
        const body = await r.json().catch(() => ({}));
        if (r.status === 409) { notify('info', '抽取任务已在队列中，请稍候'); return; }
        if (!r.ok) throw new Error(this._casesApiError(body, r.status));
        notify('info', '已提交抽取，单次最多 10 条');
        const state = await this._cvWaitExtract(baseline);
        if (state === 'queued') notify('info', '任务已入队，调度器执行后自动完成');
      } catch (e) {
        notify('error', '抽取失败: ' + e.message);
      } finally {
        this.cvExtractBusy = false;
      }
    },

    async _cvLatestExtractRunId() {
      try {
        const r = await fetch('/api/cron/runs?job_id=extract_cases_demo&page_size=1', {
          credentials: 'same-origin',
        });
        if (!r.ok) return 0;
        const runs = await r.json();
        return Array.isArray(runs) && runs.length ? Number(runs[0].id) || 0 : 0;
      } catch (e) {
        return 0;
      }
    },

    async _cvWaitExtract(baseline) {
      // 调度器 15s 一个 tick；最多等 60s，不阻塞页面其他操作。
      for (let i = 0; i < 20; i += 1) {
        await new Promise((res) => setTimeout(res, 3000));
        const r = await fetch('/api/cron/runs?job_id=extract_cases_demo&page_size=1', {
          credentials: 'same-origin',
        });
        if (!r.ok) continue;
        const runs = await r.json();
        const run = Array.isArray(runs) ? runs[0] : null;
        if (!run || Number(run.id) <= baseline) continue;
        if (run.status === 'running') continue;
        if (run.status === 'success') {
          await Promise.all([this.cvLoad(), this.cvLoadStats(), this.cvLoadFacets()]);
          if (typeof this._notify === 'function') this._notify('success', '抽取完成');
          return 'done';
        }
        throw new Error(run.error_message || '抽取任务失败');
      }
      return 'queued';
    },

    cvLoadFacets() {
      const seq = ++this._cvFacetSeq;
      const p = this._cvParams();
      return fetch(`/api/cases/facets?${p.toString()}`, { credentials: 'same-origin' })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
        .then((data) => { if (seq === this._cvFacetSeq) this.cvFacets = data; })
        .catch((e) => console.error('加载筛选选项失败:', e));
    },

    // ── 概览带 ──────────────────────────────────────────────
    cvStatCols() {
      const s = this.cvStats || {};
      const norm = (rows) => (Array.isArray(rows) ? rows.slice(0, 4) : []).map((o) => ({
        value: o.category_code || o.industry_l1 || o.scenario_code || '',
        label: o.category_name || o.industry_l1 || o.scenario_name || '',
        count: o.count || 0,
      }));
      return [
        { key: 'category', title: '类型', rows: norm(s.by_category) },
        { key: 'industry', title: '行业', rows: norm(s.by_industry) },
        { key: 'scenario', title: '场景', rows: norm(s.by_scenario) },
      ];
    },
    cvBarPct(count, rows) {
      const max = Math.max(1, ...rows.map((r) => r.count || 0));
      return Math.round(((count || 0) / max) * 100);
    },
    cvDashToggle(dim, value) {
      if (!value) return;
      this.cvPanel = null;
      this.cvToggle(dim, value);
      const el = document.querySelector('.cv-grid');
      if (el && el.scrollIntoView) el.scrollIntoView({ block: 'start', behavior: 'smooth' });
    },
    cvDashActive(dim, value) {
      return this.cvIsSel(dim, value);
    },

    // ── URL ─────────────────────────────────────────────────
    cvSyncURL() {
      if (window.location.pathname !== '/cases') return;
      const p = this._cvParams();
      if (this.cvDetail && this.cvDetail.case_id) p.set('case', this.cvDetail.case_id);
      const qs = p.toString();
      const url = '/cases' + (qs ? '?' + qs : '');
      try { window.history.replaceState({ view: 'cases' }, '', url); } catch (e) { /* noop */ }
    },

    cvReadURL() {
      const p = new URLSearchParams(window.location.search);
      this.cvQ = p.get('q') || p.get('subject') || '';
      const pick = (key, legacy) => {
        const raw = p.get(key) || (legacy ? p.get(legacy) : '') || '';
        return raw.split(',').map((v) => v.trim()).filter(Boolean);
      };
      this.cvSel = {
        enterprise: pick('enterprise'),
        industry: pick('industry'),
        scenario: pick('scenario'),
        category: pick('category'),
        value: pick('value', 'reference_value'),
        month: pick('month'),
      };
      return p;
    },

    initCasesPage() {
      const p = this.cvReadURL();
      this.cvPanel = null;
      this.cvLoad();
      this.cvLoadStats();
      this.cvLoadFacets();
      const caseId = p.get('case');
      if (caseId) this.cvOpenDetail({ case_id: caseId });
    },

    // ── 详情 ────────────────────────────────────────────────
    cvOpenDetail(cs) {
      if (!cs || !cs.case_id) return;
      const seq = ++this._cvDetailSeq;
      const id = cs.case_id;
      this._cvFocusReturn = document.activeElement;
      this.casesEditing = false;
      this.casesTagInput = '';
      this.casesTags = [];
      this.cvDetail = cs;
      this.cvDetailLoading = true;
      this.cvSyncURL();
      this.$nextTick(() => {
        const el = this.$refs && this.$refs.cvSheet;
        if (el && typeof el.focus === 'function') el.focus();
      });
      this.loadCaseTags(id);
      fetch(`/api/cases/${encodeURIComponent(id)}`, { credentials: 'same-origin' })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
        .then((data) => {
          if (seq !== this._cvDetailSeq) return;
          this.cvDetail = data;
          this.cvSyncURL();
        })
        .catch((e) => console.error('加载案例详情失败:', e))
        .finally(() => { if (seq === this._cvDetailSeq) this.cvDetailLoading = false; });
    },
    cvCloseDetail() {
      this._cvDetailSeq += 1;
      const back = this._cvFocusReturn;
      this._cvFocusReturn = null;
      this.cvDetail = null;
      this.casesEditing = false;
      this.casesTags = [];
      this.casesTagInput = '';
      this.cvSyncURL();
      this.$nextTick(() => {
        if (back && typeof back.focus === 'function') back.focus();
      });
    },
    cvEvidenceBadge(detail) {
      return detail && detail.evidence_verified ? '原文引用' : '模型转述';
    },
    cvHasEvidenceContext(detail) {
      const ctx = detail && detail.evidence_context;
      return !!(ctx && (ctx.context_before || ctx.context_after));
    },

    async cvShare() {
      const id = this.cvDetail && this.cvDetail.case_id;
      if (!id) return;
      try {
        const r = await fetch('/api/share/cases', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ case_ids: [id] }),
        });
        const body = await r.json().catch(() => ({}));
        if (r.status === 409) {
          const sid = body && body.details && body.details.share_id;
          if (sid) this.activeBriefId = sid;
          if (this.loadShareQueue) this.loadShareQueue();
          if (this._startSharePoll) this._startSharePoll();
          if (this._notify) this._notify('info', '已有相同案例简报任务，已跳到分享页');
          this.navigate('share');
          return;
        }
        if (!r.ok) throw new Error(this._casesApiError(body, r.status));
        this.activeBriefId = body.share_id;
        if (this.loadShareQueue) this.loadShareQueue();
        if (this._startSharePoll) this._startSharePoll();
        if (this._notify) this._notify('success', '案例简报已入队');
        this.navigate('share');
      } catch (e) {
        if (this._notify) this._notify('error', '生成分享失败: ' + e.message);
        else console.error('生成分享失败:', e);
      }
    },
    cvCopyLink() {
      const id = this.cvDetail && this.cvDetail.case_id;
      if (!id) return;
      const url = `${window.location.origin}/cases?case=${encodeURIComponent(id)}`;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url)
          .then(() => { if (this._notify) this._notify('success', '链接已复制'); })
          .catch(() => { if (this._notify) this._notify('error', '复制失败'); });
      } else if (this._notify) {
        this._notify('info', url);
      }
    },

    // 查看原文 (复用 articles showArticleDetail)
    casesGoToArticle(articleId) {
      if (!articleId) return;
      this.showArticleDetail({ id: articleId });
    },

    _casesApiError(body, status) {
      if (body && typeof body.message === 'string' && body.message) return body.message;
      if (body && typeof body.detail === 'string' && body.detail) return body.detail;
      return 'HTTP ' + status;
    },

    // ── 核验 / 驳回 ─────────────────────────────────────────
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
        await Promise.all([this.cvLoad(), this.cvLoadStats(), this.cvLoadFacets()]);
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
        this._applyCaseReviewLocal(caseId, { review_status: body.review_status || 'rejected' });
        await Promise.all([this.cvLoad(), this.cvLoadStats(), this.cvLoadFacets()]);
      } catch (e) {
        if (typeof this._notify === 'function') this._notify('error', '驳回失败: ' + e.message);
        else console.error('驳回失败:', e);
      } finally {
        this.casesReviewBusy = false;
      }
    },

    _applyCaseReviewLocal(caseId, patch) {
      const apply = (item) => (item && item.case_id === caseId ? Object.assign({}, item, patch) : item);
      this.cvItems = (this.cvItems || []).map(apply);
      if (this.cvDetail && this.cvDetail.case_id === caseId) {
        this.cvDetail = Object.assign({}, this.cvDetail, patch);
      }
    },

    _removeCaseLocal(caseId) {
      this.cvItems = (this.cvItems || []).filter((c) => c.case_id !== caseId);
      this.cvTotal = Math.max(0, (this.cvTotal || 1) - 1);
    },

    // ── 编辑 ────────────────────────────────────────────────
    casesStartEdit() {
      const d = this.cvDetail || {};
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
      const id = this.cvDetail && this.cvDetail.case_id;
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
          summary: body.summary != null ? body.summary : this.casesEditForm.summary,
          case_action: body.case_action || this.casesEditForm.case_action,
          reference_value: body.reference_value || this.casesEditForm.reference_value,
          counterparty: String(this.casesEditForm.counterparty || '').trim() || null,
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

    // ── 删除 ────────────────────────────────────────────────
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
        if (this.cvDetail && this.cvDetail.case_id === caseId) this.cvCloseDetail();
        if (typeof this._notify === 'function') this._notify('success', '已删除');
        this.cvLoadStats();
        this.cvLoadFacets();
      } catch (e) {
        if (typeof this._notify === 'function') this._notify('error', '删除失败: ' + e.message);
        else console.error('删除失败:', e);
      } finally {
        this.casesReviewBusy = false;
      }
    },

    // ── 标签 ────────────────────────────────────────────────
    async loadCaseTags(caseId) {
      this.casesTags = [];
      if (!caseId) return;
      try {
        const r = await fetch(`/api/cases/${encodeURIComponent(caseId)}/keywords`, {
          credentials: 'same-origin',
        });
        if (!r.ok) return;
        const d = await r.json();
        if (this.cvDetail && this.cvDetail.case_id === caseId) {
          this.casesTags = Array.isArray(d) ? d : (d.items || d.tags || []);
        }
      } catch (e) {
        console.error('加载案例标签失败:', e);
      }
    },

    async casesAddTag() {
      const kw = String(this.casesTagInput || '').trim();
      const id = this.cvDetail && this.cvDetail.case_id;
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
      const id = this.cvDetail && this.cvDetail.case_id;
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
