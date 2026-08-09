# moni 申报截图

> moni-B §四.7 路线图 W1 申报包装 — 量化看板 (v1.0.19) 截图集
> 用于 §五「降本增效 + 效益提质」评分 +2-3 分佐证

## 截图清单 (6 张 = 3 时间窗 × 2 主题)

| 文件 | 时间窗 | 主题 | 关键数字 |
|------|--------|------|----------|
| `light-30d.png` | 30 天 | 浅色 | 默认 (申报推荐) |
| `light-7d.png`  | 7 天  | 浅色 | 短期对照 |
| `light-90d.png` | 90 天 | 浅色 | 全库累积 |
| `dark-30d.png`  | 30 天 | 深色 | 默认 (申报推荐) |
| `dark-7d.png`   | 7 天  | 深色 | 短期对照 |
| `dark-90d.png`  | 90 天 | 深色 | 全库累积 |

## 截图规格

- **视窗**: 1440px (Chrome 默认 desktop viewport)
- **页面**: http://127.0.0.1:8900/stats (顶栏 "量化看板")
- **截图工具**: Browserbase vision (Claude desktop viewport)
- **生成日期**: 2026-08-09

## 截图内容 (每张都包含)

1. **顶部三卡** — 基线 (上一周期) / 当前 / 目标 (× 1.2)
2. **5 模块横排** — 抓取 / LLM / 抽取 / 使用 / 效率 (状态徽章)
3. **抓取趋势图** — SVG inline path (无 chart.js 依赖)
4. **LLM 任务趋势图** — 同上
5. **节省瀑布图** — 按 task_type 节省分钟数分布 (柱状图)
6. **防夸大口径** — efficiency 卡明示 "公式估算 · config/savings_baseline.yaml"

## 申报材料引用

PDF / Word 申报材料中插入这 6 张截图时建议:

- **正文**: 用 `light-30d.png` (浅色更易打印 + 申报推荐)
- **附录 A**: 三档时间窗对比 (light-7d + light-30d + light-90d)
- **附录 B**: 深色版本 (展示主题适配)
- **Caption 模板**: "图 X — moni 量化看板 30 天视图 (浅色主题, 1440px 视窗, 2026-08-09)"

## 文件路径

```
docs/screenshots/v1.0.19/
├── README.md            (本文件)
├── light-30d.png
├── light-7d.png
├── light-90d.png
├── dark-30d.png
├── dark-7d.png
└── dark-90d.png
```

## 重新生成截图

```bash
# 1. 启 8900 服务 (v1.0.19)
pkill -f "uvicorn.*create_app"
/usr/local/bin/python3 -c "import uvicorn; from moni.web.server import create_app; uvicorn.run(create_app(), host='127.0.0.1', port=8900, log_level='error')" &

# 2. 浏览器打开 http://127.0.0.1:8900/stats
# 3. 切 7d / 30d / 90d 各截 1 张 (浅色)
# 4. 切 dark mode (🌙) → 同 3 个 period 各截 1 张
# 5. cp 到本目录覆盖
```

## 更新频率

- 每次 sprint closeout 重新生成 (数据反映当前业务量)
- 重大 UI 改动后强制重生成
- tag 对应目录版本化 (v1.0.19/, v1.0.20/, ...)