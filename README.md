# source-monitor

同业信源自动抓取与增量检测脚本，为 Hermes Agent cron job 提供数据预处理。

## 项目背景

银行从业人员需要每天跟踪"微信生态 × 银行数字化 × AI"交叉领域的行业动态。
手动逐网站查看效率低，本项目提供自动化抓取+关键词过滤+增量检测能力。

## 目标

替代 browser 逐页爬取方案，将信源巡检从 ~3分钟 降低到 ~30秒。

## 架构

```
┌──────────────────────────────────────────────────┐
│  Hermes Cron Job (每天 09:00)                    │
│                                                  │
│  1. Script 阶段 (source_monitor.py)              │
│     ├── curl 抓取多个信源首页                    │
│     ├── 解析 RSS feed (36氪)                     │
│     ├── 关键词过滤                               │
│     ├── 增量检测 (对比 state.json)               │
│     └── 输出 JSON → stdout                       │
│                                                  │
│  2. Agent 阶段 (LLM)                             │
│     ├── 读取 stdout JSON                         │
│     ├── 对相关文章做摘要                         │
│     ├── 分类输出简报                             │
│     └── 保存 .md 日报文件                        │
└──────────────────────────────────────────────────┘
```

## 信源列表

| 优先级 | 信源 | URL | 抓取方式 |
|--------|------|-----|----------|
| P0 | 移动支付网 | https://mpaypass.com.cn/ | HTML解析 |
| P0 | 量子位 | https://www.qbitai.com/ | HTML解析 |
| P0 | 36氪快讯 | https://36kr.com/newsflashes | HTML解析 |
| P0 | 36氪RSS | https://36kr.com/feed | RSS/Atom解析 |
| P1 | 中国信通院 | https://www.caict.ac.cn/kxyj/qwfb/ztbg/ | HTML解析 |
| P1 | 发现报告 | https://www.fxbaogao.com/ | HTML解析 |
| P2 | 微信公众号 | (via WeRSS/WeChatRSS) | RSS解析(预留接口) |

## 关键词过滤

### 银行相关
银行, 零售银行, 获客, 活客, 财富管理, 客户经营, 私域, 数字化, 金融科技, 手机银行, 小程序(银行), 信用卡, 贷款, 理财, 基金

### 微信生态
微信, 小程序, 公众号, 视频号, 企微, 企业微信, 微信生态, 微信小店

### AI相关
大模型, AI, 智能体, 智能客服, LLM, 人工智能, GPT, DeepSeek, Agent

### 交叉组合（至少命中两个分类的关键词则保留）

## 技术要求

- Python 3.9+
- 纯标准库 + urllib（不依赖 pip 安装的第三方包）
- 如果标准库解析HTML不够用，可以引入 beautifulsoup4（bs4），但需在 README 中说明
- 脚本存放位置：本项目根目录 `source_monitor.py`
- 状态文件：`state.json`（存放上次抓取结果，用于增量对比）
- 输出：JSON 到 stdout，供 Hermes Agent 读取

## 输出格式 (stdout JSON)

```json
{
  "run_time": "2026-06-04T09:00:00+08:00",
  "total_articles": 45,
  "filtered_articles": 12,
  "new_articles": 8,
  "sources": [
    {
      "name": "移动支付网",
      "url": "https://mpaypass.com.cn/",
      "total": 20,
      "filtered": 5,
      "new": 3,
      "articles": [
        {
          "title": "文章标题",
          "url": "https://...",
          "date": "2026-06-04",
          "keywords_matched": ["银行", "AI"],
          "is_new": true
        }
      ]
    }
  ],
  "summary": {
    "banking": 3,
    "wechat": 2,
    "ai": 4,
    "cross": 2
  }
}
```

## Hermes 集成方式

cron job 配置：
```yaml
script: /Users/mt16/dev/source-monitor/source_monitor.py
no_agent: false  # Agent 读取 stdout 做摘要
```

## 开发阶段

### Phase 1 (当前)
- [x] 项目初始化
- [ ] 36氪 RSS 解析
- [ ] 移动支付网首页 HTML 解析
- [ ] 量子位首页 HTML 解析
- [ ] 关键词过滤逻辑
- [ ] 增量检测 (state.json)
- [ ] JSON 输出

### Phase 2
- [ ] 36氪快讯页 HTML 解析
- [ ] 中国信通院报告页
- [ ] 发现报告搜索页
- [ ] 错误处理与重试
- [ ] 超时控制（单源最多10秒）

### Phase 3
- [ ] 微信公众号 RSS 接口（WeRSS/WeChatRSS）
- [ ] 自定义关键词配置文件 (config.yaml)
- [ ] 日报模板化输出

## 测试方式

```bash
# 直接运行
python3 source_monitor.py

# 检查输出
python3 source_monitor.py | python3 -m json.tool

# 清除状态（重新全量）
rm state.json && python3 source_monitor.py
```
