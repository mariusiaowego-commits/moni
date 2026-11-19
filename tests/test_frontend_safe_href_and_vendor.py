"""tests/test_frontend_safe_href_and_vendor.py — v1.0.47 前端安全契约断言.

锁住两件事:

1. **URL 白名单** — index.html 里每一个动态 URL 绑定都必须经过
   ``safeHref()``。这些 URL 全部来自不可信数据源 (RSS 正文 / LLM 联网
   核查返回的证据 URL / 用户配置的信源地址)，在 127.0.0.1:8900 同源下
   一旦放行 ``javascript:``，点击即以站点权限执行脚本。
2. **零外网依赖** — Alpine / GSAP 必须自托管。内网、离线、DNS 被污染
   时 CDN 拉不到，Alpine 起不来，``x-cloak`` 让整页持续空白。

判定口径:
- AC-1 用**全量扫描**: 抓到任何一个没走 safeHref 的 URL sink 就红,
  并报出 file:line —— 不是"抽查 11 处"。sink 覆盖 :href / :src /
  :srcset / :action / :formaction, 不只是 href。
- AC-2 区分外链与本地相对路径: 只放行 vendor/ js/ css/ icons/ 前缀。
- AC-4 用 **node 子进程**跑矩阵: safe_url.js 是纯函数, 真的求值一遍
  比正则断言可靠 (方案来自 docs/plans/2026-10-23-frontend-safe-href-and-
  offline-assets.md §C 契约 4)。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "moni" / "web" / "static"
INDEX_HTML = STATIC / "index.html"
MAIN_CSS = STATIC / "css" / "main.css"
SAFE_URL_JS = STATIC / "js" / "safe_url.js"
VENDOR_DIR = STATIC / "vendor"
SHOWCASE_BUILDER = (
    Path(__file__).resolve().parents[1] / "scripts" / "build_showcase_html.py"
)

ALPINE_FILE = "alpine-3.14.1.min.js"
GSAP_FILE = "gsap-3.12.5.min.js"

#: SHA-256 防漂移 —— 任何对 vendor 文件的二次修改 / 重新压缩都会让断言变红.
#: 同一份哈希记录在 vendor/README.md 里.
EXPECTED_SHA256 = {
    ALPINE_FILE: "358d9afbb1ab5befa2f48061a30776e5bcd7707f410a606ba985f98bc3b1c034",
    GSAP_FILE: "28033e449a31ebcc396e5be8b13b63152bf03094288fb5867034321927bce087",
}

EXTERNAL_HOSTS = (
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
)

LOCAL_PREFIXES = ("vendor/", "js/", "css/", "icons/", "static/")


def _index_lines() -> list[str]:
    return INDEX_HTML.read_text(encoding="utf-8").splitlines()


def _strip_html_comments(html: str) -> str:
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


# ---------------------------------------------------------------------------
# AC-1 — every dynamic :href goes through safeHref()
# ---------------------------------------------------------------------------


def _dynamic_hrefs() -> list[tuple[int, str]]:
    """Return (line number, bound expression) for every :href="..." binding."""
    return [
        (ln, expr)
        for ln, sink, expr in _dynamic_url_sinks()
        if sink == ":href"
    ]


#: 除 :href 外的其它 URL sink —— 同样是"浏览器会拿着这个值去导航/加载"
#: 的位置, 同样能被 javascript: 打断。
URL_SINKS = (":href", ":src", ":srcset", ":action", ":formaction")

#: HTML 注入 sink —— 不是 URL, 但同样是"外部数据 → 浏览器解析"的通道。
#: 仓库现状只有两类常量渲染 helper (图标 / 统计图), 任何新的 x-html
#: 表达式都必须先进白名单才能通过。
HTML_SINKS = ("x-html",)

#: 少数 sink 走的是固定映射表 (名字 → 本地静态图标路径) 或常量渲染
#: helper, 值完全由代码里的白名单决定, 不是外部数据, 因此允许不经
#: safeHref()。
#:
#: ⚠️ 必须是**整表达式全匹配**, 不是"包含" —— 审计第三轮实测
#: ``:src="getSourceIcon(s.name) + row.evil_url"`` 在子串语义下会
#: 整条豁免, 而 Alpine 求值后就是一个可加载的 javascript: URL。
#: ``readerRenderMarkdownPlain`` 是 v1.0.102 加的：摘要 / 核查结论用
#: 同一套 marked + DOMPurify 管线，只是不挂正文引用 chip（那些「」不是
#: 文章里的引文）。仍然过 sanitize，所以进白名单。
ALLOWED_UNGUARDED_SINK_CALLS = (
    "getSourceIcon",
    "pinIcon",
    "methodPieSvg",
    "readerArticleHtml",
    "readerRenderMarkdown",
    "readerRenderMarkdownPlain",
)

#: 属性绑定扫描器 —— 同时吃双引号 / 单引号 / 跨行值。
#:
#: 审计第三轮实测的三个漏网形状:
#:   :src='row.evil_url'          (单引号)
#:   x-bind:href='row.evil_url'   (单引号)
#:   :src="\n  row.evil_url\n  "  (跨行属性值)
#: 逐行 + 只认双引号的正则这三种全部 GREEN, 属于"测试在骗自己"。
_SINK_ATTR_RE = re.compile(
    r"(?P<sink>:href|:src|:srcset|:action|:formaction|x-html)"
    r"\s*=\s*"
    r'(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\')',
    re.DOTALL,
)


def _dynamic_url_sinks(html: str | None = None) -> list[tuple[int, str, str]]:
    """Return (line number, sink name, bound expression) for every URL sink.

    扫整份文档 (不是逐行), 单双引号通吃, 属性值允许跨行。
    """
    if html is None:
        html = _strip_html_comments(INDEX_HTML.read_text(encoding="utf-8"))
    out: list[tuple[int, str, str]] = []
    for match in _SINK_ATTR_RE.finditer(html):
        expr = match.group("dq")
        if expr is None:
            expr = match.group("sq") or ""
        line = html[: match.start()].count("\n") + 1
        out.append((line, match.group("sink"), expr))
    return out


def _is_allowlisted_expression(expr: str) -> bool:
    """整表达式全匹配白名单 helper —— 不是子串包含。"""
    expr = expr.strip()
    return any(
        re.fullmatch(re.escape(name) + r"\([^()]*\)", expr)
        for name in ALLOWED_UNGUARDED_SINK_CALLS
    )


def test_all_dynamic_hrefs_are_guarded_by_safe_href():
    """No bare :href may survive — every one must call safeHref()."""
    hrefs = _dynamic_hrefs()
    assert hrefs, "no dynamic :href found — the scan itself is broken"

    unguarded = [(ln, expr) for ln, expr in hrefs if "safeHref(" not in expr]
    assert not unguarded, (
        "these dynamic :href bindings bypass safeHref(): %r"
        % ([f"moni/web/static/index.html:{ln} :href={expr!r}" for ln, expr in unguarded],)
    )


def test_guarded_href_count_is_at_least_the_known_sinks():
    """Regression floor: v1.0.47 audited 11 sinks — a drop means a refactor
    removed navigation, not that the problem went away."""
    assert len(_dynamic_hrefs()) >= 11


def test_every_dynamic_url_sink_is_guarded_or_allowlisted():
    """不只是 :href —— :src / :srcset / :action / :formaction 同样是 URL sink。

    审计第二轮发现: 原测试只扫 :href, 一旦有人新增一个
    ``:src="article.evil_url"`` 这类绑定, 测试依然全绿 —— 覆盖面比
    宣称的小, 属于"测试在骗自己"。这里把扫描范围扩到全部 URL sink,
    允许豁免的只有代码内的固定映射表。

    审计第三轮再收紧: 豁免从**子串包含**改为**整表达式全匹配** ——
    ``:src="getSourceIcon(s.name) + row.evil_url"`` 在子串语义下会被
    整条放行, 而 Alpine 求值后就是可加载的 javascript: URL。
    """
    sinks = _dynamic_url_sinks()
    assert sinks, "no dynamic URL sink found — the scan itself is broken"

    unguarded = [
        (ln, sink, expr)
        for ln, sink, expr in sinks
        if sink not in HTML_SINKS
        if "safeHref(" not in expr
        and not _is_allowlisted_expression(expr)
    ]
    assert not unguarded, (
        "these dynamic URL sinks bypass safeHref(): %r"
        % (
            [
                f"moni/web/static/index.html:{ln} {sink}={expr!r}"
                for ln, sink, expr in unguarded
            ],
        )
    )


def test_every_html_sink_is_allowlisted():
    """``x-html`` 是 HTML 注入通道 —— 不是 URL, 但同样吃外部数据。

    仓库现状只有 5 处, 全是常量渲染 helper (``pinIcon(...)`` /
    ``methodPieSvg()``)。新增的 x-html 表达式必须先进白名单。
    """
    sinks = [s for s in _dynamic_url_sinks() if s[1] in HTML_SINKS]
    assert sinks, "no x-html binding found — the scan itself is broken"

    offenders = [
        (ln, expr) for ln, _sink, expr in sinks if not _is_allowlisted_expression(expr)
    ]
    assert not offenders, (
        "these x-html bindings are not allowlisted constant helpers: %r"
        % ([f"moni/web/static/index.html:{ln} x-html={expr!r}" for ln, expr in offenders],)
    )


def test_sink_scanner_covers_single_quotes_multiline_and_xhtml():
    """扫描器自测 —— 审计第三轮的三个漏网形状必须被抓住。

    这条不依赖仓库内容, 纯粹钉住扫描器本身: 换了实现不小心退回
    "逐行 + 只认双引号", 这条会立刻红。
    """
    probe = (
        '<a :href="safeHref(a.url)"></a>\n'
        "<img :src='row.evil_url'>\n"
        "<img x-bind:href='row.evil_url'>\n"
        '<img :src="\n  row.evil_url\n  ">\n'
        '<span x-html="row.evil"></span>\n'
    )
    found = {(sink, expr.strip()) for _ln, sink, expr in _dynamic_url_sinks(probe)}
    assert (":src", "row.evil_url") in found
    assert (":href", "row.evil_url") in found
    assert (":src", "row.evil_url") in found
    assert ("x-html", "row.evil") in found

    # 子串豁免必须无效: 只有整表达式全匹配白名单 helper 才放行
    assert _is_allowlisted_expression("getSourceIcon(s.name)") is True
    assert _is_allowlisted_expression("getSourceIcon(s.name) + row.evil_url") is False
    assert _is_allowlisted_expression("row.evil_url") is False


def test_no_dynamic_window_open_or_location_navigation():
    """静态 JS 里不允许出现未受控的跳转出口。

    审计第三轮盘点: ``window.open`` / ``location.href =`` /
    ``location.assign`` / ``location.replace`` 在 ``moni/web/static/js/``
    下当前**一处都没有**。这是回归地板 —— 以后有人加, 必须同时加
    URL 白名单, 否则测试先红。
    """
    js_dir = STATIC / "js"
    patterns = (
        r"window\.open\s*\(",
        r"location\.href\s*=",
        r"location\.(?:assign|replace)\s*\(",
    )
    offenders: list[str] = []
    for js in sorted(js_dir.glob("*.js")):
        text = js.read_text(encoding="utf-8")
        for pat in patterns:
            for match in re.finditer(pat, text):
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{js.name}:{line} {pat}")
    assert not offenders, offenders


def test_every_target_blank_anchor_has_noopener_noreferrer():
    """``target="_blank"`` 不带 rel="noopener noreferrer" ⇒ 新开的页面通过
    ``window.opener`` 反向操纵本页 (tabnabbing)。审计第二轮实测有 1 处
    漏写 (队列产物链接), 这里锁死, 防止以后又漏。

    审计第三轮再收紧: 判定从"标签里有没有 rel=" 改成"rel 的 token 集合
    里有没有 noopener **且** noreferrer" —— 原来的子串判定会让
    ``rel="nofollow"`` 绿灯, 而那等于 tabnabbing 静默回归。
    """
    html = _strip_html_comments(INDEX_HTML.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for match in re.finditer(r'<a\b[^>]*>', html, re.S):
        tag = match.group(0)
        target = re.search(r'target\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', tag)
        if not target:
            continue
        if (target.group(1) or target.group(2) or "").strip().lower() != "_blank":
            continue
        rel = re.search(r'rel\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', tag)
        tokens = set()
        if rel:
            tokens = set((rel.group(1) or rel.group(2) or "").lower().split())
        if not {"noopener", "noreferrer"} <= tokens:
            line = html[: match.start()].count("\n") + 1
            offenders.append(
                f"moni/web/static/index.html:{line} <a target=_blank> rel={sorted(tokens)}"
            )
    assert not offenders, offenders


def test_blocked_urls_get_a_visible_marker():
    """A blocked URL must not just vanish — it needs the 拦截 badge class."""
    html = _strip_html_comments(INDEX_HTML.read_text(encoding="utf-8"))
    assert "isBlockedUrl(" in html
    for cls in ("link-blocked", "link-btn-blocked", "btn-blocked"):
        assert cls in html, f"no binding uses the {cls} state"


# ---------------------------------------------------------------------------
# AC-2 — zero external script/style dependencies
# ---------------------------------------------------------------------------


def test_index_html_has_no_external_hosts():
    html = _strip_html_comments(INDEX_HTML.read_text(encoding="utf-8"))
    for host in EXTERNAL_HOSTS:
        assert host not in html, f"index.html still references {host}"


def test_every_script_and_preload_is_local():
    html = _strip_html_comments(INDEX_HTML.read_text(encoding="utf-8"))
    urls = re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)
    urls += re.findall(r'<link[^>]*\brel="preload"[^>]*\bhref="([^"]+)"', html)
    assert urls, "no script/preload urls found — the scan itself is broken"
    for url in urls:
        assert not url.startswith(("http://", "https://", "//")), (
            f"external script dependency survived: {url}"
        )
        assert url.startswith(LOCAL_PREFIXES), (
            f"script/preload url is not a local relative path: {url}"
        )


def test_alpine_and_gsap_are_vendored_with_pinned_versions():
    html = _strip_html_comments(INDEX_HTML.read_text(encoding="utf-8"))
    assert f"vendor/{ALPINE_FILE}" in html
    assert f"vendor/{GSAP_FILE}" in html


def test_gsap_script_still_has_defer_and_precedes_consumers():
    """v1.0.44 的行号序契约不能因为换成本地路径而破掉."""
    lines = _index_lines()
    script_lines = [
        (i, ln)
        for i, ln in enumerate(lines, 1)
        if re.search(r"<script\b[^>]*\bsrc\s*=", ln)
    ]
    gsap = [(i, ln) for i, ln in script_lines if "gsap" in ln]
    assert len(gsap) == 1, f"expected exactly 1 gsap script tag, got {len(gsap)}"
    gsap_line, gsap_tag = gsap[0]
    assert "defer" in gsap_tag
    assert not re.search(r"\basync\b", gsap_tag)
    for i, tag in script_lines:
        if "gsap" in tag:
            continue
        m = re.search(r'src="([^"]+)"', tag)
        if m and re.search(r"(app|pin_anim)\.js", m.group(1)):
            assert gsap_line < i, (
                f"gsap script (line {gsap_line}) must precede {m.group(1)} (line {i})"
            )


def test_safe_url_js_is_loaded_before_alpine_initialises():
    """safe_url.js 必须是普通 script —— Alpine 是 defer, 普通 script 先跑.

    文档序不重要 (safe_url.js 放在 Alpine 标签之后也没问题): 普通
    ``<script src>`` 在解析期同步执行, 而 ``defer`` 脚本要等整个文档
    解析完才跑。真正的保证是 —— safe_url.js 不能带 defer / async。
    """
    lines = _index_lines()
    safe_lines = [
        (i, ln)
        for i, ln in enumerate(lines, 1)
        if "js/safe_url.js" in ln and "<script" in ln
    ]
    assert safe_lines, "safe_url.js is never loaded"
    safe_line, safe_tag = safe_lines[0]
    assert "defer" not in safe_tag, (
        "safe_url.js must NOT be deferred — it has to run before Alpine evaluates"
    )
    assert not re.search(r"\basync\b", safe_tag), (
        "safe_url.js must not be async — execution order would be undefined"
    )

    alpine_lines = [
        i
        for i, ln in enumerate(lines, 1)
        if "<script" in ln and "alpine" in ln and "defer" in ln
    ]
    assert alpine_lines, "alpine script tag not found"


# ---------------------------------------------------------------------------
# AC-3 — vendor assets: presence, integrity, licence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", sorted(EXPECTED_SHA256))
def test_vendor_asset_hash_is_pinned(filename):
    path = VENDOR_DIR / filename
    assert path.is_file(), f"vendor asset missing: {path}"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == EXPECTED_SHA256[filename], (
        f"{filename} drifted — vendor files must be byte-identical to the "
        f"reviewed upstream build (expected {EXPECTED_SHA256[filename]}, got {digest})"
    )


def test_vendor_licences_are_present():
    for name in ("LICENSE-ALPINE.txt", "LICENSE-GSAP.txt", "README.md"):
        path = VENDOR_DIR / name
        assert path.is_file(), f"missing {path}"
        assert len(path.read_text(encoding="utf-8").strip()) > 50, f"{path} is empty"

    alpine_lic = (VENDOR_DIR / "LICENSE-ALPINE.txt").read_text(encoding="utf-8")
    assert "MIT License" in alpine_lic
    assert "Caleb Porzio" in alpine_lic

    gsap_lic = (VENDOR_DIR / "LICENSE-GSAP.txt").read_text(encoding="utf-8")
    assert "GreenSock" in gsap_lic
    assert "gsap.com/standard-license" in gsap_lic


def test_vendor_readme_records_source_and_hash():
    readme = (VENDOR_DIR / "README.md").read_text(encoding="utf-8")
    for filename, digest in EXPECTED_SHA256.items():
        assert filename in readme
        assert digest in readme, f"{filename} sha256 not recorded in vendor/README.md"


# ---------------------------------------------------------------------------
# AC-4 — safe_url.js behaviour (node matrix)
# ---------------------------------------------------------------------------


MATRIX = [
    # (input, expected safe?)
    ("https://36kr.com/p/1", True),
    ("http://news.163.com/item", True),
    ("/artifacts/brief-2026.html", True),
    ("/fetch", True),
    ("mailto:contact@test.com", True),
    ("  https://qbitai.com/feed  ", True),
    ("https://example.com/path?q=1#frag", True),
    # 危险协议
    ("javascript:alert(1)", False),
    ("JAVASCRIPT:alert(document.cookie)", False),
    ("  javascript:void(0)  ", False),
    ("vbscript:msgbox(1)", False),
    ("data:text/html;base64,PHNjcmlwdD4=", False),
    ("file:///etc/passwd", False),
    ("blob:http://127.0.0.1:8900/uuid", False),
    ("about:blank", False),
    # 相对路径逃逸 / 跳板
    ("//evil.com/phish", False),
    ("/\\evil.com", False),
    ("\\evil.com", False),
    # 边界
    ("", False),
    ("   ", False),
    (None, False),
]


def test_safe_url_logic_matrix_via_node():
    """真的用 node 求值一遍 —— 正则断言证明不了 new URL() 的行为."""
    node = subprocess.run(
        ["node", "--version"], capture_output=True, text=True, check=False
    )
    if node.returncode != 0:
        pytest.skip("node not available on this machine")

    payload = {
        "module": str(SAFE_URL_JS),
        "matrix": [[inp, ok] for inp, ok in MATRIX],
        "origin": "http://127.0.0.1:8900",
    }
    script = """
const payload = JSON.parse(process.argv[1]);
const api = require(payload.module);
const origin = payload.origin;
const failures = [];
for (const [input, expected] of payload.matrix) {
  let got;
  try {
    got = api.isSafeUrl(input, origin);
  } catch (err) {
    failures.push([String(input), 'threw: ' + err.message]);
    continue;
  }
  if (got !== expected) {
    failures.push([String(input), 'expected ' + expected + ' got ' + got]);
  }
  const href = api.safeHref(input, origin);
  if (expected && href === null) failures.push([String(input), 'safeHref returned null']);
  if (!expected && href !== null) failures.push([String(input), 'safeHref returned ' + href]);
}
if (api.isBlockedUrl('', origin) !== false) failures.push(['', 'isBlockedUrl should be false']);
if (api.isBlockedUrl(null, origin) !== false) failures.push(['null', 'isBlockedUrl should be false']);
if (api.isBlockedUrl('javascript:alert(1)', origin) !== true) failures.push(['javascript:', 'isBlockedUrl should be true']);
console.log(JSON.stringify(failures));
process.exit(failures.length ? 1 : 0);
"""
    proc = subprocess.run(
        ["node", "-e", script, json.dumps(payload)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        "safe_url.js matrix failed:\n%s\n%s" % (proc.stdout, proc.stderr)
    )


def test_safe_url_js_exposes_the_globals_alpine_needs():
    src = SAFE_URL_JS.read_text(encoding="utf-8")
    for name in ("isSafeUrl", "safeHref", "isBlockedUrl", "SAFE_URL_BLOCKED_MESSAGE"):
        assert f"global.{name}" in src, f"{name} is not exposed as a global"


def test_safe_href_uses_the_browser_origin_by_default():
    """浏览器路径必须真被测到 —— ``defaultOrigin()`` 不能被绕开。

    审计第三轮实测: 之前的 ``file://`` 矩阵把 origin 当**参数**直接喂
    进 ``isSafeUrl(input, origin)``, 整条 ``defaultOrigin()`` 路径零覆盖。
    于是把 ``defaultOrigin()`` 改成返回 ``'null'`` / ``''`` / 恒定值,
    测试全部 GREEN —— 而真实后果是 ``new URL(x, 'null')`` 直接 throw,
    ``isSafeUrl`` 对**一切** URL 返回 false: 离线页的外链全哑, 站内
    相对路径全被拦。

    这条在 node 里 stub 一个 ``window.location.origin``, **不带 origin
    参数**调 ``safeHref()``, 强制走 ``defaultOrigin()`` 分支。
    """
    node = subprocess.run(
        ["node", "--version"], capture_output=True, text=True, check=False
    )
    if node.returncode != 0:
        pytest.skip("node not available on this machine")

    payload = {
        "module": str(SAFE_URL_JS),
        "origin": "file://",
        "cases": [
            # file:// 下外链照常放行
            ["https://36kr.com/p/1", "https://36kr.com/p/1"],
            ["mailto:contact@test.com", "mailto:contact@test.com"],
            # file:// 下站内相对路径被剥离 ⇒ 拦截
            ["/artifacts/brief.html", None],
            ["index.html", None],
            # 危险协议一律拦截
            ["javascript:alert(1)", None],
            ["data:text/html;base64,PHNjcmlwdD4=", None],
        ],
    }
    script = """
const payload = JSON.parse(process.argv[1]);
// stub 浏览器环境: defaultOrigin() 必须读得到这个 origin
global.window = { location: { origin: payload.origin } };
const api = require(payload.module);
const failures = [];
for (const [input, expected] of payload.cases) {
  let got;
  try {
    got = api.safeHref(input);          // 不传 origin —— 走 defaultOrigin()
  } catch (err) {
    failures.push([String(input), 'threw: ' + err.message]);
    continue;
  }
  if (got !== expected) {
    failures.push([String(input), 'expected ' + JSON.stringify(expected) + ' got ' + JSON.stringify(got)]);
  }
}
console.log(JSON.stringify(failures));
process.exit(failures.length ? 1 : 0);
"""
    proc = subprocess.run(
        ["node", "-e", script, json.dumps(payload)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        "safe_url.js defaultOrigin() path failed:\n%s\n%s" % (proc.stdout, proc.stderr)
    )


def test_safe_url_behaviour_under_file_origin():
    """把 ``file://`` 下的行为钉死 (审计第二轮实测确认)。

    Chrome / WebKit 打开本地文件时 ``location.origin`` 是 ``"file://"``;
    Gecko 的规范值是 ``"null"`` (由下一条测试单独覆盖)。``file://`` 下:

    - 外链 ``https://...`` 照常放行 (离线分享页要能点外链)
    - **站内相对路径被剥离** —— ``file://`` 下 ``new URL('/x', 'file://')``
      的 protocol 是 ``file:``, 不在白名单 ⇒ 相对链接在离线页里点不开
    - ``javascript:`` / ``data:`` 一律拦截

    这条限制是**已知取舍**, 不是 bug: 离线页本来就拿不到站内 API。
    """
    node = subprocess.run(
        ["node", "--version"], capture_output=True, text=True, check=False
    )
    if node.returncode != 0:
        pytest.skip("node not available on this machine")

    file_matrix = [
        ["https://36kr.com/p/1", True],
        ["mailto:contact@test.com", True],
        ["javascript:alert(1)", False],
        ["data:text/html;base64,PHNjcmlwdD4=", False],
        ["/artifacts/brief.html", False],
        ["index.html", False],
    ]
    payload = {
        "module": str(SAFE_URL_JS),
        "matrix": file_matrix,
        "origin": "file://",
    }
    script = """
const payload = JSON.parse(process.argv[1]);
const api = require(payload.module);
const failures = [];
for (const [input, expected] of payload.matrix) {
  const got = api.isSafeUrl(input, payload.origin);
  if (got !== expected) {
    failures.push([String(input), 'expected ' + expected + ' got ' + got]);
  }
}
console.log(JSON.stringify(failures));
process.exit(failures.length ? 1 : 0);
"""
    proc = subprocess.run(
        ["node", "-e", script, json.dumps(payload)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        "safe_url.js file:// matrix failed:\n%s\n%s" % (proc.stdout, proc.stderr)
    )


def test_safe_url_behaviour_under_opaque_null_origin():
    """Gecko reports ``origin === "null"`` for file:// pages.

    ``new URL(x, "null")`` throws for every input, so a naive
    ``new URL(trimmed, origin)`` would block ordinary external links as well.
    The file mode must parse absolute URLs without a base: https/http/mailto
    stay usable, while relative paths cannot be resolved and are rejected.
    """
    node = subprocess.run(
        ["node", "--version"], capture_output=True, text=True, check=False
    )
    if node.returncode != 0:
        pytest.skip("node not available on this machine")

    payload = {
        "module": str(SAFE_URL_JS),
        "origin": "null",
        "matrix": [
            ["https://36kr.com/p/1", True],
            ["http://news.163.com/item", True],
            ["mailto:contact@test.com", True],
            ["/artifacts/brief.html", False],
            ["index.html", False],
            ["//evil.com/phish", False],
            ["javascript:alert(1)", False],
            ["data:text/html;base64,PHNjcmlwdD4=", False],
            ["file:///etc/passwd", False],
        ],
    }
    script = """
const payload = JSON.parse(process.argv[1]);
const api = require(payload.module);
const failures = [];
for (const [input, expected] of payload.matrix) {
  const got = api.isSafeUrl(input, payload.origin);
  if (got !== expected) {
    failures.push([String(input), 'expected ' + expected + ' got ' + got]);
  }
}
console.log(JSON.stringify(failures));
process.exit(failures.length ? 1 : 0);
"""
    proc = subprocess.run(
        ["node", "-e", script, json.dumps(payload)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        'safe_url.js origin="null" matrix failed:\n%s\n%s'
        % (proc.stdout, proc.stderr)
    )


def test_default_origin_accepts_literal_null_from_location():
    """The browser path must not let a literal null fall through to localhost."""
    node = subprocess.run(
        ["node", "--version"], capture_output=True, text=True, check=False
    )
    if node.returncode != 0:
        pytest.skip("node not available on this machine")

    payload = {
        "module": str(SAFE_URL_JS),
        "cases": [
            ["https://36kr.com/p/1", "https://36kr.com/p/1"],
            ["/artifacts/brief.html", None],
            ["javascript:alert(1)", None],
        ],
    }
    script = """
const payload = JSON.parse(process.argv[1]);
global.window = { location: { origin: null } };
const api = require(payload.module);
const failures = [];
for (const [input, expected] of payload.cases) {
  const got = api.safeHref(input);
  if (got !== expected) {
    failures.push([String(input), 'expected ' + JSON.stringify(expected) + ' got ' + JSON.stringify(got)]);
  }
}
console.log(JSON.stringify(failures));
process.exit(failures.length ? 1 : 0);
"""
    proc = subprocess.run(
        ["node", "-e", script, json.dumps(payload)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        "safe_url.js literal null origin path failed:\n%s\n%s"
        % (proc.stdout, proc.stderr)
    )


# ---------------------------------------------------------------------------
# AC-5 — blocked-state styling uses existing tokens only
# ---------------------------------------------------------------------------


def test_blocked_state_css_uses_existing_tokens():
    css = MAIN_CSS.read_text(encoding="utf-8")
    for cls in (".link-blocked", ".link-btn-blocked", ".btn-blocked"):
        assert cls in css, f"no CSS rule for {cls}"
    assert "var(--muted-fg)" in css
    assert "var(--destructive)" in css
    assert "已拦截" in css


def test_blocked_state_has_no_animation_without_reduce_motion():
    """新 CSS 不能引入未受 prefers-reduced-motion 约束的动画."""
    css = MAIN_CSS.read_text(encoding="utf-8")
    start = css.index(".link-blocked::after")
    tail = css[start : start + 1200]
    block = tail.split("}")[0]
    assert "animation" not in block
    assert "transition" not in block


# ---------------------------------------------------------------------------
# AC-6 — the single-file showcase builder stays in sync
# ---------------------------------------------------------------------------


def test_showcase_builder_has_no_cdn_dependency():
    src = SHOWCASE_BUILDER.read_text(encoding="utf-8")
    # ignore comments — they explain the history of the CDN URLs
    code = "\n".join(
        ln for ln in src.splitlines() if not ln.strip().startswith("#")
    )
    assert "cdn.jsdelivr.net" not in code, (
        "showcase builder still downloads from the CDN — it must read the "
        "self-hosted vendor/ files"
    )
    assert "vendor" in code


def test_showcase_builder_inlines_safe_url_js():
    src = SHOWCASE_BUILDER.read_text(encoding="utf-8")
    assert '"safe_url.js"' in src, (
        "safe_url.js is not inlined into the single-file showcase — the demo "
        "page would lose the href guard"
    )


def test_showcase_builder_injects_demo_origin_before_safe_url_js():
    """file:// has no site origin; without this override every /artifacts/
    link becomes link-blocked and pointer-events:none, so the demo click is dead.
    """
    src = SHOWCASE_BUILDER.read_text(encoding="utf-8")
    assert "SHOWCASE_SAFE_ORIGIN" in src
    assert "window.__MONI_SAFE_URL_BASE_ORIGIN__" in src
    assert 'if name == "safe_url.js"' in src
    assert "text = inject_showcase_safe_origin(text)" in src


def test_showcase_demo_origin_keeps_artifacts_safe_but_blocks_javascript():
    """Evaluate the builder's injected override through the real safe_url.js.

    This exercises the generated source, not only the origin parameter:
    ``inject_showcase_safe_origin()`` must define the demo origin before
    safe_url.js reads it, even when the actual page origin is file://.
    """
    node = subprocess.run(
        ["node", "--version"], capture_output=True, text=True, check=False
    )
    if node.returncode != 0:
        pytest.skip("node not available on this machine")

    spec = importlib.util.spec_from_file_location(
        "moni_build_showcase_for_safe_url_test", SHOWCASE_BUILDER
    )
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    injected = builder.inject_showcase_safe_origin(
        SAFE_URL_JS.read_text(encoding="utf-8")
    )
    assert injected.index("window.__MONI_SAFE_URL_BASE_ORIGIN__") < injected.index(
        "function defaultOrigin()"
    )

    payload = {
        "source": injected,
        "origin": "http://showcase.moni.local",
        "matrix": [
            ["/artifacts/brief.html", True],
            ["https://36kr.com/p/1", True],
            ["javascript:alert(1)", False],
            ["data:text/html;base64,PHNjcmlwdD4=", False],
        ],
    }
    script = """
const payload = JSON.parse(process.argv[1]);
global.window = {
  location: { origin: 'file://' },
};
const module_ = { exports: {} };
new Function('module', 'exports', payload.source)(module_, module_.exports);
const api = module_.exports;
const failures = [];
for (const [input, expected] of payload.matrix) {
  const got = api.isSafeUrl(input);
  if (got !== expected) {
    failures.push([String(input), 'expected ' + expected + ' got ' + got]);
  }
}
console.log(JSON.stringify(failures));
process.exit(failures.length ? 1 : 0);
"""
    proc = subprocess.run(
        ["node", "-e", script, json.dumps(payload)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        "showcase demo origin override failed:\n%s\n%s"
        % (proc.stdout, proc.stderr)
    )
