#!/usr/bin/env python3
"""PDF 生成脚本 — 将 Markdown 内容转换为 PDF 文件。

使用 reportlab 生成 PDF，支持中文（需要系统字体）。
视觉风格参考 BestBlogs.dev。

Usage:
    python3 scripts/generate_pdf.py <input.md> <output.pdf> [title]

Arguments:
    input.md   - Markdown 内容文件路径
    output.pdf - 输出 PDF 文件路径
    title      - 可选，报告总标题（默认"同业追踪日报"）
"""

import sys
import re
from pathlib import Path
from datetime import datetime

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm, cm
    from reportlab.lib.colors import HexColor, white, black
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
except ImportError:
    print("ERROR: reportlab not installed. Run: pip install reportlab")
    sys.exit(1)


# 颜色定义（BestBlogs.dev 风格）
COLOR_PRIMARY = HexColor("#1a365d")      # 深蓝
COLOR_TEXT = HexColor("#2a3447")          # 深灰
COLOR_MUTED = HexColor("#6b7280")        # 辅助灰
COLOR_BORDER = HexColor("#e5e1d8")       # 边框
COLOR_ACCENT = HexColor("#d97706")       # 橙色强调
COLOR_ACCENT_LIGHT = HexColor("#fef3c7") # 浅橙背景
COLOR_TAG_BG = HexColor("#f3f4f6")       # tag 背景
COLOR_WHITE = white
COLOR_LIGHT_BG = HexColor("#f7f4ee")     # 浅米色背景


def register_fonts():
    """注册中文字体（尝试多个路径）"""
    font_paths = [
        # macOS
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        # Linux
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    
    for font_path in font_paths:
        if Path(font_path).exists():
            try:
                pdfmetrics.registerFont(TTFont("Chinese", font_path))
                return "Chinese"
            except Exception:
                continue
    
    return "Helvetica"


def create_styles(font_name):
    """创建段落样式"""
    styles = getSampleStyleSheet()
    
    # 页面主标题
    styles.add(ParagraphStyle(
        name='ReportTitle',
        fontName=font_name,
        fontSize=28,
        leading=36,
        alignment=TA_CENTER,
        textColor=COLOR_WHITE,
        spaceAfter=8,
    ))
    
    # 日期副标题
    styles.add(ParagraphStyle(
        name='DateLine',
        fontName=font_name,
        fontSize=14,
        leading=20,
        alignment=TA_CENTER,
        textColor=COLOR_ACCENT_LIGHT,
        spaceAfter=10,
    ))
    
    # 文章序号（橙色大数字）
    styles.add(ParagraphStyle(
        name='ArticleNumber',
        fontName=font_name,
        fontSize=32,
        leading=40,
        textColor=COLOR_ACCENT,
        spaceAfter=4,
    ))
    
    # 文章标题（醒目）
    styles.add(ParagraphStyle(
        name='ArticleTitle',
        fontName=font_name,
        fontSize=18,
        leading=26,
        textColor=COLOR_PRIMARY,
        spaceBefore=0,
        spaceAfter=6,
    ))
    
    # 文章来源
    styles.add(ParagraphStyle(
        name='ArticleSource',
        fontName=font_name,
        fontSize=10,
        leading=14,
        textColor=COLOR_MUTED,
        spaceAfter=12,
    ))
    
    # 区块标题（如 "AI 摘要"、"事实核查"）
    styles.add(ParagraphStyle(
        name='SectionLabel',
        fontName=font_name,
        fontSize=11,
        leading=16,
        textColor=COLOR_ACCENT,
        spaceBefore=12,
        spaceAfter=6,
    ))
    
    # 正文
    styles.add(ParagraphStyle(
        name='ArticleBody',
        fontName=font_name,
        fontSize=11,
        leading=18,
        textColor=COLOR_TEXT,
        spaceAfter=8,
    ))
    
    # 列表项
    styles.add(ParagraphStyle(
        name='ListItem',
        fontName=font_name,
        fontSize=11,
        leading=18,
        textColor=COLOR_TEXT,
        leftIndent=15,
        spaceAfter=4,
    ))
    
    # 引用块
    styles.add(ParagraphStyle(
        name='Quote',
        fontName=font_name,
        fontSize=11,
        leading=18,
        textColor=COLOR_MUTED,
        leftIndent=20,
        borderPadding=12,
        borderColor=COLOR_ACCENT,
        borderWidth=3,
        spaceAfter=12,
    ))
    
    # Tag 标签
    styles.add(ParagraphStyle(
        name='TagText',
        fontName=font_name,
        fontSize=9,
        leading=12,
        textColor=COLOR_MUTED,
    ))
    
    return styles


def parse_markdown(md_text):
    """解析 Markdown 文本为段落列表"""
    lines = md_text.split('\n')
    paragraphs = []
    current_para = []
    
    for line in lines:
        line = line.rstrip()
        
        if not line:
            if current_para:
                paragraphs.append(('body', ' '.join(current_para)))
                current_para = []
            continue
        
        # 标题
        if line.startswith('### '):
            if current_para:
                paragraphs.append(('body', ' '.join(current_para)))
                current_para = []
            paragraphs.append(('h3', line[4:]))
        elif line.startswith('## '):
            if current_para:
                paragraphs.append(('body', ' '.join(current_para)))
                current_para = []
            paragraphs.append(('h2', line[3:]))
        elif line.startswith('# '):
            if current_para:
                paragraphs.append(('body', ' '.join(current_para)))
                current_para = []
            paragraphs.append(('h1', line[2:]))
        # 分隔线
        elif line.startswith('---'):
            if current_para:
                paragraphs.append(('body', ' '.join(current_para)))
                current_para = []
            paragraphs.append(('hr', ''))
        # 列表项
        elif line.startswith('- ') or line.startswith('* '):
            if current_para:
                paragraphs.append(('body', ' '.join(current_para)))
                current_para = []
            paragraphs.append(('li', line[2:]))
        # 引用
        elif line.startswith('> '):
            if current_para:
                paragraphs.append(('body', ' '.join(current_para)))
                current_para = []
            paragraphs.append(('quote', line[2:]))
        # 普通文本
        else:
            current_para.append(line)
    
    if current_para:
        paragraphs.append(('body', ' '.join(current_para)))
    
    return paragraphs


def format_text(text):
    """将 Markdown 内联格式转换为 reportlab XML"""
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    text = re.sub(r'`(.*?)`', r'<font face="Courier" size="10">\1</font>', text)
    text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
    return text


def make_tag_pill(tag_text, styles):
    """创建 tag 标签（圆角矩形样式）"""
    tag_data = [[Paragraph(tag_text, styles['TagText'])]]
    tag_table = Table(tag_data, colWidths=[None])
    tag_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), COLOR_TAG_BG),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('ROUNDEDCORNERS', [3, 3, 3, 3]),
    ]))
    return tag_table


def build_pdf(md_text, output_path, title="同业追踪日报"):
    """构建 PDF 文件"""
    font_name = register_fonts()
    styles = create_styles(font_name)
    
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=2*cm,
        rightMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm,
    )
    
    story = []
    
    # === 标题区（大色块背景）===
    today = datetime.now().strftime("%Y年%m月%d日")
    title_data = [
        [Paragraph(title, styles['ReportTitle'])],
        [Paragraph(today, styles['DateLine'])],
    ]
    title_table = Table(title_data, colWidths=[doc.width])
    title_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), COLOR_PRIMARY),
        ('TOPPADDING', (0, 0), (0, 0), 30),
        ('BOTTOMPADDING', (-1, -1), (-1, -1), 25),
        ('LEFTPADDING', (0, 0), (-1, -1), 20),
        ('RIGHTPADDING', (0, 0), (-1, -1), 20),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(title_table)
    story.append(Spacer(1, 25))
    
    # === 解析并渲染文章 ===
    paragraphs = parse_markdown(md_text)
    article_count = 0
    
    i = 0
    while i < len(paragraphs):
        para_type, content = paragraphs[i]
        
        if para_type == 'h1':
            # 页面主标题（跳过，我们已经渲染了）
            pass
        
        elif para_type == 'h2':
            # 文章标题 — 带橙色序号
            article_count += 1
            
            # 序号 + 标题组合
            num_str = f"{article_count:02d}"
            
            # 查找来源信息（下一行可能是来源）
            source_text = ""
            if i + 1 < len(paragraphs) and paragraphs[i+1][0] == 'body':
                body = paragraphs[i+1][1]
                if '**来源**' in body or '来源' in body:
                    source_text = body
            
            # 序号
            story.append(Paragraph(num_str, styles['ArticleNumber']))
            
            # 标题
            story.append(Paragraph(format_text(content), styles['ArticleTitle']))
            
            # 来源
            if source_text:
                story.append(Paragraph(format_text(source_text), styles['ArticleSource']))
                i += 1  # 跳过来源行
            
            # 分隔线
            story.append(HRFlowable(width="100%", thickness=1, color=COLOR_BORDER, spaceAfter=10))
        
        elif para_type == 'h3':
            # 区块标题（AI 摘要、事实核查、标签）
            label_text = content.strip()
            if '标签' in label_text:
                # Tag 标签特殊处理 — 下一行是 tag 列表
                if i + 1 < len(paragraphs):
                    i += 1
                    _, tags_content = paragraphs[i]
                    tags = [t.strip() for t in tags_content.split(',')]
                    
                    # 创建 tag pills 横排
                    tag_row = []
                    for tag in tags[:6]:  # 最多 6 个
                        tag_row.append(make_tag_pill(tag, styles))
                        tag_row.append(Spacer(6, 0))
                    
                    if tag_row:
                        # 用 Table 包装 tag 行
                        tag_table_data = [tag_row]
                        tag_table = Table(tag_table_data, colWidths=[doc.width])
                        tag_table.setStyle(TableStyle([
                            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                        ]))
                        story.append(Spacer(1, 8))
                        story.append(tag_table)
                        story.append(Spacer(1, 12))
            else:
                story.append(Paragraph(f"▎{label_text}", styles['SectionLabel']))
        
        elif para_type == 'body':
            if content.strip():
                story.append(Paragraph(format_text(content), styles['ArticleBody']))
        
        elif para_type == 'li':
            story.append(Paragraph(f"•  {format_text(content)}", styles['ListItem']))
        
        elif para_type == 'quote':
            story.append(Paragraph(format_text(content), styles['Quote']))
        
        elif para_type == 'hr':
            story.append(Spacer(1, 15))
            story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_BORDER))
            story.append(Spacer(1, 15))
        
        i += 1
    
    # 生成 PDF
    doc.build(story)
    print(f"PDF generated: {output_path}")


def main():
    if len(sys.argv) < 3:
        print("Usage: generate_pdf.py <input.md> <output.pdf> [title]")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_path = sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else "同业追踪日报"
    
    md_text = Path(input_path).read_text(encoding='utf-8')
    build_pdf(md_text, output_path, title)


if __name__ == "__main__":
    main()
