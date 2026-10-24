# sprint-27 / v1.0.60 + v1.0.62 独立审计报告

- **审计对象**: PR #160 `feat(fetch): 接入中文独立博客 OPML 信源 (v1.0.60)`；
  v1.0.62 中文独立博客失败率熔断与配置链修复
- **审计日期**: 2026-10-24
- **审计方式**: 逐文件代码审 + 离线测试 + 真实网络 smoke + 干净 HOME 隔离
- **结论**: **PASS（修复后）**

---

## 1. 审计范围

| 文件 | 检查点 |
|---|---|
| `moni/fetch/sources/chinese_independent_blogs.py` | URL 安全、超时、响应上限、失败隔离、轮转、统计 |
| `tests/test_chinese_independent_blogs.py` | 真实 OPML、RSS/Atom、预算、失败/非法隔离、注册 |
| `moni/db/seed_config.py` | tag/source/source_tag 一致性、默认开关 |
| `config/sources.yaml` | 与 seed 一致、enabled/interval |
| `moni/web/routes/config_sources.py` | fetch_config 默认值可预填 |

---

## 2. 发现项

### P2-1 `resp.encoding` 在 stream 上下文外读取（已修）

- 位置: `moni/fetch/sources/chinese_independent_blogs.py` `_http_text`
- 风险: 依赖响应对象在上下文退出后的元数据可用性，不同 httpx 版本可能行为不同。
- 修复: 在 `with httpx.stream(...)` 内读取 `resp.encoding`，再在外部解码。
- 验证: 定向测试 + 全量回归。

### P2-2 feed 健康统计未进入 `run()` 返回值（已修）

- 位置: `FetcherChineseIndependentBlogs.fetch/run`
- 风险: 只能从日志判断 306/695 类健康度，任务结果本身看不到。
- 修复: `last_stats` 记录 `feeds_total/scanned/ok/empty/invalid/failed/cursor_*`，`run()` 合并到返回值。
- 验证: `test_stats_are_returned_by_run`。

### P3-1 cleaner 管线缺测试（已修）

- 风险: 只能证明解析出 HTML，不能证明入库前被 cleaner 清洗。
- 修复: `test_html_content_is_cleaned_by_base_pipeline` 断言 `cleaned == 1`。

---

## 3. 未发现的高危项

- 无 SQL 注入：所有 DB 查询使用参数绑定。
- 无任意文件读取：本地 OPML 被 `_inside_config_root()` 限制在仓库 `config/`。
- 无无限重定向：使用 httpx 默认重定向上限，且仅 http(s) feed 进入抓取。
- 无单 feed 拖垮整轮：传输/HTTP/XML 错误均被隔离；v1.0.62 起改为失败率熔断，
  404/410 与 XML 错误不计入网络失败率，429/5xx 计入。
- 无每轮 695 全扫：`max_feeds` 窗口 + cursor 轮转。
- 无 tag 门控误杀：新 tag 无关键词，天然 pass-through；未绑定 wechat/general-tech。

---

## 4. 验证证据

### 4.1 定向

```text
/usr/local/bin/python3 -m pytest \
  tests/test_chinese_independent_blogs.py tests/test_fetch_config.py \
tests/test_fetch_timeout_config.py tests/test_seed_config_fetch_config.py \
  -q -p no:cacheprovider
90 passed
```

### 4.2 全量

```text
/usr/local/bin/python3 -m pytest tests/ -q -p no:cacheprovider
1280 passed, 2 skipped, 0 failed
```

### 4.3 干净隔离

```text
HOME=$(mktemp -d) MONI_DATA_DIR=$(mktemp -d) \
  /usr/local/bin/python3 -m pytest \
  tests/test_chinese_independent_blogs.py tests/test_bestblogs.py tests/test_fetch_config.py \
  -q -p no:cacheprovider
105 passed
```

### 4.4 真实 smoke

```text
12 feeds / 24 article cap / 临时 DB / 当前代码
scanned 12 / ok 4 / failed 7 / http_error 1 / invalid 0 / empty 0
articles 8（网络失败率 58%，未触发 70% 熔断）
```

样例字段：标题、URL、publisher、published_at、content_len 均非空。

### 4.5 全量可行性

| 项 | 结果 |
|---|---:|
| feeds | 695 |
| ok | 306 |
| articles available | 13,107 |
| feeds with content | 290 |
| median latency | 1.15 s |

---

## 5. 合入门禁

- [x] 独立 audit 完成
- [x] audit 发现项已修复
- [x] 定向测试通过
- [x] 全量回归通过
- [x] 干净 HOME 隔离通过
- [x] 真实网络 smoke 通过
- [ ] PR CI 绿
- [ ] squash merge
- [ ] tag v1.0.62
- [ ] 8900 重启 + 5 端点

> 本文件只记录审计结论与证据，不直接触发发布。
