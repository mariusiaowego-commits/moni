"""tests/test_frontend_safe_href_and_vendor.py — v1.0.47 前端安全契约断言.

锁住两件事:

1. **URL 白名单** — index.html 里每一个动态 ``:href`` 绑定都必须经过
   ``safeHref()``。这些 URL 全部来自不可信数据源 (RSS 正文 / LLM 联网
   核查返回的证据 URL / 用户配置的信源地址)，在 127.0.0.1:8900 同源下
   一旦放行 ``javascript:``，点击即以站点权限执行脚本。
2. **零外网依赖** — Alpine / GSAP 必须自托管。内网、离线、DNS 被污染
   时 CDN 拉不到，Alpine 起不来，``x-cloak`` 让整页持续空白。

判定口径:
- AC-1 用**全量扫描**: 抓到任何一个没走 safeHref 的 :href 就红,
  并报出 file:line —— 不是"抽查 11 处"。
- AC-2 区分外链与本地相对路径: 只放行 vendor/ js/ css/ icons/ 前缀。
- AC-4 用 **node 子进程**跑矩阵: safe_url.js 是纯函数, 真的求值一遍
  比正则断言可靠 (方案来自 docs/plans/2026-10-23-frontend-safe-href-and-
  offline-assets.md §C 契约 4)。
"""

from __future__ import annotations

import hashlib
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
    out: list[tuple[int, str]] = []
    for i, line in enumerate(_index_lines(), 1):
        for expr in re.findall(r':href="([^"]*)"', line):
            out.append((i, expr))
    return out


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
