# Info Graphic PNG

生成一张信息图 PNG，展示文章的核心内容。

## 要求

1. **必须调用 image_generate 工具生成 PNG 图片**
2. 使用加载的 skill（如 baoyu-infographic 或 kami）来设计信息图
3. 信息图应包含：
   - 文章标题
   - 核心要点（3-5 个关键信息）
   - 数据可视化（如有数字/statistics）
   - 简洁的结论或洞察
4. 输出语言跟随原文（中文文章用中文，英文文章用英文）
5. 图片尺寸：1200x630（landscape）

## 重要

- 不要输出纯文本分析，必须生成 PNG 图片
- 调用 image_generate 工具后，输出图片路径
- 如果 skill 无法使用，直接用 image_generate 工具生成信息图
