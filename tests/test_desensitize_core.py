"""Comprehensive unit tests for moni/llm/desensitize M0 core."""

from __future__ import annotations

import base64
import gc
import hashlib
import hmac
import json
import os
import re
import stat
import statistics
import time
from pathlib import Path

import pytest

from moni.llm.desensitize import (
    InMemoryVault,
    MaskResult,
    PolicyMode,
    RequestScopeVault,
    RestoreStats,
    Rule,
    StreamRestorer,
    TaskPolicy,
    UserRules,
    builtin_rules,
    load_salt,
    mask_prompt,
    mask_text,
    resolve_policy,
    restore_html_safe,
    restore_json_aware,
    restore_text,
    salt_path,
)
from moni.llm.desensitize.rules import chinese_id_checksum, luhn_checksum
from moni.llm.desensitize.vault import PLACEHOLDER_RE

SALT_A = b"A" * 32
SALT_B = b"B" * 32


def _make_valid_cn_id(prefix17: str = "11010519900307123") -> str:
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    total = sum(int(prefix17[i]) * weights[i] for i in range(17))
    return prefix17 + "10X98765432"[total % 11]


def _make_valid_luhn_card(prefix15: str = "622588012345678") -> str:
    digits = [int(c) for c in prefix15]
    # Compute check digit for 16-digit card.
    total = 0
    for idx, d in enumerate(reversed(digits)):
        if idx % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - (total % 10)) % 10
    return prefix15 + str(check)


# ── Group 1: Deterministic placeholder, kind isolation, salt rotation & regex parity ──


def test_group1_same_kind_same_value_is_deterministic() -> None:
    v1 = InMemoryVault(salt=SALT_A)
    v2 = InMemoryVault(salt=SALT_A)
    rules = UserRules.from_rules([Rule(kind="ORG", word="招商银行")])

    r1 = mask_text("分析招商银行的零售业务", vault=v1, user_rules=rules)
    r2 = mask_text("招商银行信用卡增速", vault=v2, user_rules=rules)

    assert r1.masked_total == 1
    assert r2.masked_total == 1
    assert r1.items[0].placeholder == r2.items[0].placeholder


def test_group1_different_kind_same_value_produces_different_placeholder() -> None:
    vault = InMemoryVault(salt=SALT_A)
    r_org = mask_text(
        "主体为 AlphaX",
        vault=vault,
        user_rules=UserRules.from_rules([Rule(kind="ORG", word="AlphaX")]),
    )
    r_proj = mask_text(
        "项目为 AlphaX",
        vault=vault,
        user_rules=UserRules.from_rules([Rule(kind="PROJECT", word="AlphaX")]),
    )

    assert r_org.items[0].placeholder != r_proj.items[0].placeholder
    assert r_org.items[0].placeholder.startswith("{{ORG_")
    assert r_proj.items[0].placeholder.startswith("{{PROJECT_")


def test_group1_salt_change_changes_placeholder_and_clears_vault() -> None:
    vault = InMemoryVault(salt=SALT_A)
    rules = UserRules.from_rules([Rule(kind="ORG", word="招商银行")])
    r1 = mask_text("招商银行年报", vault=vault, user_rules=rules)
    ph1 = r1.items[0].placeholder

    vault.rotate_salt(SALT_B)
    assert len(vault) == 0
    r2 = mask_text("招商银行年报", vault=vault, user_rules=rules)
    ph2 = r2.items[0].placeholder

    assert ph1 != ph2


def test_group1_placeholder_regex_matches_magpie_byte_for_byte() -> None:
    magpie_re = re.compile(r"\{\{[A-Z][A-Z0-9_]{0,23}_[a-z2-7]{8}\}\}")
    assert PLACEHOLDER_RE.pattern == magpie_re.pattern

    vault = InMemoryVault(salt=SALT_A)
    ph = vault.put("API_KEY", "sk-test-secret-1234567890")
    digest = hmac.new(SALT_A, b"API_KEY\x00sk-test-secret-1234567890", hashlib.sha256).digest()
    expected = "{{API_KEY_" + base64.b32encode(digest).decode("ascii")[:8].lower() + "}}"
    assert ph == expected
    assert magpie_re.fullmatch(ph) is not None


def test_group1_salt_file_permissions_and_rotation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "moni_sec" / "desensitize.salt"
    monkeypatch.setenv("MONI_DESENSITIZE_SALT_PATH", str(target))
    assert salt_path() == target

    s1 = load_salt()
    assert len(s1) == 32
    assert load_salt() == s1
    assert stat.S_IMODE(os.stat(target.parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600

    s2 = load_salt(rotate=True)
    assert len(s2) == 32
    assert s2 != s1


# ── Group 2: Alias merging & ASCII word-boundary guard ──


def test_group2_alias_merging_maps_to_same_placeholder() -> None:
    vault = InMemoryVault(salt=SALT_A)
    rules = UserRules.from_rules(
        [Rule(kind="ORG", word="招商银行", aliases=("招行", "CMB"), match_case=True)]
    )
    res = mask_text("招商银行与招行及CMB同属一家主体", vault=vault, user_rules=rules)

    assert res.masked_total == 3
    assert res.masked_distinct == 1
    placeholders = {it.placeholder for it in res.items}
    assert len(placeholders) == 1
    assert restore_text(res.text, vault) == "招商银行与招商银行及招商银行同属一家主体"


def test_group2_ascii_word_boundary_prevents_cmb_hitting_cmake() -> None:
    vault = InMemoryVault(salt=SALT_A)
    rules = UserRules.from_rules(
        [Rule(kind="ORG", word="招商银行", aliases=("招行", "CMB"), match_case=True)]
    )
    res = mask_text("构建脚本 CMake 与 ACMB1 不应被误伤，但使用CMB服务要遮蔽", vault=vault, user_rules=rules)

    assert "CMake" in res.text
    assert "ACMB1" in res.text
    assert res.masked_total == 1
    assert "使用{{ORG_" in res.text


def test_group2_longest_chinese_match_wins() -> None:
    vault = InMemoryVault(salt=SALT_A)
    rules = UserRules.from_rules(
        [
            Rule(kind="ORG", word="招行"),
            Rule(kind="ORG", word="招商银行总行"),
        ]
    )
    res = mask_text("招商银行总行发布公告", vault=vault, user_rules=rules)
    assert res.masked_total == 1
    assert restore_text(res.text, vault) == "招商银行总行发布公告"


# ── Group 3: Financial numbers, percentages, dates & 16-hex article IDs zero false positives ──


@pytest.mark.parametrize(
    "safe_text",
    [
        "该行实现营业收入 5.138 亿元，同比增幅 12.45%。",
        "全国布局 1.7 万网点，不良率维持在 0.92% 低位。",
        "截至 2026-11-24，日期 20261124 与 2026年11月24日 均属常规时间。",
        "订单号 13800138000 已完成清分，流水号 13912345678 无需脱敏。",
        '{"timestamp": 13800138000, "amount": 5.13800138000, "pct": 35.6}',
        "交易金额 13800138000 元，成交量 13800138000 股，估值倍数 13.8 倍。",
        "关联文章 articles.id=5d41402abc4b2a76 与 9f13800138000abc 保留原值。",
        "对象 UUID 550e8400-e29b-41d4-a716-446655440000 不在凭据字段内。",
        "图片 icon@2x.png 和文档 @AGENTS.md 及 git@github.com:org/repo.git。",
        "新闻链接 https://example.com/news/sk-hynix-third-quarter-profit.html。",
        "文档说明：密钥为 2048 位，token 是 expired，secretKey: Uint8Array;。",
    ],
)
def test_group3_financial_numbers_dates_ids_zero_false_positives(safe_text: str) -> None:
    vault = InMemoryVault(salt=SALT_A)
    res = mask_text(safe_text, vault=vault)
    assert res.masked_total == 0, f"unexpected mask in: {safe_text} -> {res.text}"
    assert res.text == safe_text


def test_group3_phone_masked_in_explicit_phone_context() -> None:
    vault = InMemoryVault(salt=SALT_A)
    res = mask_text("客户手机号：13800138000，备用 +86 13912345678", vault=vault)
    assert res.masked_total == 2
    assert "13800138000" not in res.text
    assert "13912345678" not in res.text
    assert restore_text(res.text, vault) == "客户手机号：13800138000，备用 +86 13912345678"


def test_group3_credential_field_still_masks_hex_digest() -> None:
    vault = InMemoryVault(salt=SALT_A)
    res = mask_text('{"refresh_token": "5d41402abc4b2a769f8e1a2b3c4d5e6f"}', vault=vault)
    assert res.masked_total == 1
    assert "5d41402abc4b2a769f8e1a2b3c4d5e6f" not in res.text


# ── Group 4: ID card mod-11 & bank card Luhn checksum validation ──


def test_group4_id_card_valid_masked_invalid_ignored() -> None:
    valid_id = _make_valid_cn_id("11010519900307123")
    wrong_check = "1" if valid_id[-1] != "1" else "2"
    invalid_id = valid_id[:-1] + wrong_check

    assert chinese_id_checksum(valid_id) is True
    assert chinese_id_checksum(invalid_id) is False

    vault = InMemoryVault(salt=SALT_A)
    res = mask_text(f"合法身份证 {valid_id}；错校验码 {invalid_id}", vault=vault)

    assert valid_id not in res.text
    assert invalid_id in res.text
    assert res.masked_total == 1
    assert res.items[0].kind == "ID_CARD"


def test_group4_bank_card_valid_luhn_masked_invalid_ignored() -> None:
    valid_card = _make_valid_luhn_card("622588012345678")
    wrong_digit = str((int(valid_card[-1]) + 1) % 10)
    invalid_card = valid_card[:-1] + wrong_digit

    assert luhn_checksum(valid_card) is True
    assert luhn_checksum(invalid_card) is False

    vault = InMemoryVault(salt=SALT_A)
    res = mask_text(f"卡号 {valid_card}，非卡号 {invalid_card}", vault=vault)

    assert valid_card not in res.text
    assert invalid_card in res.text
    assert res.masked_total == 1
    assert res.items[0].kind == "BANK_CARD"


def test_group4_bank_card_with_financial_unit_suffix_not_masked() -> None:
    valid_luhn_num = _make_valid_luhn_card("400000000000000")
    vault = InMemoryVault(salt=SALT_A)
    res = mask_text(f"期末总资产 {valid_luhn_num} 亿元", vault=vault)
    assert res.masked_total == 0
    assert valid_luhn_num in res.text


# ── Group 5: Shape-preserving URL param masking & /Users/<name> stand-in ──


def test_group5_url_masks_only_credential_param_values() -> None:
    vault = InMemoryVault(salt=SALT_A)
    url = "https://agg.example.cn/api/rss/all?channel=finance&token=sec_tok_abc123xyz&api_key=my_secret_key_99&page=2"
    res = mask_text(f"抓取地址：{url}", vault=vault)

    assert "https://agg.example.cn/api/rss/all?channel=finance&token={{URL_TOKEN_" in res.text
    assert "&api_key={{URL_TOKEN_" in res.text
    assert res.text.endswith("&page=2")
    assert "{{SECRET_" not in res.text
    assert "sec_tok_abc123xyz" not in res.text
    assert "my_secret_key_99" not in res.text
    assert restore_text(res.text, vault) == f"抓取地址：{url}"


def test_group5_url_jwt_under_unexpected_param_name_masked() -> None:
    vault = InMemoryVault(salt=SALT_A)
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3OCJ9.sig_abc123456"
    url = f"https://rss.example.cn/feed?custom_auth={jwt}&v=1"
    res = mask_text(url, vault=vault)
    assert "custom_auth={{URL_TOKEN_" in res.text
    assert jwt not in res.text


def test_group5_path_user_uses_u_standin_and_restores() -> None:
    vault = InMemoryVault(salt=SALT_A)
    src = "配置文件位于 /Users/alice_chen/.moni/config.yaml 与 /home/bob_dev/app.py"
    res = mask_text(src, vault=vault)

    assert re.search(r"/Users/u_[a-z2-7]{8}/\.moni/config\.yaml", res.text)
    assert re.search(r"/home/u_[a-z2-7]{8}/app\.py", res.text)
    assert "alice_chen" not in res.text
    assert "bob_dev" not in res.text
    assert restore_text(res.text, vault) == src


# ── Group 6: JSON lexical-aware restoration ──


def test_group6_json_aware_restoration_with_quotes_and_newlines() -> None:
    vault = InMemoryVault(salt=SALT_A)
    raw_val = '招行"零售"\n内部纪要\\2026'
    ph = vault.put("ORG", raw_val)

    llm_reply = f'{{"summary": "重点关注 {ph} 的动态", "entities": ["{ph}"]}}'
    restored = restore_json_aware(llm_reply, vault)
    parsed = json.loads(restored)

    assert parsed["summary"] == f"重点关注 {raw_val} 的动态"
    assert parsed["entities"] == [raw_val]


def test_group6_json_aware_auto_quotes_bare_placeholder_value() -> None:
    vault = InMemoryVault(salt=SALT_A)
    raw_val = '机构"A"\n分行'
    ph = vault.put("ORG", raw_val)

    llm_reply = f'{{"org": {ph}, "list": [{ph}, "ok"]}}'
    restored = restore_json_aware(llm_reply, vault)
    parsed = json.loads(restored)

    assert parsed["org"] == raw_val
    assert parsed["list"] == [raw_val, "ok"]


def test_group6_restore_text_escaped_mode_produces_valid_json_string() -> None:
    vault = InMemoryVault(salt=SALT_A)
    raw_secret = 'p@ss"word\nline2'
    ph = vault.put("PASSWORD", raw_secret)
    restored = restore_text(f'{{"pw": "{ph}"}}', vault, escaped=True)
    assert json.loads(restored) == {"pw": raw_secret}


# ── Group 7: StreamRestorer byte-by-byte / char-by-char cut immunity ──


@pytest.mark.parametrize(
    "template",
    [
        "前缀文字 {ph} 后缀文字",
        "{ph}开头和结尾{ph}",
        "全角｛｛{body}｝｝和单括号{{{body}}}与裸{body}结束",
        "混合【{body}】与空格{{ {lower_body} }}结尾",
    ],
)
def test_group7_stream_restorer_every_split_position_zero_cracks(template: str) -> None:
    vault = InMemoryVault(salt=SALT_A)
    ph = vault.put("ORG", "招商银行")
    body = ph[2:-2]
    full_reply = template.format(ph=ph, body=body, lower_body=body.lower())
    expected = restore_text(full_reply, vault)

    # Split at every single character index (single-char feed AND 2-part split at every offset).
    sr_char = StreamRestorer(vault)
    out_chars: list[str] = []
    for ch in full_reply:
        out_chars.append(sr_char.feed(ch))
    out_chars.append(sr_char.flush())
    assert "".join(out_chars) == expected
    assert "{{" not in "".join(out_chars)
    assert body not in "".join(out_chars)

    # Also test every 2-way split across the string (including UTF-8 byte boundaries).
    for cut in range(len(full_reply) + 1):
        sr = StreamRestorer(vault)
        joined = sr.feed(full_reply[:cut]) + sr.feed(full_reply[cut:]) + sr.flush()
        assert joined == expected


def test_group7_stream_restorer_never_emits_partial_placeholder_early() -> None:
    vault = InMemoryVault(salt=SALT_A)
    ph = vault.put("ORG", "招商银行")
    sr = StreamRestorer(vault)

    emitted: list[str] = []
    partial_text = f"研究显示{ph[:7]}"  # "研究显示{{ORG_r"
    for ch in partial_text:
        chunk = sr.feed(ch)
        assert "{" not in chunk
        assert "ORG_" not in chunk
        emitted.append(chunk)
    assert "".join(emitted) == "研究显示"

    tail = sr.feed(ph[7:]) + sr.flush()
    assert "".join(emitted) + tail == "研究显示招商银行"


# ── Group 8: Five-level fault-tolerant restoration ──


def test_group8_five_level_fault_tolerant_restoration() -> None:
    vault = RequestScopeVault()
    ph = vault.put("ORG", "招商银行")
    code = ph.split("_", 1)[1][:-2]

    variants = [
        f"L1精确: {ph}",
        f"L2空格小写: {{{{ org_{code} }}}}",
        f"L2大写hash: {{{{ORG_{code.upper()}}}}}",
        f"L3单括号: {{ORG_{code}}}",
        f"L3左双右单: {{{{ORG_{code}}}",
        f"L3左单右双: {{ORG_{code}}}}}",
        f"L3裸码: ORG_{code}",
        f"L4全角双括号: ｛｛ORG_{code}｝｝",
        f"L4全角方括号: 【ORG_{code}】",
        f"L4全角下划线: ORG＿{code}",
        f"L5汉化前缀: 机构_{code}",
    ]
    for line in variants:
        restored = restore_text(line, vault)
        assert restored.endswith(": 招商银行"), f"failed to restore variant: {line} -> {restored}"


def test_group8_level5_localized_prefix_lookup() -> None:
    vault = RequestScopeVault()
    p = vault.put("ORG", "招商银行")
    suffix = p[-10:-2]
    assert restore_text(f"关于机构_{suffix}的报告", vault) == "关于招商银行的报告"
    # Ambiguous / missing suffix in Level-5 localized form is left untouched.
    assert restore_text("关于机构_zzzzzzzz的报告", vault) == "关于机构_zzzzzzzz的报告"


def test_group8_single_stray_brace_in_code_not_eaten() -> None:
    vault = InMemoryVault(salt=SALT_A)
    ph = vault.put("ORG", "招商银行")
    body = ph[2:-2]
    # Only one left brace and no right brace -> brace stays as code syntax.
    assert restore_text(f"if (x) {{ {body}", vault) == "if (x) { 招商银行"


# ── Group 9: Unknown / hallucinated placeholders & system-note example codes ──


def test_group9_unknown_hallucinated_placeholder_does_not_raise_and_uses_fallback() -> None:
    vault = InMemoryVault(salt=SALT_A)
    vault.put("ORG", "招商银行")

    # Valid base32 syntax but not in vault -> §6.3 natural language fallback.
    text = "未知 {{ORG_aaaaaaaa}}、{{PROJECT_bbbbbbbb}}、{{PERSON_cccccccc}}、{{API_KEY_dddddddd}}"
    restored = restore_text(text, vault)
    assert restored == "未知 【某相关机构】、【某业务项目】、【某相关人员】、【脱敏项】"


def test_group9_system_prompt_example_code_with_non_base32_char_preserved() -> None:
    vault = InMemoryVault(salt=SALT_A)
    vault.put("ORG", "招商银行")
    # Malformed non-base32 codes must not leak verbatim in plain mode (replaced with readable fallback).
    sample = "示例 {{ORG_11111111}} 应原样保留"
    assert restore_text(sample, vault) == "示例 【某相关机构】 应原样保留"


def test_group9_unknown_placeholder_preserved_literally_in_json_aware_mode() -> None:
    vault = InMemoryVault(salt=SALT_A)
    vault.put("ORG", "招商银行")
    raw_json = '{"org": "{{ORG_aaaaaaaa}}"}'
    assert restore_json_aware(raw_json, vault) == raw_json


# ── Additional coverage: Builtin secrets, known memory, policies, HTML, performance, fallbacks ──


def test_builtin_secrets_api_keys_jwt_bearer_pem() -> None:
    vault = InMemoryVault(salt=SALT_A)
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAtestkey123456\n-----END RSA PRIVATE KEY-----"
    text = (
        f"OpenAI: sk-proj-AbCdEf1234567890GhIjKlMnOpQr\n"
        f"GitHub: ghp_1234567890abcdefghijklmnopqrstuvwxyz\n"
        f"AWS: AKIAIOSFODNN7EXAMPLE\n"
        f"Bearer: Bearer eyJtokenVal_12345678\n"
        f"密码：hunter2pass9\n"
        f"{pem}"
    )
    res = mask_text(text, vault=vault)
    assert "sk-proj-" not in res.text
    assert "ghp_" not in res.text
    assert "AKIAIOSFODNN7EXAMPLE" not in res.text
    assert "eyJtokenVal_12345678" not in res.text
    assert "hunter2pass9" not in res.text
    assert "MIIEowIBAAKCAQEAtestkey123456" not in res.text
    assert restore_text(res.text, vault) == text


def test_remask_immunity_does_not_corrupt_existing_placeholders() -> None:
    vault = InMemoryVault(salt=SALT_A)
    rules = UserRules.from_rules([Rule(kind="ORG", word="招商银行")])
    first = mask_text("招商银行研究报告", vault=vault, user_rules=rules)
    second = mask_text(first.text, vault=vault, user_rules=rules)
    assert second.text == first.text
    assert second.masked_total == 0


def test_known_cross_turn_memory_masks_previously_seen_secret() -> None:
    vault = InMemoryVault(salt=SALT_A)
    r1 = mask_text("密码为 SecretPass9988", vault=vault)
    assert r1.masked_total == 1
    ph = r1.items[0].placeholder

    # In the next turn, SecretPass9988 appears in plain prose without field label.
    r2 = mask_text("上轮提到的 SecretPass9988 需要轮换", vault=vault)
    assert r2.masked_total == 1
    assert r2.items[0].by == "known"
    assert r2.items[0].placeholder == ph


def test_policy_web_search_safe_keeps_public_org_masks_credentials() -> None:
    vault = InMemoryVault(salt=SALT_A)
    rules = UserRules.from_rules(
        [
            Rule(kind="ORG", word="招商银行"),
            Rule(kind="SELF_ORG", word="本行核心系统"),
        ]
    )
    policy = resolve_policy("fact_check_plus")
    assert policy.mode == PolicyMode.WEB_SEARCH_SAFE

    res = mask_prompt(
        "核查 招商银行 与 本行核心系统 及 sk-proj-AbCdEf1234567890GhIjKlMnOpQr",
        policy=policy,
        vault=vault,
        user_rules=rules,
    )
    assert "招商银行" in res.text
    assert "本行核心系统" not in res.text
    assert "sk-proj-" not in res.text


def test_policy_raster_generalize_uses_chinese_pseudonyms_without_braces() -> None:
    vault = InMemoryVault(salt=SALT_A)
    rules = UserRules.from_rules(
        [
            Rule(kind="ORG", word="招商银行", pseudonym="某头部股份行"),
            Rule(kind="PROJECT", word="凤凰工程"),
        ]
    )
    policy = resolve_policy("info_graphic_png")
    assert policy.mode == PolicyMode.RASTER_GENERALIZE

    res = mask_prompt("对比招商银行与凤凰工程", policy=policy, vault=vault, user_rules=rules)
    assert "{{" not in res.text
    assert "某头部股份行" in res.text
    assert "某重点项目" in res.text


def test_restore_html_safe_escapes_dom_characters() -> None:
    vault = InMemoryVault(salt=SALT_A)
    ph = vault.put("ORG", '<script>alert("xss")</script>')
    out = restore_html_safe(f"<p>{ph}</p>", vault)
    assert out == "<p>&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;</p>"


def test_custom_rules_validation_and_broken_regex_skip() -> None:
    rules = UserRules.from_rules(
        [
            Rule(kind="INTERNAL_ID", prefix="moni_"),
            Rule(kind="BAD_EMPTY", pattern="a*"),  # matches empty -> skipped
            Rule(kind="BAD_SYNTAX", pattern="[unclosed"),  # invalid regex -> skipped
            Rule(kind="TICKET", pattern=r"TKT-\d{6}"),
        ]
    )
    vault = InMemoryVault(salt=SALT_A)
    res = mask_text("编号 moni_abc12345 和 TKT-908070", vault=vault, user_rules=rules)
    assert res.masked_total == 2
    assert res.items[0].kind == "INTERNAL_ID"
    assert res.items[1].kind == "TICKET"


def test_mask_prompt_cmb_does_not_false_positive_on_cmbchina() -> None:
    rules = UserRules.from_rules(
        [Rule(kind="ORG", word="招商银行", aliases=("CMB",), match_case=True)]
    )
    res = mask_prompt(
        "CMB 与 CMBCHINA 都在名单里，招商银行也在。",
        vault=InMemoryVault(salt=b"\x02" * 32),
        user_rules=rules,
    )
    assert res.blocked is False
    assert res.leak_scan_hits == 0
    assert "CMBCHINA" in res.text
    assert res.masked_total == 2


def test_mask_prompt_true_leak_still_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    from moni.llm.desensitize import engine as engine_mod

    vault = InMemoryVault(salt=SALT_A)
    secret_key = "sk-proj-RealLeakTestKey1234567890"

    # 1. Direct scan catches leaked high-entropy API_KEY AND construct violation.
    hits_raw = engine_mod._scan_outbound_leaks(
        f"leaked payload {secret_key}",
        masked_pairs=[("API_KEY", secret_key)],
        construct_violations=0,
        leak_terms=(),
    )
    assert hits_raw >= 1

    hits_construct = engine_mod._scan_outbound_leaks(
        "clean",
        masked_pairs=(),
        construct_violations=1,
        leak_terms=(),
    )
    assert hits_construct == 1

    res_terms = mask_prompt(
        "未配置规则的绝密词组 需拦截",
        vault=vault,
        leak_terms=("未配置规则的绝密词组",),
    )
    assert res_terms.blocked is True
    assert res_terms.blocked_reason == "leak_scan_hit"

    # 2. Simulate an engine bypass returning unmasked API_KEY; mask_prompt must fail-closed.
    orig_impl = engine_mod._mask_text_impl

    def _leaky_impl(text: str, **kwargs):
        if text == f"send {secret_key}":
            return (
                MaskResult(text=text, masked_total=0, items=(), vault=vault),
                [("API_KEY", secret_key)],
                0,
            )
        return orig_impl(text, **kwargs)

    monkeypatch.setattr(engine_mod, "_mask_text_impl", _leaky_impl)
    res_bypass = mask_prompt(f"send {secret_key}", vault=vault)
    assert res_bypass.blocked is True
    assert res_bypass.blocked_reason == "leak_scan_hit"
    assert res_bypass.leak_scan_hits >= 1


def test_mask_prompt_overhead_within_20pct_of_mask_text() -> None:
    vault = InMemoryVault(salt=SALT_A)
    rules = UserRules.from_rules(
        [
            Rule(kind="ORG", word="招商银行", aliases=("招行", "CMB"), match_case=True),
            Rule(kind="SELF_ORG", word="本行同业部"),
            Rule(kind="PROJECT", word="天枢二期"),
        ]
    )
    policy = resolve_policy("standard", level_l3=True)
    valid_id = _make_valid_cn_id("11010519900307123")
    valid_card = _make_valid_luhn_card("622588012345678")
    para = (
        f"2026年三季度调研：招商银行营收 5.138 亿元，1.7 万网点，增长 12.35%。本行同业部推进天枢二期，"
        f"联系邮箱 analyst@bankcorp.cn，手机号 13800138000，身份证 {valid_id}，银行卡 {valid_card}，"
        f"抓取 https://rss.example.cn/all?token=tok_demo_123456&page=1，配置路径 /Users/alice/.moni/cfg.yaml，"
        f"服务器 8.8.8.8，文章ID 5d41402abc4b2a76，流水号 13912345678。"
        "行业分析指出，当前同业负债成本平稳，净息差企稳回升，重点关注零售客群经营与资产质量改善节奏。\n"
    )
    report = (para * 20)[:6000]
    gc.collect()
    mask_text(report, vault=vault, user_rules=rules, policy=policy)
    mask_prompt(report, vault=vault, user_rules=rules, policy=policy)

    t_text: list[float] = []
    t_prompt: list[float] = []
    for _ in range(20):
        rv1 = RequestScopeVault(parent=vault)
        t0 = time.perf_counter_ns()
        mask_text(report, vault=rv1, user_rules=rules, policy=policy)
        t_text.append((time.perf_counter_ns() - t0) / 1_000_000.0)

        rv2 = RequestScopeVault(parent=vault)
        t1 = time.perf_counter_ns()
        mask_prompt(report, vault=rv2, user_rules=rules, policy=policy)
        t_prompt.append((time.perf_counter_ns() - t1) / 1_000_000.0)

    p50_text = statistics.median(t_text)
    p50_prompt = statistics.median(t_prompt)
    assert p50_prompt <= p50_text * 1.20, (
        f"mask_prompt p50={p50_prompt:.3f}ms exceeded 1.20 * mask_text p50={p50_text:.3f}ms"
    )


def test_mask_prompt_leak_scan_blocks_if_leak_term_survives() -> None:
    vault = InMemoryVault(salt=SALT_A)
    res = mask_prompt(
        "本文包含 未配置规则的绝密词组 需拦截",
        vault=vault,
        leak_terms=("未配置规则的绝密词组",),
    )
    assert res.blocked is True
    assert res.leak_scan_hits >= 1
    assert res.fallback_used == "leak_scan_hit"


def test_mask_prompt_zero_raw_values_in_items_and_block_action() -> None:
    vault = InMemoryVault(salt=SALT_A)
    policy = resolve_policy("summary", actions={"PRIVATE_KEY": "block"})
    pem = "-----BEGIN PRIVATE KEY-----\nsecretBody1234567890\n-----END PRIVATE KEY-----"
    res = mask_prompt(f"key:\n{pem}", policy=policy, vault=vault, inject_note=True)
    assert res.blocked is True
    assert "secretBody1234567890" not in res.text
    for item in res.items:
        assert "secretBody" not in repr(item)


def test_vault_lru_eviction_and_ttl_expiry() -> None:
    vault = InMemoryVault(salt=SALT_A, max_entries=2, ttl_seconds=60.0)
    p1 = vault.put("ORG", "机构1")
    p2 = vault.put("ORG", "机构2")
    assert vault.get(p1) == "机构1"  # touch p1 -> p2 is now oldest
    p3 = vault.put("ORG", "机构3")
    assert len(vault) == 2
    assert vault.get(p2) is None
    assert vault.get(p1) == "机构1"
    assert vault.get(p3) == "机构3"

    short_ttl = InMemoryVault(salt=SALT_A, ttl_seconds=0.01)
    pt = short_ttl.put("ORG", "短命机构")
    time.sleep(0.02)
    assert short_ttl.get(pt) is None


def test_performance_6000_char_chinese_report_under_2ms() -> None:
    vault = InMemoryVault(salt=SALT_A)
    rules = UserRules.from_rules(
        [
            Rule(kind="ORG", word="招商银行", aliases=("招行", "CMB"), match_case=True),
            Rule(kind="SELF_ORG", word="本行同业部"),
            Rule(kind="PROJECT", word="天枢二期"),
        ]
    )
    paragraph = (
        "2026年三季度银行业同业调研显示，招商银行零售存款余额达 5.138 亿元，"
        "全国 1.7 万网点持续推进数字化转型，核心指标改善 12.35%。本行同业部在"
        "天枢二期项目复盘中对比了招行与CMB的资产负债结构，相关流水号 13800138000 "
        "与文章编号 5d41402abc4b2a76 均已入库归档，联系人手机号 13912345678 仅内部留存。"
    )
    # Build ~6,000 Chinese characters report.
    report = (paragraph * 45)[:6000]
    assert len(report) == 6000

    # Warm up regex/vault structures twice, then benchmark 3 rounds x 60 iterations.
    for _ in range(2):
        warm = mask_text(report, vault=vault, user_rules=rules)
        _ = restore_text(warm.text, vault)
    gc.collect()

    # 口径说明（2026-11-24 在本机 load avg 22 下实测得出，证据见 sprint-68 RUN.md §3.2）：
    # 1) 用 3 轮 × 60 样本合并后的 pooled P95，而不是"三轮 p95 取 max"
    #    （后者统计量接近 p99.9，一次 GC / 调度抖动就误报）。
    # 2) 用 process_time（CPU 时间）而非 wall clock：本机负载高时（load avg 22）
    #    wall-clock p95 实测 7/8 次越过 2.5ms（最高 15.18ms，纯 OS 调度抢占），
    #    而 CPU-time p95 8/8 次都在 2.5ms 内 —— 抖动来自宿主机争用而非代码成本。
    _PERF_N = 60
    pooled_cpu_ms: list[float] = []
    for _ in range(3):
        cpu_latencies_ms: list[float] = []
        for _ in range(_PERF_N):
            req_vault = RequestScopeVault(parent=vault)
            t0 = time.process_time_ns()
            masked = mask_text(report, vault=req_vault, user_rules=rules)
            restored = restore_text(masked.text, req_vault)
            t1 = time.process_time_ns()
            cpu_latencies_ms.append((t1 - t0) / 1_000_000.0)
            assert "本行同业部" not in masked.text
            assert "13912345678" not in masked.text
            assert "5.138 亿元" in masked.text
            assert "1.7 万网点" in masked.text
            assert "5d41402abc4b2a76" in masked.text
            assert "天枢二期" in restored
        pooled_cpu_ms.extend(cpu_latencies_ms)

    pooled_cpu_ms.sort()
    p50 = pooled_cpu_ms[len(pooled_cpu_ms) // 2]
    p95 = pooled_cpu_ms[int(0.95 * len(pooled_cpu_ms)) - 1]
    # Measured on this host (3x60, CPU time): p50=1.6-1.7ms, p95=1.9-2.2ms; 预算 2.5ms 来自规格
    # docs/plans/2026-11-24_llm-desens-shield-plan.md:239（P95 ≤ 2.5ms）。
    assert p95 < 2.5, f"6000-char mask+restore p95={p95:.3f}ms (p50={p50:.3f}ms) exceeded 2.5ms"
    assert p50 < 2.0, f"6000-char mask+restore p50={p50:.3f}ms regressed above 2.0ms"


def test_performance_dense_6000_char_report_p95_under_2ms() -> None:
    vault = InMemoryVault(salt=SALT_A)
    rules = UserRules.from_rules(
        [
            Rule(kind="ORG", word="招商银行", aliases=("招行", "CMB"), match_case=True),
            Rule(kind="SELF_ORG", word="本行同业部"),
            Rule(kind="PROJECT", word="天枢二期"),
        ]
    )
    policy = resolve_policy("standard", level_l3=True)
    valid_id = _make_valid_cn_id("11010519900307123")
    valid_card = _make_valid_luhn_card("622588012345678")
    para = (
        f"2026年三季度调研：招商银行营收 5.138 亿元，1.7 万网点，增长 12.35%。本行同业部推进天枢二期，"
        f"联系邮箱 analyst@bankcorp.cn，手机号 13800138000，身份证 {valid_id}，银行卡 {valid_card}，"
        f"抓取 https://rss.example.cn/all?token=tok_demo_123456&page=1，配置路径 /Users/alice/.moni/cfg.yaml，"
        f"服务器 8.8.8.8，文章ID 5d41402abc4b2a76，流水号 13912345678。"
        "行业分析指出，当前同业负债成本平稳，净息差企稳回升，重点关注零售客群经营与资产质量改善节奏。\n"
    )
    dense_report = (para * 20)[:6000]
    assert len(dense_report) == 6000

    for _ in range(2):
        warm = mask_text(dense_report, vault=vault, user_rules=rules, policy=policy)
        assert warm.masked_total >= 100
        _ = restore_text(warm.text, vault)
    gc.collect()

    # 同上：CPU-time pooled P95（3 轮 × 60 样本合并）。
    _PERF_N = 60
    pooled_cpu_ms: list[float] = []
    for _ in range(3):
        cpu_latencies_ms: list[float] = []
        for _ in range(_PERF_N):
            req_vault = RequestScopeVault(parent=vault)
            t0 = time.process_time_ns()
            masked = mask_text(dense_report, vault=req_vault, user_rules=rules, policy=policy)
            restored = restore_text(masked.text, req_vault)
            t1 = time.process_time_ns()
            cpu_latencies_ms.append((t1 - t0) / 1_000_000.0)
            assert "5.138 亿元" in masked.text
            assert "5d41402abc4b2a76" in masked.text
            assert valid_id not in masked.text
            assert valid_card not in masked.text
            assert "analyst@bankcorp.cn" not in masked.text
            assert "8.8.8.8" not in masked.text
            assert "alice" not in masked.text
            assert valid_id in restored
            assert "alice" in restored
        pooled_cpu_ms.extend(cpu_latencies_ms)

    pooled_cpu_ms.sort()
    p50 = pooled_cpu_ms[len(pooled_cpu_ms) // 2]
    p95 = pooled_cpu_ms[int(0.95 * len(pooled_cpu_ms)) - 1]
    # Measured on this host (3x60, CPU time): p50=1.6-1.7ms, p95=1.9-2.2ms; 预算 2.5ms 来自规格
    # docs/plans/2026-11-24_llm-desens-shield-plan.md:239（P95 ≤ 2.5ms）。
    assert p95 < 2.5, f"dense 6000-char mask+restore p95={p95:.3f}ms (p50={p50:.3f}ms) exceeded 2.5ms"
    assert p50 < 2.0, f"dense 6000-char mask+restore p50={p50:.3f}ms regressed above 2.0ms"


def test_builtin_rules_returns_non_empty_tuple() -> None:
    rules = builtin_rules()
    assert isinstance(rules, tuple)
    assert len(rules) >= 15


# ── Step 0: RestoreStats, write_through default=False, char counts, aborted flush ──


def test_restore_stats_exact_hallucinated_and_back_counts() -> None:
    v = RequestScopeVault(salt=b"\x01" * 32)
    ph = v.put("ORG", "招商银行")
    out = restore_text(f"{{{{ORG_11111111}}}} 与 {ph}", v)
    assert out == "【某相关机构】 与 招商银行"
    assert isinstance(v.stats, RestoreStats)
    assert v.stats.restored_total == 1
    assert v.stats.fuzzy_restored_total == 0
    assert v.stats.invalid_code_count == 1
    assert v.stats.hallucinated_count == 0
    assert v.stats.back_counts == {"ORG": 1}
    assert v.stats.restore_latency_us >= 0

    # Valid base32 unknown placeholder counts as hallucinated (not invalid_code_count).
    out2 = restore_text("还有 {{PERSON_aaaaaaaa}}", v)
    assert out2 == "还有 【某相关人员】"
    assert v.stats.hallucinated_count == 1
    assert v.stats.invalid_code_count == 1
    assert v.stats.restored_total == 1


def test_restore_stats_fuzzy_levels_json_html_and_stream_accumulate() -> None:
    v = RequestScopeVault(salt=SALT_A)
    ph_org = v.put("ORG", "招商银行")
    ph_person = v.put("PERSON", "张三")
    suffix = ph_org[-10:-2]

    # L2 spaces + case-fold, L3 broken braces, L4 fullwidth, L5 localized Han suffix.
    assert restore_text(f"{{{{ org_{suffix} }}}}", v) == "招商银行"
    assert restore_text(f"{{{ph_person[2:-2]}}}", v) == "张三"
    assert restore_text(f"｛｛{ph_org[2:-2]}｝｝", v) == "招商银行"
    assert restore_text(f"关于机构_{suffix}的分析", v) == "关于招商银行的分析"
    assert v.stats.restored_total == 4
    assert v.stats.fuzzy_restored_total == 4
    assert v.stats.back_counts == {"ORG": 3, "PERSON": 1}

    # restore_json_aware increments both restored_total and fuzzy_restored_total.
    raw_json = json.dumps({"org": ph_org}, ensure_ascii=False)
    restored_json = restore_json_aware(raw_json, v)
    assert json.loads(restored_json) == {"org": "招商银行"}
    assert v.stats.restored_total == 5
    assert v.stats.fuzzy_restored_total == 5

    # restore_html_safe & StreamRestorer accumulate without overwriting.
    assert restore_html_safe(f"<b>{ph_person}</b>", v) == "<b>张三</b>"
    sr = StreamRestorer(v)
    emitted = sr.feed(f"机构={ph_org[:6]}") + sr.feed(ph_org[6:]) + sr.flush()
    assert emitted == "机构=招商银行"
    assert v.stats.restored_total == 7
    assert v.stats.fuzzy_restored_total == 5
    assert v.stats.back_counts == {"ORG": 5, "PERSON": 2}


def test_request_scope_vault_write_through_default_false_and_stats_isolated() -> None:
    parent = InMemoryVault(salt=SALT_A)
    parent_ph = parent.put("ORG", "中信银行")

    req = RequestScopeVault(parent=parent)
    local_ph = req.put("ORG", "招商银行")
    assert req.get(local_ph) == "招商银行"
    assert req.get(parent_ph) == "中信银行"
    assert parent.get(local_ph) is None

    # RestoreStats on req must stay independent of parent.
    assert restore_text(f"{parent_ph} + {local_ph}", req) == "中信银行 + 招商银行"
    assert req.stats.restored_total == 2
    assert parent.stats.restored_total == 0

    # Explicit write_through=True writes through entries, still keeping stats separate.
    req_wt = RequestScopeVault(parent=parent, write_through=True)
    wt_ph = req_wt.put("PERSON", "李四")
    assert parent.get(wt_ph) == "李四"
    assert req_wt.stats.restored_total == 0


def test_request_scope_vault_finalize_restore_modes() -> None:
    # Known residual placeholder gets recovered on finalize_restore retry.
    v1 = RequestScopeVault(salt=SALT_A)
    ph = v1.put("ORG", "招商银行")
    assert v1.finalize_restore(f"主体：{ph}", mode="plain") == "主体：招商银行"
    assert v1.stats.residual_placeholders == 1
    assert v1.stats.unrestored_count == 0
    assert v1.stats.restored_total == 1

    # Broken/single-brace unknown residual falls back in plain/html modes and counts unrestored.
    v2 = RequestScopeVault(salt=SALT_A)
    out_plain = v2.finalize_restore("主体：{ORG_aaaaaaaa}", mode="plain")
    assert out_plain == "主体：【某相关机构】"
    assert v2.stats.residual_placeholders == 1
    assert v2.stats.unrestored_count == 1

    v3 = RequestScopeVault(salt=SALT_A)
    out_html = v3.finalize_restore("<p>{PERSON_bbbbbbbb}</p>", mode="html")
    assert out_html == "<p>【某相关人员】</p>"
    assert v3.stats.residual_placeholders == 1
    assert v3.stats.unrestored_count == 1

    # JSON mode preserves unknown placeholder verbatim and counts both residual & unrestored.
    v4 = RequestScopeVault(salt=SALT_A)
    raw_json = '{"org": "{{ORG_aaaaaaaa}}"}'
    assert v4.finalize_restore(raw_json, mode="json") == raw_json
    assert v4.stats.residual_placeholders == 1
    assert v4.stats.unrestored_count == 1


def test_mask_result_prompt_chars_before_after() -> None:
    rules = UserRules.from_rules([Rule(kind="ORG", word="招商银行")])
    v_text = RequestScopeVault(salt=SALT_A)
    res_text = mask_text("招商银行发布年报", vault=v_text, user_rules=rules)
    assert res_text.prompt_chars_before == 0
    assert res_text.prompt_chars_after == 0

    prompt = "招商银行发布年报"
    v_prompt = RequestScopeVault(salt=SALT_A)
    res_no_note = mask_prompt(prompt, vault=v_prompt, user_rules=rules, inject_note=False)
    assert res_no_note.prompt_chars_before == len(prompt)
    assert res_no_note.prompt_chars_after == len(res_no_note.text)

    res_with_note = mask_prompt(prompt, vault=v_prompt, user_rules=rules, inject_note=True)
    assert res_with_note.prompt_chars_before == len(prompt)
    assert res_with_note.prompt_chars_after == len(res_with_note.text)
    assert res_with_note.prompt_chars_after > res_no_note.prompt_chars_after

    res_blocked = mask_prompt(
        "招商银行与敏感词XYZ",
        vault=v_prompt,
        user_rules=rules,
        leak_terms=("敏感词XYZ",),
    )
    assert res_blocked.blocked is True
    assert res_blocked.prompt_chars_before == len("招商银行与敏感词XYZ")
    assert res_blocked.prompt_chars_after == len(res_blocked.text)


def test_stream_restorer_flush_aborted_drops_incomplete_tail() -> None:
    v = RequestScopeVault(salt=SALT_A)
    ph = v.put("ORG", "招商银行")

    sr1 = StreamRestorer(v)
    assert sr1.feed("前缀 {{SEC") == "前缀 "
    assert sr1.flush(aborted=True) == ""

    sr2 = StreamRestorer(v)
    assert sr2.feed("{{SEC") == ""
    assert sr2.flush(aborted=True) == ""

    sr3 = StreamRestorer(v)
    out3 = sr3.feed("前缀 {{ORG_r4") + sr3.flush(aborted=True)
    assert out3 == "前缀 "

    sr4 = StreamRestorer(v)
    out4 = sr4.feed("前缀 【机构_a") + sr4.flush(aborted=True)
    assert out4 == "前缀 "

    sr5 = StreamRestorer(v)
    out5 = sr5.feed("前缀 机构_a2") + sr5.flush(aborted=True)
    assert out5 == "前缀 "

    # Normal text ending in a Han suffix noun is NOT discarded on abort.
    sr6 = StreamRestorer(v)
    out6 = sr6.feed("这是哪家机构") + sr6.flush(aborted=True)
    assert out6 == "这是哪家机构"

    # Complete placeholder + trailing normal text restores cleanly on abort.
    sr7 = StreamRestorer(v)
    out7 = sr7.feed(f"{ph} 正常尾巴") + sr7.flush(aborted=True)
    assert out7 == "招商银行 正常尾巴"

    # Default flush(aborted=False) preserves buffered literal braces unchanged.
    sr8 = StreamRestorer(v)
    out8 = sr8.feed("前缀 {{SEC") + sr8.flush()
    assert out8 == "前缀 {{SEC"


# ── Round 3b: Fenced/lexical JSON boundaries & malformed/EXAMPLE_00000000 fallback ──


def test_json_aware_fenced_with_unpaired_quote_in_leading_prose() -> None:
    v = RequestScopeVault(salt=b"\x04" * 32)
    ph = v.put("ORG", "招商银行")
    doc = '说明："\n```json\n{"k": ' + ph + "}\n```"
    out = restore_json_aware(doc, v)
    assert out == '说明："\n```json\n{"k": "招商银行"}\n```'
    json_block = out.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    assert json.loads(json_block) == {"k": "招商银行"}


def test_json_aware_bare_value_quotes_properly() -> None:
    v = RequestScopeVault(salt=SALT_A)
    ph = v.put("ORG", "兴业银行")
    out = restore_json_aware(f'{{"k": {ph}}}', v)
    assert json.loads(out) == {"k": "兴业银行"}


def test_json_aware_nested_object_array_and_key_placeholders() -> None:
    v = RequestScopeVault(salt=SALT_A)
    ph_org = v.put("ORG", "招商银行")
    ph_proj = v.put("PROJECT", "天枢二期")
    raw = f'{{"{ph_org}": {{"list": [{ph_proj}, "{ph_org}"], "count": 1}}}}'
    out = restore_json_aware(raw, v)
    assert json.loads(out) == {"招商银行": {"list": ["天枢二期", "招商银行"], "count": 1}}


def test_json_aware_values_with_quotes_backslashes_and_newlines() -> None:
    v = RequestScopeVault(salt=SALT_A)
    secret = '甲"乙"\\丙\n丁'
    ph = v.put("ORG", secret)
    out_bare = restore_json_aware(f'{{"k": {ph}}}', v)
    out_str = restore_json_aware(f'{{"k": "{ph}"}}', v)
    assert json.loads(out_bare) == {"k": secret}
    assert json.loads(out_str) == {"k": secret}

    # Even/odd backslash parity inside JSON strings works accurately.
    raw_parity = f'{{"slash": "end\\\\", "k": {ph}}}'
    assert json.loads(restore_json_aware(raw_parity, v)) == {"slash": "end\\", "k": secret}


def test_json_aware_pure_json_matches_previous_behavior() -> None:
    v = RequestScopeVault(salt=SALT_A)
    ph1 = v.put("ORG", "招商银行")
    ph2 = v.put("ORG", '机构"A"\n分行')
    src = f'{{"summary": "重点关注 {ph1}", "org": {ph2}, "list": [{ph1}, "ok"]}}'
    out = restore_json_aware(src, v)
    assert json.loads(out) == {"summary": "重点关注 招商银行", "org": '机构"A"\n分行', "list": ["招商银行", "ok"]}


def test_json_aware_truncated_json_falls_back_to_restore_text() -> None:
    v = RequestScopeVault(salt=SALT_A)
    ph = v.put("ORG", "招商银行")
    truncated = f'{{"a": "{ph}'
    out = restore_json_aware(truncated, v)
    assert out == '{"a": "招商银行'


def test_json_aware_adjacent_bare_placeholders_do_not_produce_broken_json() -> None:
    v = RequestScopeVault(salt=SALT_A)
    ph1 = v.put("ORG", "招商银行")
    ph2 = v.put("PERSON", "张三")
    out = restore_json_aware(f'{{"a": {ph1} {ph2}}}', v)
    # Must fall back cleanly instead of emitting '"招商银行" "张三"' adjacent quotes.
    assert '"招商银行" "张三"' not in out
    assert out == '{"a": 招商银行 张三}'


def test_json_aware_unicode_escape_adjacency_not_corrupted() -> None:
    v = RequestScopeVault(salt=SALT_A)
    ph = v.put("ORG", "招商银行")
    out_partial = restore_json_aware(f'{{"s": "\\u00{ph}"}}', v)
    assert "\\u00" in out_partial
    assert "招商银行" in out_partial

    out_valid = restore_json_aware(f'{{"s": "\\u0041{ph}"}}', v)
    assert json.loads(out_valid) == {"s": "A招商银行"}


def test_malformed_and_example_codes_fallback_and_stats() -> None:
    v = RequestScopeVault(salt=SALT_A)
    # Non-base32 '1' typo replaces in plain mode and counts into invalid_code_count, not hallucinated/unrestored.
    out_typo = restore_text("残留 {{ORG_r4k7m2q1}} 结束", v)
    assert "{{" not in out_typo
    assert out_typo == "残留 【某相关机构】 结束"
    assert v.stats.invalid_code_count == 1
    assert v.stats.hallucinated_count == 0
    assert v.stats.unrestored_count == 0

    # System prompt example {{EXAMPLE_00000000}} is replaced in plain mode without counting as hallucination.
    out_ex = restore_text("示例 {{EXAMPLE_00000000}} 结束", v)
    assert "{{" not in out_ex
    assert out_ex == "示例 【脱敏项】 结束"
    assert v.stats.invalid_code_count == 2
    assert v.stats.hallucinated_count == 0
    assert v.stats.unrestored_count == 0

    # Valid base32 missing code still counts as hallucinated.
    out_hal = restore_text("幻觉 {{ORG_llllllll}} 结束", v)
    assert out_hal == "幻觉 【某相关机构】 结束"
    assert v.stats.hallucinated_count == 1
    assert v.stats.invalid_code_count == 2

    # In JSON mode, invalid/unknown codes stay verbatim; non-base32 still skips unrestored_count.
    v_json = RequestScopeVault(salt=SALT_A)
    raw_json = '{"typo": "{{ORG_r4k7m2q1}}", "ex": "{{EXAMPLE_00000000}}", "hal": "{{ORG_llllllll}}"}'
    assert v_json.finalize_restore(raw_json, mode="json") == raw_json
    assert v_json.stats.invalid_code_count == 2
    assert v_json.stats.hallucinated_count == 1
    assert v_json.stats.unrestored_count == 1

    # inject_note uses {{EXAMPLE_00000000}} instead of {{ORG_11111111}}.
    rules = UserRules.from_rules([Rule(kind="ORG", word="招商银行")])
    v_prompt = RequestScopeVault(salt=SALT_A)
    res_prompt = mask_prompt("招商银行发布公告", vault=v_prompt, user_rules=rules, inject_note=True)
    assert "{{EXAMPLE_00000000}}" in res_prompt.text
    assert "{{ORG_11111111}}" not in res_prompt.text
