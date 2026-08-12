# Sprint 08 — 简报 web 模式 tech spec (v1.0.24)

> **状态**: draft, 父 agent 全部拍板 (Q1-Q4 = B/A/A/A)
> **作者**: blue-owl (arch, hermes coder profile)
> **日期**: 2026-08-12
> **关联**: sprint-07 v1.0.22 事实核查+ 实施后 P1 候选 #2 (sprint-07 spec §1.2 OUT 收纳)

---

## 0. 背景与决策回顾

### 0.1 现状 (sprint-07 ship 后实查)

- v1.0.22 fact_check_plus 上线, 业务人员可对单篇文章跑联网核查 + 证据链落库
- 简报 worker (`moni/share/enrichment.py`) 生成简报时, 事实核查**仍走基础档** (v1.0.10 LLM 常识推断, method=llm_inference)
- 简报 markdown / HTML 模板 (`moni/share/templates/`) **不含** evidence URL 引用
- 银行业务痛点: 简报发给领导/同事后, 接收方看不到"事实核查证据来源", 信任度低

### 0.2 拍板 (Q1-Q4 = B/A/A/A, 父 agent 全权)

- **Q1=B** 触发方式 = 复用已有 fact_check_plus 结果, 没做的才跑 (性能优先, 不阻塞简报)
- **Q2=A** evidence 嵌入 = 简报底部脚注, 银行 PDF 阅读习惯
- **Q3=A** 简报开头显示"已核查 N 篇/未核查 M 篇" rollup, 跟现有 top 摘要对齐
- **Q4=A** evidence 失败 → 该篇标"未联网核查" 不阻塞简报, B5 修了后失败率低

---

## 1. 范围 (In/Out)

### 1.1 IN (v1.0.24 ship)

1. **简报生成前, 对每个 article 检查 `has_web_fact_check` (sprint-07 引入的字段)**
2. **未联网核查的文章, 自动 enqueue fact_check_plus job (走现有 llm_jobs pipeline, 不改 worker)**
3. **简报 markdown / HTML 模板底部加 "事实核查证据" 段** (N 篇文章已联网核查 + evidence 列表)
4. **简报顶部 rollup 加 "已联网核查 N 篇"** 标签 (跟现有 "已核实/待核实/存疑" 三档并排)
5. **evidence 解析失败降级** (Q4): 该篇文章 evidence section 写 `（未联网核查，证据解析失败）` + 简报整体仍生成
6. **新增 `articles.evidence_rendered_at`** 字段 (TIMESTAMP) — 简报生成后写入, 跟 `has_web_fact_check` 配套 (审计: 哪次简报用了哪次核查结果)
7. **测试** (5-7 个): 见 §5

### 1.2 OUT (P1+ 留)

- 简报 worker 改用 LLM 检索 (sprint-07 spec §1.2 OUT, 留 P1)
- evidence 按 URL 去重 (跨文章同 URL 合并, P1)
- evidence 时效校验 (证据时间 > 文章发布时间则提示过期, P1)
- 人工编辑 evidence 链路 (Q4 失败降级 ≠ 人工编辑, 后者 P2)

### 1.3 Non-goals

- 不改 `moni/share/worker.py` 子进程模式 (enqueue 走现有 llm_jobs)
- 不改简报 7 种产物 (PDF/HTML/PNG/MD 等) — 只改 MD/HTML 模板内容, 产物渲染管道不动
- 不动 Q3 看板 method 分布 (sprint-08 单独 ship)

---

## 2. 文件清单 (~150 行 net add)

| 文件 | 改法 | 内容 |
|------|------|------|
| `moni/share/enrichment.py` | +50 | 简报生成时检查 `has_web_fact_check`, 缺则 enqueue fact_check_plus |
| `moni/share/templates/brief_md.j2` | +30 | MD 模板底部 "事实核查证据" 段 + 顶部 rollup |
| `moni/share/templates/brief_html.j2` | +30 | HTML 模板底部 "事实核查证据" 段 + 顶部 rollup |
| `moni/db/migrations/026_brief_evidence_rendered.sql` | +10 | `evidence_rendered_at` 字段 + INSERT OR IGNORE schema_version 26 |
| `moni/db/migrate.py` | +1 | `_CURRENT_VERSION` 25 → 26 |
| `moni/web/routes/share.py` | +10 | 简报生成后 UPDATE `articles.evidence_rendered_at` |
| `tests/test_brief_web_mode.py` | +80 | 6-7 个新测试 |
| `tests/test_fact_check_plus.py` | +10 | `evidence_rendered_at` 字段存在性 + 简报生成后写入 |

---

## 3. 数据契约

### 3.1 新字段 (migration 026)

```sql
ALTER TABLE articles ADD COLUMN evidence_rendered_at TEXT;  -- ISO 8601, 简报生成时写入
```

`evidence_rendered_at` 跟 `fact_checked_at` 的区别:
- `fact_checked_at` — fact_check_plus job 完成的时刻
- `evidence_rendered_at` — 某次简报生成时实际使用的核查结果的时刻 (可能复用旧的 fact_check_plus, 不重跑)

### 3.2 简报 markdown 模板 (新增段)

```markdown
## 事实核查证据 (本简报)

- ✅ 已联网核查: 3/4 篇 (1 篇 evidence 解析失败, 标 "未联网核查")
- 方法: web_search (hermes -t web 实时检索)
- 时效: 2026-08-12T14:30:00Z 检索快照

### 文章 1: 建设银行发布 2026Q2 季报

- 结论: 【已核实】
- 来源:
  - [1] 央行公告: https://www.pbc.gov.cn/...  (2026-08-10)
  - [2] 新华社: https://www.news.cn/... (2026-08-11)
- 引用片段: 央行宣布...

### 文章 2: 招行 AI 风控升级

- 结论: 【已核实】
- 来源: ...
```

### 3.3 简报 HTML 模板 (新增段)

```html
<section class="fact-check-evidence">
  <h3>事实核查证据 (本简报)</h3>
  <div class="rollup">
    <span class="badge web_search">已联网核查: 3/4 篇</span>
  </div>
  <ol class="evidence-articles">
    <li>
      <h4>建设银行发布 2026Q2 季报</h4>
      <p class="conclusion">【已核实】</p>
      <ol class="evidence-list">
        <li>
          <a href="https://www.pbc.gov.cn/...">[1] 央行公告</a>
          <p class="snippet">央行宣布...</p>
        </li>
      </ol>
    </li>
  </ol>
</section>
```

### 3.4 失败降级 (Q4)

当某篇 evidence 解析失败时 (B5 修了后会少很多, 但仍要兜底):
- evidence section 该篇写 `（未联网核查，证据解析失败，请人工复核）`
- 简报整体仍生成, rollup 标 `已联网核查: 2/4 篇 (2 篇失败降级)`

---

## 4. 代码契约 (enrichment.py 伪代码)

```python
def enrich_brief(manifest: dict) -> dict:
    # 现有 summary / fact_check (基础档) 逻辑保留
    ...
    # B5+ 后新加: 联网核查
    for article in manifest["articles"]:
        aid = article["id"]
        if not article.get("has_web_fact_check"):
            # 缺联网核查 → enqueue (不阻塞, 异步)
            enqueue_job(task_type="fact_check_plus", article_id=aid,
                        provider=settings["provider"], model=settings["model"])
    ...
    # 写回 DB: 简报生成时实际使用的 evidence 时刻
    now = datetime.now(timezone.utc).isoformat()
    for article in manifest["articles"]:
        conn.execute(
            "UPDATE articles SET evidence_rendered_at = ? WHERE id = ?",
            (now, article["id"]),
        )
```

注意: 简报 worker **不阻塞**等 fact_check_plus 完成 (那是异步 job), 简报生成时**复用已有 evidence** (从 `fact_check_evidence` 表读). 这样:
- 已有 evidence 的文章: 简报立即出, 证据链完整
- 缺 evidence 的文章: enqueue job 后本次简报 evidence 段空白, 下次简报再看

---

## 5. 测试 (test_brief_web_mode.py ~80 行)

| 测试名 | 验证 |
|--------|------|
| `test_brief_web_mode_enqueues_missing_fact_check_plus` | 简报含 2 篇, 1 篇有 has_web_fact_check / 1 篇没有 → 验 llm_jobs 新增 1 个 fact_check_plus |
| `test_brief_web_mode_skips_articles_already_checked` | 2 篇都有 has_web_fact_check → 验 llm_jobs 不增 |
| `test_brief_web_mode_renders_evidence_in_md_template` | 简报 MD 模板含 "事实核查证据" 段 + N 篇 evidence 列表 |
| `test_brief_web_mode_renders_evidence_in_html_template` | HTML 模板同 |
| `test_brief_web_mode_failure_articles_marked_as_unverified` | 1 篇 evidence 解析失败 → 简报该篇标"未联网核查" + rollup 标失败数 |
| `test_brief_web_mode_writes_evidence_rendered_at` | 简报生成后 → 验 `articles.evidence_rendered_at` 已写入 (非 NULL) |
| `test_brief_web_mode_does_not_block_on_fact_check_plus_pending` | enqueue 后 worker 仍继续生成简报, 不等 fact_check_plus done |

---

## 6. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 简报生成时 evidence 段空白 (job 还没完成) | 中 | 简报"未联网核查"数偏高 | 简报顶部 rollup 明确标 N/M, 用户可下次再看; 不阻塞 |
| evidence_rendered_at 跟 fact_checked_at 混淆 | 中 | 审计混乱 | 字段命名区分 (前者"简报使用" / 后者"LLM 完成"), 注释清晰 |
| 模板 enqueue 数量暴增 (20 篇文章全缺) | 中 | llm_jobs 队列堆积 | 复用 has_web_fact_check 早 return, 缺才 enqueue; 限流由 LLM 端控 |
| 模板语法 jinja2 跨产物 (HTML/MD) 不一致 | 低 | 简报某格式 evidence 空白 | 复用现有 jinja2 模板机制, 同一 evidence 列表用两次 (HTML+MD) |
| 简报 worker 改动影响现有事实核查基础档 | 低 | 回归 | enqueue 走独立 job, 基础档 summary 仍按原逻辑 |

---

## 7. 实施顺序 (Day 4-7)

1. `git checkout -b feature/v1.0.24-brief-web-mode` (从 main)
2. 改 `moni/db/migrations/026_brief_evidence_rendered.sql` + `moni/db/migrate.py`
3. 改 `moni/share/enrichment.py` (enqueue + evidence 读)
4. 改 `moni/share/templates/brief_md.j2` + `brief_html.j2` (新增 evidence 段)
5. 改 `moni/web/routes/share.py` (写回 evidence_rendered_at)
6. 写 7 个新测试 + 跑全量
7. git commit + push + gh pr + merge --squash
8. tag v1.0.24 + push
9. kill 8900 + restart (绝对路径) + 5 端点 curl 验

---

## 8. 关联

- 触发 sprint-07 v1.0.22 fact_check_plus 落地 (没这个, 简报 web 模式无 evidence 可读)
- 跟 sprint-08 B5 (#1) 协同: B5 修了诊断盲区 → 简报 web 模式更稳
- 跟 sprint-08 看板 method 分布 (#3) 协同: 看板数据是简报 web 模式效果的量化证据
- 申报书 B v4 估分: 简报 web 模式 +2-3 分 (落 W5 路线图)
