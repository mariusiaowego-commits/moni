#!/usr/bin/env python3
"""PDF 生成脚本 — 将 Markdown 内容转换为 PDF 文件。

使用 reportlab 生成 PDF，支持中文（需要系统字体）。
视觉风格参考 BestBlogs.dev。

配置文件：moni/share/templates/pdf-style.yaml

Usage:
    python3 scripts/generate_pdf.py <input.md> <output.pdf> [title]
"""

import sys
import re
from pathlib import Path
from datetime import datetime

try:
    import yaml
except ImportError:
    yaml = None

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


def load_config():
    """加载版式配置，优先使用 yaml 文件，fallback 到默认值"""
    defaults = {
        'colors': {
            'primary': '#1a365d', 'accent': '#d97706', 'accent_light': '#fef3c7',
            # v1.0.15: soft wash for summary / quote blocks (fill, not stroke)
            'summary_bg': '#fff4e6',
            'text': '#2a3447', 'muted': '#6b7280', 'border': '#e5e1d8',
            'tag_bg': '#f3f4f6', 'white': '#ffffff', 'page_bg': '#fffcf7',
        },
        'header': {'title_font_size': 26, 'date_font_size': 12, 'padding_top': 22, 'padding_bottom': 18},
        'article': {
            'number_font_size': 28, 'title_font_size': 15, 'title_space_after': 4,
            'source_font_size': 9, 'body_font_size': 10.5, 'body_line_height': 16,
            'section_label_prefix': '▎',
        },
        'tag': {'font_size': 9, 'padding': 3, 'radius': 3, 'max_display': 6},
        'list': {'bullet': '•', 'indent': 14},
        # v1.0.15: quote/summary = soft fill block, no heavy border frame
        'quote': {
            'border_width': 0,
            'indent': 0,
            'padding': 10,
            'radius': 4,
            'space_before': 4,
            'space_after': 10,
        },
        'divider': {'thickness': 0.5, 'space_before': 12, 'space_after': 12},
    }
    
    # 尝试加载 yaml
    for config_path in [
        Path(__file__).parent.parent / 'moni' / 'share' / 'templates' / 'pdf-style.yaml',
        Path.cwd() / 'moni' / 'share' / 'templates' / 'pdf-style.yaml',
    ]:
        if config_path.exists() and yaml:
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    user = yaml.safe_load(f) or {}
                for key in defaults:
                    if key in user and isinstance(defaults[key], dict):
                        defaults[key].update(user[key])
                    elif key in user:
                        defaults[key] = user[key]
                return defaults
            except Exception as e:
                print(f"Warning: Failed to load {config_path}: {e}")
    
    return defaults


def c(config, path, fallback=None):
    """从配置中获取颜色值，自动转 HexColor"""
    keys = path.split('.')
    val = config
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k, fallback)
        else:
            return fallback
    if isinstance(val, str) and val.startswith('#'):
        return HexColor(val)
    return val


def register_fonts():
    """注册中文字体"""
    for font_path in [
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]:
        if Path(font_path).exists():
            try:
                pdfmetrics.registerFont(TTFont("Chinese", font_path))
                return "Chinese"
            except Exception:
                continue
    return "Helvetica"


def create_styles(font_name, cfg):
    """创建段落样式"""
    styles = getSampleStyleSheet()
    
    styles.add(ParagraphStyle(
        name='ReportTitle', fontName=font_name,
        fontSize=cfg['header']['title_font_size'],
        leading=cfg['header']['title_font_size'] + 8,
        alignment=TA_CENTER, textColor=c(cfg, 'colors.white'), spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name='DateLine', fontName=font_name,
        fontSize=cfg['header']['date_font_size'],
        leading=cfg['header']['date_font_size'] + 6,
        alignment=TA_CENTER, textColor=c(cfg, 'colors.accent_light'), spaceAfter=10,
    ))
    styles.add(ParagraphStyle(
        name='ArticleNumber', fontName=font_name,
        fontSize=cfg['article']['number_font_size'],
        leading=cfg['article']['number_font_size'] + 8,
        textColor=c(cfg, 'colors.accent'), spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name='ArticleTitle', fontName=font_name,
        fontSize=cfg['article']['title_font_size'],
        leading=cfg['article']['title_font_size'] + 8,
        textColor=c(cfg, 'colors.primary'),
        spaceBefore=0, spaceAfter=cfg['article']['title_space_after'],
    ))
    styles.add(ParagraphStyle(
        name='ArticleSource', fontName=font_name,
        fontSize=cfg['article']['source_font_size'],
        leading=cfg['article']['source_font_size'] + 4,
        textColor=c(cfg, 'colors.muted'), spaceAfter=12,
    ))
    styles.add(ParagraphStyle(
        name='SectionLabel', fontName=font_name,
        fontSize=11, leading=16,
        textColor=c(cfg, 'colors.accent'), spaceBefore=12, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name='ArticleBody', fontName=font_name,
        fontSize=cfg['article']['body_font_size'],
        leading=cfg['article']['body_line_height'],
        textColor=c(cfg, 'colors.text'), spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name='ListItem', fontName=font_name,
        fontSize=cfg['article']['body_font_size'],
        leading=cfg['article']['body_line_height'],
        textColor=c(cfg, 'colors.text'),
        leftIndent=cfg['list']['indent'], spaceAfter=4,
    ))
    # Inner text for soft summary/quote wash (no Paragraph border — Table fills bg)
    styles.add(ParagraphStyle(
        name='QuoteInner', fontName=font_name,
        fontSize=cfg['article']['body_font_size'],
        leading=cfg['article']['body_line_height'],
        textColor=c(cfg, 'colors.text'),
        spaceAfter=0,
    ))
    # Legacy name kept for any external callers
    styles.add(ParagraphStyle(
        name='Quote', fontName=font_name,
        fontSize=cfg['article']['body_font_size'],
        leading=cfg['article']['body_line_height'],
        textColor=c(cfg, 'colors.text'),
        spaceAfter=0,
    ))
    styles.add(ParagraphStyle(
        name='SectionH2', fontName=font_name,
        fontSize=16, leading=22,
        textColor=c(cfg, 'colors.primary'),
        spaceBefore=6, spaceAfter=10,
    ))
    styles.add(ParagraphStyle(
        name='MetaLine', fontName=font_name,
        fontSize=cfg['article']['source_font_size'],
        leading=cfg['article']['source_font_size'] + 5,
        textColor=c(cfg, 'colors.muted'), spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name='TagText', fontName=font_name,
        fontSize=cfg['tag']['font_size'],
        leading=cfg['tag']['font_size'] + 3,
        textColor=c(cfg, 'colors.muted'),
    ))
    styles.add(ParagraphStyle(
        name='DeckLine', fontName=font_name,
        fontSize=10, leading=15,
        textColor=c(cfg, 'colors.muted'),
        alignment=TA_CENTER, spaceAfter=4,
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
        elif line.startswith('---'):
            if current_para:
                paragraphs.append(('body', ' '.join(current_para)))
                current_para = []
            paragraphs.append(('hr', ''))
        elif line.startswith('- ') or line.startswith('* '):
            if current_para:
                paragraphs.append(('body', ' '.join(current_para)))
                current_para = []
            paragraphs.append(('li', line[2:]))
        elif line.startswith('> '):
            if current_para:
                paragraphs.append(('body', ' '.join(current_para)))
                current_para = []
            paragraphs.append(('quote', line[2:]))
        else:
            current_para.append(line)
    
    if current_para:
        paragraphs.append(('body', ' '.join(current_para)))
    return paragraphs


def format_text(text):
    """Markdown 内联格式 → reportlab XML"""
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    text = re.sub(r'`(.*?)`', r'<font face="Courier" size="10">\1</font>', text)
    text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
    return text


def make_tag_pill(tag_text, styles, cfg):
    """创建 tag 标签（圆角矩形）"""
    tag_data = [[Paragraph(tag_text, styles['TagText'])]]
    tag_table = Table(tag_data, colWidths=[None])
    tag_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), c(cfg, 'colors.tag_bg')),
        ('TOPPADDING', (0, 0), (-1, -1), cfg['tag']['padding']),
        ('BOTTOMPADDING', (0, 0), (-1, -1), cfg['tag']['padding']),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('ROUNDEDCORNERS', [cfg['tag']['radius']] * 4),
    ]))
    return tag_table


def make_summary_block(text: str, styles, cfg, width: float):
    """v1.0.15: soft orange wash for 摘要 / blockquote — fill only, no frame.

    Previous Quote ParagraphStyle used borderWidth=3 + accent stroke → ugly
    yellow/orange box. User wants a light orange background chip instead.
    """
    body = (text or "").strip() or "（无摘要）"
    pad = int(cfg.get('quote', {}).get('padding', 10))
    radius = int(cfg.get('quote', {}).get('radius', 4))
    bg = c(cfg, 'colors.summary_bg') or c(cfg, 'colors.accent_light')
    inner = Paragraph(format_text(body), styles['QuoteInner'])
    tbl = Table([[inner]], colWidths=[width])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), bg),
        ('TOPPADDING', (0, 0), (-1, -1), pad),
        ('BOTTOMPADDING', (0, 0), (-1, -1), pad),
        ('LEFTPADDING', (0, 0), (-1, -1), pad + 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), pad + 2),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROUNDEDCORNERS', [radius] * 4),
        # Explicitly no BOX / LINEBELOW — fill only
    ]))
    return tbl


def _is_section_heading(text: str) -> bool:
    """Headings that must not get the orange article index (01/02…)."""
    t = (text or "").strip()
    if not t:
        return False
    # 优先关注 / 目录 / 导读 etc.
    keys = ("优先关注", "今日要点", "目录", "导读", "摘要总览", "Highlights", "TL;DR")
    return any(k in t for k in keys) and len(t) < 40


def _is_meta_body(text: str) -> bool:
    t = (text or "").strip()
    return ("来源" in t) or t.startswith("from moni") or ("质量" in t and "业务价值" in t)


def build_pdf(md_text, output_path, title="同业追踪日报"):
    """构建 PDF 文件"""
    cfg = load_config()
    font_name = register_fonts()
    styles = create_styles(font_name, cfg)
    
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.6*cm, bottomMargin=1.6*cm,
    )
    
    story = []
    today = datetime.now().strftime("%Y年%m月%d日")
    
    # === 标题区 ===
    title_data = [
        [Paragraph(title, styles['ReportTitle'])],
        [Paragraph(today, styles['DateLine'])],
    ]
    title_table = Table(title_data, colWidths=[doc.width])
    title_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), c(cfg, 'colors.primary')),
        ('TOPPADDING', (0, 0), (0, 0), cfg['header']['padding_top']),
        ('BOTTOMPADDING', (-1, -1), (-1, -1), cfg['header']['padding_bottom']),
        ('LEFTPADDING', (0, 0), (-1, -1), 20),
        ('RIGHTPADDING', (0, 0), (-1, -1), 20),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(title_table)
    story.append(Spacer(1, 18))
    
    # === 文章 ===
    paragraphs = parse_markdown(md_text)
    article_count = 0
    prefix = cfg['article'].get('section_label_prefix', '▎')
    q_before = float(cfg.get('quote', {}).get('space_before', 4))
    q_after = float(cfg.get('quote', {}).get('space_after', 10))
    
    i = 0
    while i < len(paragraphs):
        para_type, content = paragraphs[i]
        
        if para_type == 'h1':
            pass  # 跳过，已渲染标题区
        
        elif para_type == 'h2':
            heading = content.strip()
            # v1.0.15: "优先关注" is a section, not article #01
            if _is_section_heading(heading):
                story.append(Paragraph(format_text(heading), styles['SectionH2']))
                story.append(HRFlowable(
                    width="100%", thickness=1, color=c(cfg, 'colors.border'), spaceAfter=8,
                ))
            else:
                article_count += 1
                num_str = f"{article_count:02d}"

                # 查找来源 / 元信息行
                source_text = ""
                if i + 1 < len(paragraphs) and paragraphs[i + 1][0] == 'body':
                    body = paragraphs[i + 1][1]
                    if _is_meta_body(body):
                        source_text = body

                story.append(Paragraph(num_str, styles['ArticleNumber']))
                story.append(Paragraph(format_text(heading), styles['ArticleTitle']))
                if source_text:
                    story.append(Paragraph(format_text(source_text), styles['MetaLine']))
                    i += 1
                story.append(HRFlowable(
                    width="100%", thickness=0.6, color=c(cfg, 'colors.border'), spaceAfter=8,
                ))
        
        elif para_type == 'h3':
            label_text = content.strip()
            if '标签' in label_text:
                # v1.0.5.25: Skip tags at end of document (LLM spurious output)
                if i + 1 < len(paragraphs) and i + 2 < len(paragraphs):
                    i += 1
                    _, tags_content = paragraphs[i]
                    tags = [t.strip() for t in tags_content.split(',')][:cfg['tag']['max_display']]
                    tag_row = []
                    for tag in tags:
                        tag_row.append(make_tag_pill(tag, styles, cfg))
                        tag_row.append(Spacer(6, 0))
                    if tag_row:
                        tag_table = Table([tag_row], colWidths=[doc.width])
                        tag_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
                        story.append(Spacer(1, 8))
                        story.append(tag_table)
                        story.append(Spacer(1, 12))
            else:
                story.append(Paragraph(f"{prefix}{label_text}", styles['SectionLabel']))
        
        elif para_type == 'body':
            text = content.strip()
            if not text:
                pass
            elif text.startswith("from moni"):
                story.append(Paragraph(format_text(text), styles['DeckLine']))
            elif re.match(r'^\*?\*?摘要\*?\*?\s*[:：]', text):
                # "**摘要**:" alone as body — label only; quote follows
                story.append(Paragraph(f"{prefix}摘要", styles['SectionLabel']))
            elif re.match(r'^\*?\*?事实核查\*?\*?\s*[:：]', text):
                story.append(Paragraph(f"{prefix}事实核查", styles['SectionLabel']))
            elif _is_meta_body(text):
                story.append(Paragraph(format_text(text), styles['MetaLine']))
            else:
                story.append(Paragraph(format_text(text), styles['ArticleBody']))
        
        elif para_type == 'li':
            bullet = cfg['list']['bullet']
            story.append(Paragraph(f"{bullet}  {format_text(content)}", styles['ListItem']))
        
        elif para_type == 'quote':
            # Merge consecutive > lines into one soft block
            chunks = [content]
            while i + 1 < len(paragraphs) and paragraphs[i + 1][0] == 'quote':
                i += 1
                chunks.append(paragraphs[i][1])
            merged = " ".join(c.strip() for c in chunks if c and c.strip())
            story.append(Spacer(1, q_before))
            story.append(make_summary_block(merged, styles, cfg, doc.width))
            story.append(Spacer(1, q_after))
        
        elif para_type == 'hr':
            story.append(Spacer(1, cfg['divider']['space_before']))
            story.append(HRFlowable(
                width="100%", thickness=cfg['divider']['thickness'],
                color=c(cfg, 'colors.border'),
            ))
            story.append(Spacer(1, cfg['divider']['space_after']))
        
        i += 1
    
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
