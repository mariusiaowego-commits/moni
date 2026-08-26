# 竞赛成果数据支撑方案 v1 — 案例库/日历真实数据 + 量化看板成果指标

日期: 2026-08-26
状态: v1 (待 agy review + grok 审计 → v2 报 dad 拍板)
分支: feature/v1.0.35.19-contest-showcase

## 背景（领导要求）

申报书"成效与成果"需写项目上线后的实际效果，例如：
- 追踪了多少家同业
- 发现多少有参考价值的案例
- 指导业务改造多少个 / 优化多少个

这些数字必须来自**生产环境真实数据**，不能编。领导点名三个方向：
1. 一篇实际产出物物料，明确看得出"找到了同业案例"
2. 案例库更多真实数据 → 汇总追踪同业数/参考价值案例数
3. 量化看板增加领导要的数据展示（累计追踪、同业分布饼图、高价值案例榜单）

## 现状勘察（2026-08-26 实查）

### 数据现状 (data/news.db)
| 表 | 行数 | 质量 |
|---|---|---|
| articles | 3453 | 11 源，最近全量 8-25 (33 新) / 8-14 (49 新) |
| cases | 53 | 只来自 **18 篇** 文章；全部 completed + draft；reference: 16 high / 22 medium / 15 low；case_type 全 AI/launch；counterparty 非空仅 22 |
| events | 758 | 666 completed + 2 failed + 90 skipped；financing 62 / other 221 / product 143 / regulation 115 / tech 217 |
| llm_jobs | 155 | 历史 LLM 任务 |

### 代码现状
- **提取只有单篇入口**: `POST /api/cases/retry/{article_id}` + `POST /api/events/retry/{article_id}`；批量函数 `extract_cases_for_articles_batch` / `extract_events_batch` 存在但**无 CLI / 无全量入口** ← 用户"不知道怎么添真实数据"的根因
- **pipeline 默认 provider 过时**: cases/events pipeline 默认 `minimax-cn / MiniMax-M3`，但 8-24 已 429 配额耗尽；激活 provider 是 **ark / ark-code-latest**（llm_settings active_*_model_id 已指向 ark）。用默认参数批量跑会 429 失败
- **counterparty 是自由文本**: "美国银行、摩根大通" / "Visa、万事达卡、渣打银行、贝莱德等11家机构" — 无法直接 DISTINCT 计数"追踪了多少家同业"
- **同 subject 重复**: SWIFT/美国银行摩根大通 2 条、Visa/BioCatch 2 条（不同 article 各自提取）
- **量化看板已有**: /api/stats/overview + timeseries (fetch/llm/events/cases/pins/share) + llm + savings + fact-check-method；extract_layer_stats 只有 events_total/cases_total 计数，**无同业/价值/榜单维度**
- 前端案例库页已有（筛选 + 卡片 + by_type/by_reference_value 统计）；/stats 页已有 5 模块卡片

### 提取流程（跑通性结论）
- LLM 薄壳: moni/llm/client.py 已固定 HERMES_PROFILE=coder，call_llm(provider, model, prompt) 走 hermes chat CLI → ark 可用
- 三阶段事务管道（normalize → insert → state）设计完整，单篇 retry 应该能跑（llm_jobs 155 条证明 LLM 链路通）
- **未验证**: 批量函数从未被调用过；案例提取 prompt 模板 (case_extraction.txt) 实际效果未知（53 条全 AI/launch 说明提取倾向单一）

## 工作分解（3 步）

### Step 1 — 全量抓取（用户自己做，我给 SOP）
```bash
cd /Users/mt16/dev/moni
set -a && source .env && set +a          # wechat token 必需
/usr/local/bin/python3 -m moni fetch     # 全量 11 源
```
- ⚠️ 8-25 那轮 success=0 有 error（需先查 fetch error 原因，避免白跑）
- 跑完看 articles 增量 + 分布

### Step 2 — 案例库/日历真实数据填充（我主力）
1. **新增 CLI**: `moni extract cases|events [--source NAME] [--since N] [--limit N] [--dry-run]`
   - 默认 provider/model 从 llm_settings 读 active model（ark），不再写死 minimax-cn
   - 选文优先级: 有 raw_content → 未提取过 → 最新优先；默认限流 3 并发（pipeline 已有 Semaphore）
   - dry-run 先出计划（多少篇候选 × 预计 LLM 调用数）
2. **counterparty 规范化**（追踪同业数真实性）:
   - normalize 时按顿号/逗号拆分 + 去重 + 清洗（去"等N家机构"）
   - 新增 `cases_institutions` 关联表 (case_id, institution, role) 或先 SQL 拆分统计 → 方案里定
3. **跑一次批量提取**（先 500-1000 篇试点，看案例质量，再全量）
4. **review 流程打通**: 案例库页已有 pending 列表 + confirm/reject 按钮；需要批量确认入口（勾选 N 条 → 批量 verified）

### Step 3 — 量化看板成果指标（我 + grok 前端）
新增 `/api/stats/cases-dashboard?period=`:
- total_cases 累计案例数
- total_institutions 累计追踪同业数（counterparty 拆分后 DISTINCT）
- high_value_cases 高价值案例数（reference_value=high）+ verified/published 数
- institution_distribution 同业分布 TOP10（饼图）
- high_value_ranking 高价值案例榜单（分 tag：按 tag → reference_value=high 排行）
- action_distribution 案例动作分布（launch/pilot/scale/transform）
前端 /stats 页新增"成果指标"模块（4 卡片 + 饼图 + 榜单表），榜单点项 → 跳案例库带筛选
"指导业务改造/优化"计数: 方案 A 用 case_lifecycle (published=已采用) + review 人工标记；方案 B 新增 business_impact 字段（参考/改造/优化/落地）→ 让 agy 从申报书口径给意见

### 产出物物料（领导要的"实际产出物"）
批量提取 + 数据填充后，用真实案例生成一份同业案例简报（HTML/PDF，复用简报能力），
明确展示: "本期追踪 N 家同业，发现 M 个可参考案例，其中 X 个高价值（分 tag 列出）"。

## 分工
- 我 (moni-hermes): 批量提取 CLI + counterparty 规范化 + stats API + 全流程协调
- agy (moni-agy): 方案 review + 申报书"成效与成果"段撰写口径 + plan 撰写
- grok (moni-grok): /stats 前端成果指标模块实现 + 方案审计（前端可行性）

## 风险
1. LLM 调用量: 全量 3453 篇 × ark 成本/时长 — 分批跑 + dry-run 先行 + 限流
2. counterparty 拆分质量: 自由文本拆分不准 → 清洗规则 + 人工 review 兜底；申报数字以"已核验"口径为准
3. 案例提取质量: 53 条全 AI/launch 说明 prompt 倾向单一 — 试点 100 篇评估 case_type/action 多样性，必要时调 prompt
4. 8-25 fetch success=0 原因未查 — Step 1 前置
5. 申报书数字诚实原则: 量化数字分"实测"与"待校准"，不夸大（competition-application-writing skill 铁律）

## 待确认（报 dad）
1. 批量提取试点规模（先 500 篇 vs 全量）
2. "指导业务改造/优化"计数口径（published 标记 vs 新增 business_impact 字段）
3. counterparty 规范化的表结构（关联表 vs SQL 拆分）
