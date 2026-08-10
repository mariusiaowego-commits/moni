/* calendar.js — 行业日历 Alpine 组件
 * v1.0.16.1: F5 行业事件日历 UI polish
 *   - 类型色改 OKLCH 软色 + bg/fg 双色对 (匹配 moni 调色板)
 *   - popover 内联 display: none 已删 (与 x-show 冲突导致点击不出)
 *   - 单元格 cursor: pointer 只在有事件时
 *   - 提供 calendarIsToday()/calendarTodayStr() 帮模板判断今天
 */

function calendarModule() {
  return {
    // ── 状态 ────────────────────────────────────────────────
    calendarYear: new Date().getFullYear(),
    calendarMonth: new Date().getMonth() + 1, // 1-12
    calendarEvents: [],       // 当前月的事件（原始数组）
    calendarEventsByDate: {}, // 按日期分组：{ '2026-08-07': [event, ...] }
    calendarPending: [],      // 待确认事件
    calendarFilterType: '',   // 空=全部
    calendarLoading: false,
    calendarStats: null,
    calendarDayModal: null,
    calendarPopover: { show: false, date: null, x: 0, y: 0 },

    // ── 类型配置 (OKLCH 软色对，与 moni 调色板一致) ──────────
    // bg/fg 用在 chip 上（淡背景 + 深色文字），dot 仅用 bg
    calendarTypes: [
      { value: '',              label: '全部类型', dot: '#9aa39a', bg: 'oklch(0.92 0.02 80)',  fg: 'oklch(0.35 0.05 80)' },
      { value: 'regulation',    label: '监管',     dot: '#d97a7a', bg: 'oklch(0.90 0.06 25)',  fg: 'oklch(0.40 0.14 25)' },
      { value: 'product',       label: '产品',     dot: '#5b8ccf', bg: 'oklch(0.91 0.05 250)', fg: 'oklch(0.38 0.13 250)' },
      { value: 'financing',     label: '融资',     dot: '#d9a861', bg: 'oklch(0.92 0.07 80)',  fg: 'oklch(0.40 0.12 70)' },
      { value: 'tech',          label: '技术',     dot: '#5fa98f', bg: 'oklch(0.91 0.06 155)', fg: 'oklch(0.38 0.10 155)' },
      { value: 'other',         label: '其他',     dot: '#9aa39a', bg: 'oklch(0.92 0.02 80)',  fg: 'oklch(0.35 0.04 80)' },
    ],

    calendarTypeColor(type) {
      const t = this.calendarTypes.find(x => x.value === type);
      return t ? t.dot : '#9aa39a';
    },
    calendarTypeLabel(type) {
      const t = this.calendarTypes.find(x => x.value === type);
      return t ? t.label : type;
    },
    calendarTypeStyle(type) {
      // 返回 style 对象用于 chip 的 bg/fg
      const t = this.calendarTypes.find(x => x.value === type);
      if (!t) return '';
      return `background: ${t.bg}; color: ${t.fg};`;
    },

    // ── 日期工具 ────────────────────────────────────────────
    calendarDaysInMonth(year, month) {
      return new Date(year, month, 0).getDate();
    },

    calendarFirstDayOfWeek(year, month) {
      // 0=周日, 1=周一, ..., 6=周六
      return new Date(year, month - 1, 1).getDay();
    },

    calendarMonthLabel() {
      return `${this.calendarYear} 年 ${this.calendarMonth} 月`;
    },

    calendarTodayStr() {
      const n = new Date();
      const mm = String(n.getMonth() + 1).padStart(2, '0');
      const dd = String(n.getDate()).padStart(2, '0');
      return `${n.getFullYear()}-${mm}-${dd}`;
    },

    calendarIsToday(dateStr) {
      return dateStr === this.calendarTodayStr();
    },

    calendarPrevMonth() {
      if (this.calendarMonth === 1) {
        this.calendarMonth = 12;
        this.calendarYear--;
      } else {
        this.calendarMonth--;
      }
      this.loadCalendarEvents();
    },

    calendarNextMonth() {
      if (this.calendarMonth === 12) {
        this.calendarMonth = 1;
        this.calendarYear++;
      } else {
        this.calendarMonth++;
      }
      this.loadCalendarEvents();
    },

    calendarGoToday() {
      const now = new Date();
      this.calendarYear = now.getFullYear();
      this.calendarMonth = now.getMonth() + 1;
      this.loadCalendarEvents();
    },

    // 生成日历格子数据（6行×7列）
    calendarGrid() {
      const days = [];
      const firstDay = this.calendarFirstDayOfWeek(this.calendarYear, this.calendarMonth);
      const totalDays = this.calendarDaysInMonth(this.calendarYear, this.calendarMonth);
      const prevYear = this.calendarMonth === 1 ? this.calendarYear - 1 : this.calendarYear;
      const prevMonth = this.calendarMonth === 1 ? 12 : this.calendarMonth - 1;
      const prevMonthDays = this.calendarDaysInMonth(prevYear, prevMonth);

      // 上月尾部
      for (let i = firstDay - 1; i >= 0; i--) {
        days.push({ day: prevMonthDays - i, isCurrentMonth: false, date: null });
      }
      // 当月
      for (let d = 1; d <= totalDays; d++) {
        const mm = String(this.calendarMonth).padStart(2, '0');
        const dd = String(d).padStart(2, '0');
        days.push({
          day: d,
          isCurrentMonth: true,
          date: `${this.calendarYear}-${mm}-${dd}`,
        });
      }
      // 下月头部（补齐6行）
      let nextDay = 1;
      while (days.length < 42) {
        days.push({ day: nextDay, isCurrentMonth: false, date: null });
        nextDay++;
      }
      return days;
    },

    // 某天有多少事件
    calendarEventCountForDay(dateStr) {
      if (!dateStr) return 0;
      const list = this.calendarEventsByDate[dateStr];
      return list ? list.length : 0;
    },

    calendarEventsForDay(dateStr) {
      if (!dateStr) return [];
      return this.calendarEventsByDate[dateStr] || [];
    },

    // 某天的类型分布（最多 5 个色点 + 计数）
    calendarDotsForDay(dateStr) {
      const events = this.calendarEventsForDay(dateStr);
      const seen = new Set();
      const out = [];
      for (const e of events) {
        if (seen.has(e.event_type)) continue;
        seen.add(e.event_type);
        out.push(e.event_type);
        if (out.length >= 5) break;
      }
      return out;
    },

    calendarHasEvents(dateStr) {
      return this.calendarEventCountForDay(dateStr) > 0;
    },

    // ── 数据加载 ────────────────────────────────────────────
    async loadCalendarEvents() {
      this.calendarLoading = true;
      try {
        let url = `/api/events?year=${this.calendarYear}&month=${this.calendarMonth}&limit=200`;
        if (this.calendarFilterType) {
          // 空字符串 '全部类型' 不带这个 query,避免后端用空字符串过滤
          url += `&event_type=${encodeURIComponent(this.calendarFilterType)}`;
        }
        const resp = await fetch(url);
        const data = await resp.json();
        const exactEvents = (data.items || []).filter(
          e => e.event_date_precision === 'exact'
        );
        this.calendarEvents = exactEvents;
        const byDate = {};
        for (const e of exactEvents) {
          const d = e.event_date;
          if (!byDate[d]) byDate[d] = [];
          byDate[d].push(e);
        }
        this.calendarEventsByDate = byDate;
      } catch (e) {
        console.error('加载日历事件失败:', e);
      } finally {
        this.calendarLoading = false;
      }
    },

    async loadCalendarPending() {
      try {
        const resp = await fetch('/api/events/pending?limit=50');
        const data = await resp.json();
        this.calendarPending = data.items || [];
      } catch (e) {
        console.error('加载待确认事件失败:', e);
      }
    },

    // 人工确认/驳回事件 (P2-2 v1.0.20)
    async calendarReviewEvent(evt, status) {
      if (!evt || !evt.event_id) return;
      const url = `/api/events/${encodeURIComponent(evt.event_id)}/${status}`;
      try {
        const resp = await fetch(url, { method: 'POST' });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        // 从待确认列表移除
        this.calendarPending = (this.calendarPending || []).filter(
          (e) => e.event_id !== evt.event_id
        );
        // 刷新统计
        this.loadCalendarStats();
      } catch (e) {
        console.error('事件确认失败:', e);
        alert('操作失败: ' + e.message);
      }
    },

    async loadCalendarStats() {
      try {
        const resp = await fetch('/api/events/stats');
        this.calendarStats = await resp.json();
      } catch (e) {
        console.error('加载统计失败:', e);
      }
    },

    calendarOnFilterChange() {
      this.loadCalendarEvents();
    },

    // 打开某天的 popover（fixed 定位，跟随点击的格子）
    calendarOpenDay(event, dateStr) {
      const cell = event.currentTarget || event.target.closest('.cal-day');
      if (!cell) return;
      const rect = cell.getBoundingClientRect();
      const popWidth = 320;
      const margin = 8;
      let left = rect.left;
      if (left + popWidth > window.innerWidth - margin) {
        left = window.innerWidth - popWidth - margin;
      }
      if (left < margin) left = margin;
      // 默认弹在格子下方；空间不够则放上方
      let top = rect.bottom + 6;
      if (top + 380 > window.innerHeight - margin) {
        top = rect.top - 380 - 6;
      }
      if (top < margin) top = margin;
      this.calendarPopover = { show: true, date: dateStr, x: left, y: top };
      // 点击外部关闭
      setTimeout(() => {
        const handler = (e) => {
          if (e.target.closest('.cal-popover') || e.target.closest('.cal-day')) return;
          this.calendarPopover.show = false;
          document.removeEventListener('click', handler);
        };
        document.addEventListener('click', handler);
      }, 0);
    },

    calendarClosePopover() {
      this.calendarPopover.show = false;
    },

    // 查看原文：弹出文章详情 modal
    calendarGoToArticle(articleId) {
      this.calendarPopover.show = false;
      if (!articleId) {
        this.navigate('articles');
        return;
      }
      this.showArticleDetail({ id: articleId });
    },

    // 初始化日历页
    initCalendarPage() {
      this.loadCalendarEvents();
      this.loadCalendarPending();
      this.loadCalendarStats();
    },
  };
}