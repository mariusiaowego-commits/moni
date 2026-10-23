// moni safe_url.js — v1.0.47
//
// URL 白名单护栏。index.html 里所有动态 :href 绑定都必须经过
// safeHref()，否则不可信数据（RSS 正文、LLM 联网核查返回的证据 URL、
// 用户配置的信源地址）可以直接注入 javascript: 之类的协议。
// 在 127.0.0.1:8900 这个同源下，点击即等于以站点权限执行脚本。
//
// 放行集合：
//   - http: / https:   —— 外链正文、信源地址、证据链
//   - mailto:          —— 交给系统邮件客户端，无 DOM 执行权限
//   - 同源相对路径      —— /artifacts/... /api/... 等站内产物与接口
//
// 拦截集合（其余一律拒绝）：
//   javascript: data: vbscript: file: blob: about: 以及协议相对跳板
//   //evil.com 、 /\evil.com 、 \evil.com
//
// 纯函数、无 DOM 依赖：可以直接用 node 跑矩阵单测。

(function (global) {
  'use strict';

  var BLOCKED_MESSAGE =
    '该链接协议不安全（仅支持 http/https/mailto/站内路径），已自动拦截';

  function defaultOrigin() {
    if (
      typeof window !== 'undefined' &&
      window.location &&
      window.location.origin
    ) {
      return window.location.origin;
    }
    // node 单测 / SSR 兜底：与 moni 默认监听地址一致
    return 'http://127.0.0.1:8900';
  }

  /**
   * 判断一个 URL 是否可以安全地放进 href。
   *
   * @param {string} rawUrl 待校验的原始字符串
   * @param {string} [baseOrigin] 基准 origin，默认取 window.location.origin
   * @returns {boolean} true = 允许作为 href
   */
  function isSafeUrl(rawUrl, baseOrigin) {
    if (typeof rawUrl !== 'string') return false;

    var trimmed = rawUrl.trim();
    if (!trimmed) return false;

    // 协议相对 URL（//evil.com）浏览器会解析成外站，斜杠跳板（/\evil.com）
    // 在 Chromium / WebKit 里会被折叠成 // —— 两者都必须在前置层拦掉，
    // 绝不能只靠 startsWith('/') 判断"站内路径"。
    if (trimmed.slice(0, 2) === '//') return false;
    if (trimmed.slice(0, 2) === '/\\') return false;
    if (trimmed.charAt(0) === '\\') return false;

    var origin = baseOrigin || defaultOrigin();
    var parsed;
    try {
      parsed = new URL(trimmed, origin);
    } catch (err) {
      // 格式破损 / 非法字符 —— 一律拒绝
      return false;
    }

    var proto = parsed.protocol.toLowerCase();

    if (proto === 'http:' || proto === 'https:') {
      // 以 / 开头的必须解析回同源，否则是伪装成站内路径的外站
      if (trimmed.charAt(0) === '/') {
        return parsed.origin === origin;
      }
      // 完整 URL：必须有合法主机名
      return !!parsed.hostname;
    }

    if (proto === 'mailto:') {
      return trimmed.charAt(0) !== '/' && parsed.pathname.length > 0;
    }

    return false;
  }

  /**
   * 安全则返回可用的 href 字符串，不安全则返回 null。
   * Alpine 绑定到 null 时会移除 href 属性 —— 链接退化成纯文本，无法点击。
   *
   * @param {string} rawUrl
   * @param {string} [baseOrigin]
   * @returns {string|null}
   */
  function safeHref(rawUrl, baseOrigin) {
    if (!isSafeUrl(rawUrl, baseOrigin)) return null;
    return rawUrl.trim();
  }

  /**
   * 是否"存在但不可信"的 URL —— 也就是需要显示拦截徽章的那一类。
   *
   * 空值 (undefined / null / "") 不算被拦截：那只是"没有链接"，
   * 不是"有一条被拦下的链接"，两者在 UI 上要区分开。
   *
   * @param {string} rawUrl
   * @param {string} [baseOrigin]
   * @returns {boolean}
   */
  function isBlockedUrl(rawUrl, baseOrigin) {
    if (!rawUrl) return false;
    return !isSafeUrl(rawUrl, baseOrigin);
  }

  var api = {
    isSafeUrl: isSafeUrl,
    safeHref: safeHref,
    isBlockedUrl: isBlockedUrl,
    BLOCKED_MESSAGE: BLOCKED_MESSAGE,
  };

  // 暴露为全局，供 Alpine 表达式直接调用
  global.isSafeUrl = isSafeUrl;
  global.safeHref = safeHref;
  global.isBlockedUrl = isBlockedUrl;
  global.SAFE_URL_BLOCKED_MESSAGE = BLOCKED_MESSAGE;
  global.SafeUrl = api;

  // node 单测入口
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
