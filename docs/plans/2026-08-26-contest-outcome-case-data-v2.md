# 竞赛成果数据支撑方案 v2 — 案例库/日历真实数据 + 量化看板成果指标

日期: 2026-08-26
状态: v2 (已吸收 agy review + grok 审计, 待 dad 拍板)
分支: feature/v1.0.35.19-contest-showcase
评审: agy → /tmp/moni-plan-review-agy-20260826.md (5 盲点 + 决策建议) / grok → /tmp/moni-plan-audit-grok-20260826.md (24 发现: P0×5 P1×9 P2×10)

## 一、评审裁决摘要（orchestrator 综合）

### 双 agent 共识（实锤，全部纳入 v2）
| # | 共识 | 来源 |
|---|---|---|
| C1 | **同业计数 = subject ∪ counterparty**（不是只 counterparty！counterparty 仅对手方，53 条里 58.5% 为空） | agy 盲点1 = grok F4 |
| C2 | **批量提取函数不能直接用**（`extract_cases_for_articles_batch` 不传 provider/model → 每篇 ValueError；Semaphore 只包 async 未接 batch） | agy 盲点4 = grok F2 |
| C3 | **"指导改造/优化"本轮不编数**（published=0、无业务采纳字段，做了就是编；外部同业监测 ≠ 本行内部改造，答辩一查即穿） | agy 盲点3 = grok F16 |
| C4 | **禁止首轮全量盲跑**（先 100 篇看 type/action 分布，再 500；前置过滤 raw_content≥100 + 信源优先级） | agy 盲点2 = grok F3/F10/F11 |
| C5 | **stats 成果卡必须支持 period=all 默认 all**（申报数字是累计，绑 7d/30d 切窗会打成 0；不套 N_EXTRACT_THRESHOLD 隐藏） | grok F7 |
| C6 | **normalize 默认 AI/launch 污染多样性**（53 条全 AI/launch；无法判定→标 unknown/丢弃，不瞎填） | agy 盲点5 = grok F11 |

### 分歧裁决
| 分歧 | agy 意见 | grok 意见 | 裁决（我） | 理由 |
|---|---|---|---|---|
| 机构表结构 | 新增 `case_institutions` 关联表（migration 031） | SQL 拆分 + alias，不建表 | **采纳 grok：本轮 SQL 拆分**，agy 表设计存为 v2 演进附录 | 时间窗口紧；数据质量未稳；几百案例全表扫毫秒级不构成性能问题；migration+回填+双写+测试成本高 |
| 改造/优化计数 | 三层漏斗 + `internal_adoption_status` 字段 | 本轮完全不做 | **采纳 agy 的口径设计，采纳 grok 的实施节奏**：三层漏斗作为申报口径框架；本轮只做"监测洞察层"实测数字 + "成果转化层"可数真实产物（share_tasks）；"业务成效层"留人工标注（字段设计好，本轮不建） | 申报书不能写死业务成效数字，但口径要设计完整才站得住 |
| 并发 | — | — | **CLI 默认串行 + 分批 checkpoint**（agy 盲点4: case_id COUNT 非互斥有并发竞态 → 串行避开；grok F10: 每批落库 + 进度 + SIGINT 摘要） | 稳定性优先 |

### agy 独有发现（纳入）
- **case_id 生成竞态**: `_generate_case_id` 用 COUNT 计算序号，并发下重复 → IntegrityError/静默丢。CLI 串行规避 + 后续可改时间戳+随机后缀
- **前置过滤漏斗**: 仅 2528 篇有有效 raw_content；mpaypass 1269 篇 + bestblogs 961 篇是金融高密度池；约 2500 篇串行需 5.5-8h → 分批 50 篇 checkpoint
- **prompt 缺业务领域 (domain) 提取**: 看板无法按业务线聚类 → 本轮先挂 article_tags 弱标签，prompt 加 domain 字段列为 v2

### grok 独有发现（纳入）
- **案例库无 review 闭环**: cases.js 无 confirm/reject/lifecycle，routes/cases.py 无 POST confirm/reject（events 才有！）；CaseRepository.confirm/reject 是死代码 → 先补单条 API + 按钮，批量确认缩成"筛选 high + 逐条确认"
- **no_cases 不落行** → "未提取过"无法判定，全量会重复烧 LLM → CLI 查 llm_jobs.task_type=case_extract 或先插 skip 占位 + `--retry-failed`/`--include-skipped`
- **case_tags 表空** → 分 tag 榜单无数据源 → 榜单改"高价值案例列表"，tag 列用 article_tags 并在 UI 标"来源标签"
- **F6 路由表**: `/stats` `/cases` 不在 `_viewToPath/_pathToView` → 深链/跳转 URL 落 /articles → 前端第一刀补映射 + initCasesPage 读 query
- **图表复用**: SVG 走 x-html 拼 path（stats.js:241 Alpine SVG path 不刷新坑）；机构 TOP5+其他用横条，不用 TOP10 饼（120px 圆不可读）；色板用 --chart-* CSS var
- **F19 资源冲突**: fetch 与 extract 不并行（bestblogs timeout 1800s）
- **F21 showcase**: 本 sprint 若只截生产 8900，方案明写"demo 不同步"

## 二、本 sprint 交付范围

**做**:
1. 批量提取 CLI `moni extract cases [--source] [--limit] [--dry-run] [--retry-failed]`（串行 + 50 篇 checkpoint + 进度 + SIGINT 摘要；provider/model 读 llm_settings active model = ark，抽公共 `_resolve_active_model()` 同时修单篇 retry 路由）
2. 前置过滤: raw_content≥100 + 信源/标签优先级（mpaypass/bestblogs/banking 池优先）
3. 试点: dry-run → 100 篇 → 看 type/action/reference 分布 → 500 篇
4. 同业规范化: repository 层 SQL 拆分函数（subject ∪ counterparty，split [、，,;；/及与] + 去"等N家.*" + alias 映射，认不出的进 unmapped 列表）
5. 案例库 review 闭环: 单条 confirm/reject API + 前端按钮 + lifecycle 流转（draft→verified→published）
6. `/api/stats/cases-dashboard?period=all`（默认 all）+ /stats 页"成果指标"全宽 section（4 卡 + 机构 TOP5 横条 + 高价值案例榜单 + 动作分布）
7. 前端路由表补 stats/cases + 榜单跳转案例库带筛选（reference_value/subject）
8. 产出物: 用 high 案例 → Pin 对应文章 → 一键简报生成 1 份真实同业案例简报（HTML），封面三句数字 = dashboard 三个字段（追踪 N 家 / M 个可参考 / X 个高价值）
9. 测试: stats 端点 + normalize split 单测 + CLI dry-run + pytest 全量回归
10. 文档: STATUS / USER_GUIDE 口径 + 看板 display_label（待核验/已核验）

**不做**（本轮）:
- ❌ 全量 3453 盲跑
- ❌ `case_institutions` 关联表（v2 演进，设计见附录 A）
- ❌ `business_impact` / `internal_adoption_status` 字段（v2 演进，设计见附录 A）
- ❌ 分 tag 排行（无 tag 数据源；改用高价值案例列表 + 来源标签弱标注）
- ❌ 一键批量 verified（先单条 confirm）
- ❌ 完整进度 modal / demo showcase mock（若只截生产页，demo 不同步）

## 三、实施步骤

### P0 基础设施修复（先修管道，再画看板）
1. `_resolve_active_model()` 公共函数（读 llm_settings active_summary_model_id → provider+model），修:
   - `extract_cases_for_articles_batch` 签名（传 provider/model）
   - `POST /api/cases/retry/{id}` 与 `POST /api/events/retry/{id}` 读 active model
   - events 侧 `extract_events_batch` 默认 minimax-cn 同步改（同根因）
2. normalize 默认值: AI/launch 无法判定时标 unknown/丢弃（先看 100 篇分布再定）
3. no_cases 落 skip 占位（或 CLI 查 llm_jobs 防重复烧 LLM）

### P1 批量提取 CLI
4. `moni extract cases|events` 子命令: 串行 + 50 篇 checkpoint + dry-run（候选篇数/已 skip/预计调用/粗算时长）+ `--after-id` resume + SIGINT 摘要 + `--include-skipped`/`--retry-failed`
5. 前置过滤漏斗: raw_content≥100 + 信源优先级 + banking/payment/ai-fintech 标签优先
6. 试点 100 篇 → 分布评估 → 500 篇（与全量 fetch 不并行）

### P2 量化看板成果指标
7. `moni/stats/repository.py::cases_dashboard_stats(period="all")` + 路由（默认 all，响应含 total_cases/total_institutions/high_value_cases/verified_cases/institution_distribution TOP5+其他/high_value_ranking/action_distribution/display_label）
8. 案例库 review 闭环: `POST /api/cases/{id}/confirm|reject` + 前端按钮 + lifecycle
9. 前端: app.js 路由表补 stats/cases → stats.js loadCasesDashboard + institutionBar（x-html）+ goCasesFromOutcome 跳转 → index.html `.stats-outcomes` 全宽 section（4 卡 + 横条 + 榜单表 + 口径 chip"累计，不受 7/30/90 天影响"）
10. 空态/全0/单扇100%/暗色 手测

### P3 产出物物料 + 申报书
11. 高价值案例 → Pin → 一键简报生成 1 份真实简报（HTML），封面 = dashboard 三数字
12. 申报书 §五 成效与成果: 三层漏斗口径（监测洞察层实测数字 + 成果转化层真实产物 + 业务成效层定性+锚点案例），改造/优化不写死数字
13. 全量 pytest 回归 + 文档同步

## 四、申报书口径（三层漏斗，来自 agy 建议 a）

```
1. 监测洞察层 (系统实测·全量)   ← 本轮可写死
   累计追踪同业机构数: N 家 (subject ∪ counterparty 规范化后 DISTINCT)
   结构化沉淀同业案例: M 个 (分 type/action)
   高借鉴价值案例: X 个 (reference_value=high)
2. 成果转化层 (人机协同·真实产物)  ← 本轮可数
   自动生成同业对标简报: K 期 (share_tasks 真实产物数)
3. 业务成效层 (落地赋能·人工标注)  ← 本轮定性 + 锚点案例，不写死数字
   支撑业务方案论证/流程优化/架构改造: 定性描述 + 案例号锚点
```

## 五、分工
- 我 (moni-hermes): P0 管道修复 + P1 CLI + P2 stats API + 协调 + P3 简报
- grok (moni-grok): P2 前端成果模块（路由表/卡片/横条/榜单/跳转）+ 前端审计
- agy (moni-agy): 申报书"成效与成果"段撰写（三层漏斗落地）+ v2 方案终审
- dad: 全量抓取 Step 1 (SOP 已给) + 拍板

## 六、待 dad 拍板（3 Qs）
1. **试点规模**: A 先 100 看分布再 500（推荐）/ B 直接 500 / C 全量
2. **机构规范化**: A SQL 拆分（推荐，快）/ B 关联表 case_institutions（agy 推荐，规范但重）
3. **业务成效层**: A 本轮定性+锚点案例，不建字段（推荐）/ B 本轮就建 internal_adoption_status 字段先空着

## 附录 A — v2 演进设计（本轮不做，存案）
- `case_institutions` 表: (id, case_id FK, institution_name, role subject/counterparty, created_at) + idx(name) — 数据稳定后落表替换 SQL 拆分
- `internal_adoption_status`: none/referenced/optimized/transformed + adopted_by_dept + adoption_note — 人工标注，申报业务成效层用
- prompt 加 domain/track 业务领域提取 → 看板按业务线聚类
