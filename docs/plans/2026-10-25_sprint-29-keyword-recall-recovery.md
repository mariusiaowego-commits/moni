# sprint-29 TECH-SPEC: 词边界收紧后的召回补偿 (v1.0.65)

- **Sprint ID**: 26102502
- **目标版本**: v1.0.65
- **阶段**: sprint-29
- **日期**: 2026-10-25
- **目标仓库**: `mariusiaowego-commits/moni`
- **状态**: 已实现，待审计
- **前序**: v1.0.64 (PR #166) 加词边界杀掉误命中，同时连带丢了一批真命中

---

## 1. 触发 (v1.0.64 收尾审计发现)

v1.0.64 的收尾文档原本把「152 篇旧规则独有存活」直接写成「152 篇误命中」。
独立审计要求拿出构成证据，拆开后发现**这个等式不成立**:

| 关键词 | 涉及篇数 | 实际是什么 |
|---|---:|---|
| `App` | 70 | 多数是 Apple / Apply / Apparently 碎片, 但也有 `Web Application Security` |
| `CV` | 41 | **绝大多数是 CVPR / ECCV 论文标题** —— `CV` 在 ai-fintech 里就是「计算机视觉」, 这是真命中 |
| `ark` | 11 | 确实全是 Markdown / SarkGay / Apple Park / market 等碎片 |
| `MA` | 7 | 确实全是 MASAJIN / MAC / SMART 等碎片 |

也就是说: `ark` / `MA` 那 18 篇是真误命中被正确杀掉, 而 `CV` 那 41 篇是被词边界
误伤的真命中 —— `CVPR 2026` 里 `CV` 后面直接跟 `P`, 不算词边界。

**根因**: 关键词表里有 `CV` 这个 2 字母缩写, 但真实语料里它以 `CVPR` / `ECCV`
形式出现的时候远多于独立出现。收紧词边界是对的, 缺的是把高频复合词补进表。

---

## 2. 处置方式的选择

三条路, 选了第三条:

| 方案 | 为什么不选 |
|---|---|
| 放宽规则 (让 ASCII 词允许后缀字母) | 等于把 `MA`→MASAJIN、`ark`→Markdown 一起放回来, 前功尽弃 |
| 删掉 `CV` 关键词 | 会让独立出现的 "CV" 也进不来, 而那是这个 tag 的核心语义 |
| **补 `CVPR` / `ECCV` 两个复合词** | 精准命中真实形态, 不动规则, 不影响其它词 |

---

## 3. 实测 (2026-10-25 生产库快照)

用只读重放对比「现状」与「补词后」:

| 口径 | 篇数 |
|---|---:|
| v1.0.64 现状存活 | 4210 |
| 补 `CVPR` + `ECCV` 后 | 4236 (**+26**) |
| 其中「被误杀救回」 | 26 (CVPR 17 / ECCV 9) |
| 其中「引入新噪音」 | **0** |

零噪音这一点很关键: 这 26 篇在旧规则下本来就该入库, 补词只是把词边界误伤的那部分
补回来, 没有放宽任何闸门。

### 3.1 刻意没有补的词

| 词 | 全库命中 | 为什么不补 |
|---|---:|---|
| `Application` | 20 | 救回的 9 篇是 `Web Application Security` / `Applications are now open for Fellowships` —— 和 APP (手机应用) 这个 tag 的语义无关, 补了等于把误命中放回来 |
| `NTIRE` | 2 | 只在 `CVPR NTIRE` 里出现, 已被 `CVPR` 覆盖, 单列收益 1 篇不值得多一个词 |
| `CVE` | 5 | 是安全漏洞编号 (CVE-2025-66478), **不是**计算机视觉; 旧规则命中它本身就是误命中 |

---

## 4. 改动文件

| 文件 | 改动 |
|---|---|
| `moni/db/migrations/038_ai_fintech_cv_acronyms.sql` | **新增** 给 `ai-fintech` 追加 `CVPR` / `ECCV` |
| `moni/db/migrate.py` | `_CURRENT_VERSION` 37 → 38 |
| `tests/test_migration_038_cv_acronyms.py` | **新增** 7 个测试 |
| `tests/test_llm_usage_counters.py` | 版本断言 37 → 38 |
| `tests/test_migration_030_ghost_cleanup.py` | 同上 (2 处) |
| `tests/test_migration_036_36kr_source_id.py` | 同上 |
| `tests/test_migration_037_cn_indie_tags.py` | 同上 (改用 `_CURRENT_VERSION` 常量, 避免下次再漏) |

### 4.1 迁移必须是「追加」而不是「重写」

第一版用 `UNION` 合并关键词, 结果 SQLite 把数组按字母重排了 ——
`['人工智能', '生成式AI', 'AIGC', ...]` 变成 `['AI Agent', 'AI Copilot', 'AIGC', ...]`。
关键词是用户在 /config 里手工维护的**有序**数据, 静默重排就是篡改。

改成 `json_insert(keywords, '$[#]', 'CVPR')` 逐条追加, 并在追加前用
`json_each` 检查是否已存在 (保证幂等)。测试 `test_038_preserves_the_existing_order`
钉住这一点。

---

## 5. 验收标准

- [x] 全量 pytest 通过 (**1386 passed / 2 skipped**, 基线 1379)
- [x] 迁移幂等 (跑两次结果一致, 不重复追加)
- [x] 原有关键词顺序完整保留 (`kws[:len(before)] == before`)
- [x] `keywords IS NULL` 的 tag 不被写入 `[]` + 2 条
- [x] `CV` 关键词保留 (是补充不是替换)
- [x] 生产库迁移后 schema 38 / 关键词 79 → 81
- [x] 生产库只读重放: 4210 → 4236 (+26), lost 152 → 126
- [x] 补词引入的新噪音 = 0
- [ ] 独立审计 APPROVE

---

## 6. 不做的事

- 不动匹配规则本身 (词边界保持 v1.0.64 的设计)
- 不删任何现有关键词 (`CV` / `MA` / `ark` 都留着)
- 不批量回补存量文章的 tag 归属 (那 152 篇仍在库里, 一篇没删; 只是新入库的才会带上新词)
- 不加 `Application` / `NTIRE` / `CVE` (理由见 §3.1)

---

## 7. 后续可做 (不在本 sprint)

- `payment` tag 只有 `支付` 一个词, 命中面仍然太宽
- `MA` 按新规则已归零, 可以考虑删掉 (但删词会让已入库文章的历史 hit 失去解释, 需单独决策)
- 存量 126 篇的 tag 归属是否重算
