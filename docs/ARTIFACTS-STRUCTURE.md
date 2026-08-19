# ARTIFACTS 目录结构 (Sprint-15 整肃)

> 目的: 说明 moni 里哪些目录是**生产真目录**, 哪些已被**归档**为残留, 让后续 agent / 人工一眼看清物料的真相来源, 不再被根目录散落的旧目录混淆。

## 生产真目录 (唯一真相来源)

| 目录 | 用途 | 规模 | 代码引用 |
|------|------|------|----------|
| `data/artifacts/` | 分享 worker 的产物落盘目录 (png / html / pdf / md / txt 等所有 share 格式) | 142 文件, ~17MB, 最新 08-19 | `moni/share/artifacts.py:25` — `ARTIFACTS_DIR = .../data/artifacts` |
| `data/briefings/` | 一键简报 HTML + 图片产物 | 3 文件 (08-12 生成) | — |

**代码佐证** (写死路径, 非配置):
- `moni/share/artifacts.py:25` — `ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "data" / "artifacts"`
- `moni/share/worker.py:36` — `DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "artifacts"`

## 已归档目录 (残留, 非 prod 用)

以下根目录残留目录已于 **2026-08-19** 移动 (mv, 未删) 到归档目录, 不再参与任何生产流程:

| 原路径 | 归档路径 |
|--------|----------|
| `output/` | `~/Documents/moni-archive-20260819/output` |
| `share_output/` | `~/Documents/moni-archive-20260819/share_output` |
| `infographic/` | `~/Documents/moni-archive-20260819/infographic` |
| `infographics/` | `~/Documents/moni-archive-20260819/infographics` |

> ⚠️ 归档 = 移动保留, **没有删除**。如需查找历史物料, 去 `~/Documents/moni-archive-20260819/` 找。

## 演示物料取自: `data/artifacts/`

演示 showcase 的物料一律从生产真相 `data/artifacts/` 取, 不再指向任何归档残留目录。5 个最新 / 最大真物料示例:

| 物料 | 路径 (`data/artifacts/...`) | 大小 | 时间 |
|------|------------------------------|------|------|
| info_graphic_png (最新) | `3I9-kJS0cpf4Yz-7KgqaAg-info_graphic_png.png` | 410 KB | 08-14 22:04 |
| info_graphic_png (最新) | `sprint12-yhsh-info_graphic_png.png` | 410 KB | 08-14 22:04 |
| plain_text (最新) | `3I9-kJS0cpf4Yz-7KgqaAg-hermes-output.txt` | 1.6 KB | 08-14 22:04 |
| info_graphic_png (最大) | `R2z89OizZVoBCMpdfqGbdg-info_graphic_png.png` | 2.4 MB | 06-14 19:37 |
| report_png (最大) | `tongye-brief-2026-08-06.png` | 898 KB | 08-06 13:28 |

**格式分布** (142 个非 log 文件): markdown/md 38 · pdf 23 · info_graphic_png 21 · html_landing 15 · report_png 5 · 其他 out/txt/html 等。

## 维护约定

- 新增分享产物 → 只会写进 `data/artifacts/` (worker 写死)。
- 根目录若再冒出 `output/` / `share_output/` / `infographic(s)/` 类目录 → 属异常, 应归档进 `~/Documents/moni-archive-*` 而非使用。
- 本文档随 sprint-15 A 路 artifacts 整肃落地; 后续目录结构变化需同步更新。
