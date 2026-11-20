/* job_view.js — 阅读器 LLM 任务的「状态 → 界面文字」纯函数。
 *
 * 为什么要单独一个文件：等待态是这个功能里最容易写歪的部分（散在 5 个
 * 方法里各拼各的字符串，改一处漏一处）。这里把判断全部收成纯函数，
 * 浏览器和 node 用同一份实现，表驱动用例放在 tests/fixtures/job_view_cases.json，
 * tests/test_job_view_node.py 直接 node 跑一遍 —— 改文案/改判断不可能只改一边。
 *
 * PHASES / SLOT_OF_TASK 与后端 moni/llm/phases.py 是同一份词表，
 * 两边由 node 测试比对。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.moniJobView = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var PHASES = ['none', 'queued', 'running', 'done', 'failed', 'cancelled', 'stale'];

  var SLOT_OF_TASK = {
    summary: 'summary',
    fact_check: 'fact_check',
    fact_check_plus: 'fact_check',
  };

  var STATUS_TO_PHASE = {
    pending: 'queued',
    running: 'running',
    done: 'done',
    failed: 'failed',
    stale: 'stale',
    cancelled: 'cancelled',
  };

  function phaseOf(status) {
    if (!status) return 'none';
    return STATUS_TO_PHASE[String(status)] || 'none';
  }

  function slotOf(taskType) {
    if (!taskType) return null;
    return SLOT_OF_TASK[String(taskType)] || null;
  }

  function pad2(n) {
    return n < 10 ? '0' + n : String(n);
  }

  /** 毫秒 → 「12 秒」/「3 分 05 秒」/「1 小时 02 分」。 */
  function formatElapsed(ms) {
    var total = Math.floor((Number(ms) || 0) / 1000);
    if (total < 0) total = 0;
    if (total < 60) return total + ' 秒';
    var minutes = Math.floor(total / 60);
    if (minutes < 60) return minutes + ' 分 ' + pad2(total % 60) + ' 秒';
    return Math.floor(minutes / 60) + ' 小时 ' + pad2(minutes % 60) + ' 分';
  }

  function parseMs(value) {
    if (!value) return null;
    var ms = Date.parse(String(value));
    return isNaN(ms) ? null : ms;
  }

  function emptyView(extra) {
    var out = {
      phase: 'none',
      show: false,
      busy: false,
      text: '',
      detail: '',
      tone: 'idle',
      retryable: false,
      ignore: false,
    };
    if (extra) {
      for (var k in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, k)) out[k] = extra[k];
      }
    }
    return out;
  }

  /**
   * 把一条任务（+ 当前时间）翻成界面要的那几行字。
   *
   * input = {
   *   slot:      'summary' | 'fact_check',
   *   job:       /api/llm/jobs/{id} 的响应，或 null,
   *   articleId: 当前阅读器打开的文章 id（用于丢弃迟到的别的文章回包）,
   *   nowMs:     渲染时刻（秒表靠它重算，不靠累加）,
   *   errorText: 已翻成人话的失败原因（可选，缺省用 job.error）,
   *   progress:  流式进度（PR-4 才有）
   * }
   */
  function viewFor(input) {
    var opts = input || {};
    var job = opts.job;
    if (!job) return emptyView({ slot: opts.slot || null });

    // 换篇之后迟到的回包：整条丢弃，绝不能写进当前这篇的槽位。
    if (opts.articleId != null && job.article_id != null && String(job.article_id) !== String(opts.articleId)) {
      return emptyView({ slot: opts.slot || null, ignore: true });
    }

    var phase = phaseOf(job.status);
    var slot = opts.slot || slotOf(job.task_type) || null;
    var now = typeof opts.nowMs === 'number' ? opts.nowMs : Date.now();
    var createdMs = parseMs(job.created_at);
    var startedMs = parseMs(job.started_at) || createdMs;

    // done 之后 app.js 会先回读文章详情，成功才清槽位。回读在途/失败时
    // 需要一条轻量提示，否则进度条一闪就没、页面看起来像什么都没发生。
    if (phase === 'done' && opts.refreshFailed) {
      return {
        slot: slot,
        phase: phase,
        show: true,
        busy: false,
        text: '结果已生成，点此刷新',
        detail: '详情没有拉到，重试一次就能看到结论',
        tone: 'failed',
        retryable: false,
        ignore: false,
      };
    }
    if (phase === 'done' && opts.refreshing) {
      return {
        slot: slot,
        phase: phase,
        show: true,
        busy: false,
        text: '已生成，正在刷新…',
        detail: '正在回读文章详情',
        tone: 'running',
        retryable: false,
        ignore: false,
      };
    }

    if (phase === 'none' || phase === 'done') {
      // done 的交接由 app.js 做（回读详情并清空槽位），这里只负责隐藏状态条。
      return emptyView({ slot: slot, phase: phase });
    }

    // 跟随器已经放弃轮询（30 分钟预算），但服务端可能还在跑：说实话，
    // 别让用户以为任务没了、又重复点一次。
    if (opts.gaveUp && (phase === 'queued' || phase === 'running')) {
      return emptyView({
        slot: slot,
        phase: phase,
        show: true,
        busy: false,
        text: '仍在后台运行',
        detail: '已超过本轮跟随上限，稍后再进本文会自动同步结果',
        tone: 'running',
        retryable: true,
      });
    }

    if (phase === 'queued') {
      var waited = createdMs == null ? null : now - createdMs;
      var head = '排队中';
      if (job.queue_position && job.queue_total) {
        head += ' · 第 ' + job.queue_position + '/' + job.queue_total + ' 位';
      }
      return emptyView({
        slot: slot,
        phase: phase,
        show: true,
        busy: true,
        text: head,
        detail: waited == null ? '工作线程一次只跑一个任务' : '已等待 ' + formatElapsed(waited),
        tone: 'queued',
      });
    }

    if (phase === 'running') {
      var elapsed = startedMs == null ? null : now - startedMs;
      var detail = '';
      var progress = opts.progress;
      if (progress && Number(progress.searches) > 0) {
        detail = '已检索 ' + progress.searches + ' 次';
        if (progress.last_query) detail += ' · 最近「' + progress.last_query + '」';
      } else if (elapsed != null) {
        detail = '模型正在读全文，还没有联网检索动作';
      }
      return emptyView({
        slot: slot,
        phase: phase,
        show: true,
        busy: true,
        text: elapsed == null ? '正在跑' : '已运行 ' + formatElapsed(elapsed),
        detail: detail,
        tone: 'running',
      });
    }

    if (phase === 'failed') {
      return emptyView({
        slot: slot,
        phase: phase,
        show: true,
        busy: false,
        text: '这次没跑完',
        detail: opts.errorText || job.error || '原因未知，可以重试',
        tone: 'failed',
        retryable: true,
      });
    }

    if (phase === 'stale') {
      return emptyView({
        slot: slot,
        phase: phase,
        show: true,
        busy: false,
        text: '服务中断或超时中止',
        detail: '后台没有心跳了，这一轮的结果没写回。可以重跑一次。',
        tone: 'stale',
        retryable: true,
      });
    }

    // cancelled
    return emptyView({
      slot: slot,
      phase: phase,
      show: true,
      busy: false,
      text: '已取消',
      detail: '任务被取消，旧结论保留在原处。',
      tone: 'cancelled',
      retryable: true,
    });
  }

  return {
    PHASES: PHASES,
    SLOT_OF_TASK: SLOT_OF_TASK,
    STATUS_TO_PHASE: STATUS_TO_PHASE,
    phaseOf: phaseOf,
    slotOf: slotOf,
    formatElapsed: formatElapsed,
    viewFor: viewFor,
  };
});
