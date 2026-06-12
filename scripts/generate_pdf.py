#!/usr/bin/env python3
"""PDF 生成脚本 — 将 Markdown 内容转换为 PDF 文件。

使用 reportlab 生成 PDF，支持中文（需要系统字体）。

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

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm, cm
    from reportlab.lib.colors import HexColor, white, black
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
except ImportError:
    print("ERROR: reportlab not installed. Run: pip install reportlab")
    sys.exit(1)


# 颜色定义（参考 BestBlogs.dev 风格）
COLOR_BG = HexColor("#f7f4ee")
COLOR_PRIMARY = HexColor("#1a365d")
COLOR_TEXT = HexColor("#2a3447")
COLOR_MUTED = HexColor("#6b7280")
COLOR_BORDER = HexColor("#e5e1d8")
COLOR_ACCENT = HexColor("#d97706")
COLOR_WHITE = white


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
    
    # 如果没有找到中文字体，使用默认字体
    return "Helvetica"


def create_styles(font_name):
    """创建段落样式"""
    styles = getSampleStyleSheet()
    
    # 标题样式
    styles.add(ParagraphStyle(
        name='ReportTitle',
        fontName=font_name,
        fontSize=28,
        leading=36,
        alignment=TA_CENTER,
        textColor=COLOR_WHITE,
        spaceAfter=20,
    ))
    
    # 日期样式
    styles.add(ParagraphStyle(
        name='DateLine',
        fontName=font_name,
        fontSize=12,
        leading=16,
        alignment=TA_CENTER,
        textColor=HexColor("#fef3c7"),
        spaceAfter=10,
    ))
    
    # 文章标题
    styles.add(ParagraphStyle(
        name='ArticleTitle',
        fontName=font_name,
        fontSize=18,
        leading=24,
        textColor=COLOR_PRIMARY,
        spaceBefore=20,
        spaceAfter=10,
    ))
    
    # 文章来源
    styles.add(ParagraphStyle(
        name='ArticleSource',
        fontName=font_name,
        fontSize=10,
        leading=14,
        textColor=COLOR_MUTED,
        spaceAfter=15,
    ))
    
    # 正文（使用自定义名称避免冲突）
    styles.add(ParagraphStyle(
        name='ArticleBody',
        fontName=font_name,
        fontSize=11,
        leading=18,
        textColor=COLOR_TEXT,
        spaceAfter=8,
    ))
    
    # 二级标题
    styles.add(ParagraphStyle(
        name='ArticleH2',
        fontName=font_name,
        fontSize=14,
        leading=20,
        textColor=COLOR_PRIMARY,
        spaceBefore=15,
        spaceAfter=8,
    ))
    
    # 三级标题
    styles.add(ParagraphStyle(
        name='ArticleH3',
        fontName=font_name,
        fontSize=12,
        leading=18,
        textColor=COLOR_PRIMARY,
        spaceBefore=12,
        spaceAfter=6,
    ))
    
    # 列表项
    styles.add(ParagraphStyle(
        name='ListItem',
        fontName=font_name,
        fontSize=11,
        leading=18,
        textColor=COLOR_TEXT,
        leftIndent=20,
        spaceAfter=4,
    ))
    
    # 引用块
    styles.add(ParagraphStyle(
        name='Quote',
        fontName=font_name,
        fontSize=11,
        leading=18,
        textColor=COLOR_MUTED,
        leftIndent=15,
        borderPadding=10,
        borderColor=COLOR_ACCENT,
        borderWidth=2,
        spaceAfter=10,
    ))
    
    return styles


def parse_markdown(md_text):
    """解析 Markdown 文本为段落列表"""
    lines = md_text.split('\n')
    paragraphs = []
    current_para = []
    
    for line in lines:
        line = line.rstrip()
        
        # 空行表示段落结束
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
    
    # 处理最后一段
    if current_para:
        paragraphs.append(('body', ' '.join(current_para)))
    
    return paragraphs


def format_text(text):
    """将 Markdown 内联格式转换为 reportlab XML"""
    # 粗体
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    # 斜体
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    # 行内代码
    text = re.sub(r'`(.*?)`', r'<font face="Courier">\1</font>', text)
    # 链接（只保留文本）
    text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
    return text


def build_pdf(md_text, output_path, title="同业追踪日报"):
    """构建 PDF 文件"""
    font_name = register_fonts()
    styles = create_styles(font_name)
    
    # 创建 PDF 文档
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=2*cm,
        rightMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm,
    )
    
    story = []
    
    # 标题区（大色块背景）
    # reportlab 不直接支持背景色，用表格模拟
    from reportlab.platypus import Table, TableStyle
    
    title_data = [[Paragraph(title, styles['ReportTitle'])]]
    title_table = Table(title_data, colWidths=[doc.width])
    title_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), COLOR_PRIMARY),
        ('TOPPADDING', (0, 0), (-1, -1), 30),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 30),
        ('LEFTPADDING', (0, 0), (-1, -1), 20),
        ('RIGHTPADDING', (0, 0), (-1, -1), 20),
    ]))
    story.append(title_table)
    story.append(Spacer(1, 20))
    
    # 解析 Markdown 内容
    paragraphs = parse_markdown(md_text)
    
    for para_type, content in paragraphs:
        if para_type == 'h1':
            # 一级标题（文章标题）
            story.append(Paragraph(format_text(content), styles['ArticleTitle']))
            story.append(HRFlowable(width="100%", thickness=1, color=COLOR_BORDER))
            story.append(Spacer(1, 10))
        elif para_type == 'h2':
            story.append(Paragraph(format_text(content), styles['ArticleH2']))
        elif para_type == 'h3':
            story.append(Paragraph(format_text(content), styles['ArticleH3']))
        elif para_type == 'body':
            if content.strip():
                story.append(Paragraph(format_text(content), styles['ArticleBody']))
        elif para_type == 'li':
            story.append(Paragraph(f"• {format_text(content)}", styles['ListItem']))
        elif para_type == 'quote':
            story.append(Paragraph(format_text(content), styles['Quote']))
        elif para_type == 'hr':
            story.append(Spacer(1, 10))
            story.append(HRFlowable(width="100%", thickness=1, color=COLOR_BORDER))
            story.append(Spacer(1, 10))
    
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
    
    # 读取 Markdown 内容
    md_text = Path(input_path).read_text(encoding='utf-8')
    
    # 生成 PDF
    build_pdf(md_text, output_path, title)


if __name__ == "__main__":
    main()
