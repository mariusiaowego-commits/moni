# 信源图标风格与生成提示词

所有 `moni/web/static/icons/sources/*.png` 共用一套视觉：**暖炭色圆角底板 + 细线徽章**。
新增信源时照这份说明生成，生成后必须跑一次底板/圆角归一，否则浅色主题下会是一块死黑。

## 规格

| 项 | 值 |
|---|---|
| 画布 | 256×256 PNG，RGBA |
| 底板 | 圆角矩形，radius 46px（占边长 18%），颜色 `#2E2A24` |
| 圆角外 | 全透明（alpha=0） |
| 图形 | 居中，占画布 55–65%，2–3px 线宽的描边风格 |
| 配色 | 青绿 `#1C8487` / 暖金 `#C9A227` / 少量品牌色；不超过 3 色 |
| 明暗 | 一层很浅的顶光斜面，不加投影、不加发光、不加渐变背景 |
| 文字 | 只在品牌本身就是字标时使用（如 36 氪的「36」）；不写中文名 |

## 生成提示词模板

```text
A minimal app icon on a warm charcoal (#2E2A24) rounded-square tile, 256x256.
Centered emblem: <一句话描述与信源强相关的意象/词标>.
Style: thin-line pictogram, 2-3px stroke, subtle bevel highlight on the top edge,
flat matte finish, no glow, no gradient background, no drop shadow, no text labels.
Palette: teal #1C8487, warm gold #C9A227, and at most one brand accent color.
The emblem fills 55-65% of the tile and is optically centered; the tile's four
corners are rounded with an 18% radius. Plain background, no mockup, no perspective.
```

意象选择：优先「行业符号」而不是产品名首字母——银行用柱廊/硬币，博客用书页/信号弧，
榜单用星标/奖章，播客用话筒/声波。词标只在品牌本身是字标时使用。

## 生成后处理（必做）

1. 把生成图裁成正方形、缩到 256×256。
2. 用 radius 46 的圆角遮罩把四角置为透明。
3. 把「中性暗色」像素（luma < 45 且 RGB 三通道差 < 14）统一改成 `#2E2A24`——
   生成图常带 `#000`，直接上线在浅色主题下过黑。
4. 用 24px 尺寸在浅色/深色主题各看一眼，确认线宽没有糊掉。

参考实现：`scripts/` 之外的一次性脚本不进仓库；规则以上面三条为准。
