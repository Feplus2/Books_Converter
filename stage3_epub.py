"""
Stage 3: EPUB 生成

根据 MinerU 的结构化内容 + DeepSeek 的语义分析结果，
用 ebooklib 生成格式正确的 EPUB 文件。
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

from ebooklib import epub
from PIL import Image


# ── 异常换行修复 ─────────────────────────────────────────────────
_SENTENCE_END = re.compile(r'[。！？…～」』）\)】""\']\s*$')
_BROKEN_P = re.compile(
    r'<p>([^<]*?)</p>\s*\n?\s*<p>([^<]*?)</p>',
    re.DOTALL,
)


def _merge_broken_paragraphs(html: str) -> str:
    """合并因跨页扫描而异常断裂的段落。

    如果 </p> 前的最后一个非空白字符不是句末标点，
    则将下一个 <p> 合并进来，直到遇到真正的段落结束。
    """
    prev = None
    while prev != html:
        prev = html
        html = _BROKEN_P.sub(_merge_if_broken, html)
    return html


def _merge_if_broken(m: re.Match) -> str:
    p1 = m.group(1).strip()
    p2 = m.group(2).strip()
    if not p2:
        return m.group(0)
    # 第一段不以句末标点结尾 → 合并（原始逻辑）
    if not _SENTENCE_END.search(p1) and not _looks_like_heading(p1):
        return f"<p>{p1}{p2}</p>"
    # 第二段极短（扫描跨页碎片，如"的方式不同。"）→ 合并
    if len(p2) <= 30 and not _looks_like_heading(p2):
        return f"<p>{p1}{p2}</p>"
    return m.group(0)


def _looks_like_heading(text: str) -> bool:
    """是否为疑似标题行（不应合并）"""
    t = text.strip()
    if len(t) <= 25 and not t.endswith("。"):
        return True
    return False


# ── 章节标题去重 ─────────────────────────────────────────────────
_CHAPTER_PREFIX = re.compile(
    r'^第[一二三四五六七八九十百千零\d]+[章节篇部节]\s*'
)


def _chapter_pure_name(title: str) -> str:
    """去除'第X章'前缀，返回纯章节名"""
    return _CHAPTER_PREFIX.sub('', title).strip()


def _strip_first_title_paragraph(ch_html: str, pure_name: str) -> str:
    """如果正文第一段恰好是章节名（与 DeepSeek 标题重复），直接去除"""
    if not pure_name or len(pure_name) < 2:
        return ch_html
    escaped = re.escape(pure_name)
    pattern = re.compile(rf'^\s*<p>{escaped}</p>\s*', re.DOTALL)
    return pattern.sub('', ch_html.strip(), count=1)

logger = logging.getLogger(__name__)

# EPUB 默认 CSS
DEFAULT_CSS = """
body {
    font-family: "Songti SC", "Noto Serif CJK SC", "STSong", serif;
    line-height: 1.8;
    margin: 2% 3%;
    color: #333;
}
h1 {
    text-align: center;
    font-size: 1.6em;
    margin: 2em 0 1em;
    font-weight: bold;
}
h2 {
    text-align: left;
    font-size: 1.3em;
    margin: 1.5em 0 0.8em;
    font-weight: bold;
}
h2.chapter-title {
    text-align: center;
    font-size: 1.3em;
    margin: 1.2em 0 0.8em;
    font-weight: bold;
}
h3 {
    text-align: left;
    font-size: 1.1em;
    margin: 1.2em 0 0.6em;
    font-weight: bold;
}
p {
    text-indent: 2em;
    margin: 0.3em 0;
}
p.no_indent {
    text-indent: 0;
}
img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 1em auto;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
}
table td, table th {
    border: 1px solid #ccc;
    padding: 4px 8px;
}
blockquote {
    margin: 1em 2em;
    color: #555;
}
"""


def _classify_blocks_by_page(content_list: list) -> dict:
    """将 content_list 按页码分组"""
    pages = {}
    for block in content_list:
        page = block.get("page_idx", 0) + 1  # 转1-based
        if page not in pages:
            pages[page] = []
        pages[page].append(block)
    return pages


def _render_block_to_html(block: dict, images_dir: str) -> str:
    """将单个 content block 渲染为 HTML"""
    btype = block.get("type", "")

    if btype in ("text",):
        level = block.get("text_level", 0)
        text = block.get("text", "")
        if not text.strip():
            return ""
        if level == 1:
            return f"<h1>{text}</h1>"
        elif level == 2:
            return f"<h2>{text}</h2>"
        elif level >= 3:
            return f"<h3>{text}</h3>"
        else:
            return f"<p>{text}</p>"

    elif btype == "title":
        level = block.get("text_level", 0) or 1
        text = block.get("text", "")
        if not text.strip():
            return ""
        return f"<h{min(level, 3)}>{text}</h{min(level, 3)}>"

    elif btype == "paragraph":
        text = block.get("text", "")
        if not text.strip():
            return ""
        return f"<p>{text}</p>"

    elif btype == "image":
        img_path = block.get("img_path", "") or block.get("image_path", "")
        caption = block.get("image_caption", "")
        if isinstance(caption, list):
            caption = " ".join(caption) if caption else ""
        # 尝试匹配 images_dir 中的文件名
        img_name = Path(img_path).name if img_path else ""
        html = ""
        if img_name:
            html = f'<img src="images/{img_name}" alt="{caption}"/>'
        if caption:
            html += f'<p class="no_indent"><small>{caption}</small></p>'
        return html

    elif btype == "table":
        body = block.get("table_body", "")
        caption = block.get("table_caption", "")
        if isinstance(caption, list):
            caption = " ".join(caption) if caption else ""
        html = ""
        if caption:
            html += f'<p class="no_indent"><strong>{caption}</strong></p>'
        html += body
        return html

    elif btype == "list":
        items = block.get("list_items", [])
        if not items:
            return ""
        items_html = "\n".join(f"<li>{item}</li>" for item in items)
        return f"<ul>{items_html}</ul>"

    elif btype == "equation":
        latex = block.get("latex", "") or block.get("text", "")
        return f"<p class=\"no_indent\"><code>{latex}</code></p>"

    elif btype == "code":
        code = block.get("code_body", "") or block.get("text", "")
        lang = block.get("code_language", "")
        lang_attr = f' class="language-{lang}"' if lang else ""
        return f"<pre><code{lang_attr}>{code}</code></pre>"

    elif btype in ("header", "footer", "page_number", "page_footnote", "aside_text"):
        # 跳过页眉页脚，由 DeepSeek 阶段标记的噪音控制
        return ""

    elif btype == "chart":
        return ""  # 图表跳过或作为图片处理

    else:
        text = block.get("text", "")
        if text.strip():
            return f"<p>{text}</p>"
        return ""


def _render_pages_html(
    page_start: int,
    page_end: int,
    pages: dict,
    images_dir: str,
    noise_pages: set,
) -> str:
    """渲染指定页码范围的内容为 HTML"""
    html_parts = []
    for pn in range(page_start, page_end + 1):
        if pn in noise_pages:
            continue
        blocks = pages.get(pn, [])
        for block in blocks:
            rendered = _render_block_to_html(block, images_dir)
            if rendered:
                html_parts.append(rendered)
    return "\n".join(html_parts)


def _sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def generate_epub(
    book_name: str,
    mineru_info: dict,
    structure: dict,
    output_dir: str,
) -> Path:
    """
    生成 EPUB 文件。

    Args:
        book_name: 书名（用于文件命名）
        mineru_info: Stage 1 输出 {markdown, content_list, images_dir}
        structure: Stage 2 输出 {metadata, front_matter, body, back_matter, noise_ranges}
        output_dir: 输出目录
    """
    logger.info("Stage 3 开始: 生成 EPUB")

    content_list = mineru_info.get("content_list", [])
    images_dir = mineru_info.get("images_dir", "")
    meta = structure.get("metadata", {})

    # 收集噪音页面——只跳过空白页
    # 页眉/页脚噪音由 _render_block_to_html 在 block 级别处理
    noise_pages = set()
    for noise in structure.get("noise_ranges", []):
        if noise.get("type") == "blank_page":
            for p in noise.get("pages", []):
                noise_pages.add(p)

    # 按页组织内容
    pages = _classify_blocks_by_page(content_list)

    # 创建 EPUB book
    book = epub.EpubBook()

    # 元数据
    title = meta.get("title") or book_name
    book.set_identifier(f"books-converter-{hash(title) & 0xFFFFFFFF:08x}")
    book.set_title(title)
    book.set_language(meta.get("language", "zh"))

    authors = meta.get("authors", [])
    if authors:
        for author in authors:
            book.add_author(author)

    translator = meta.get("translator")
    if translator and translator != "null":
        book.add_metadata("DC", "contributor", translator, {"role": "trl"})

    publisher = meta.get("publisher")
    if publisher and publisher != "null":
        book.add_metadata("DC", "publisher", publisher)

    # 添加 CSS
    css = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=DEFAULT_CSS,
    )
    book.add_item(css)

    # 添加图片资源
    image_items = []
    if images_dir and os.path.isdir(images_dir):
        for img_file in sorted(Path(images_dir).glob("*")):
            if img_file.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"):
                img_name = img_file.name
                try:
                    with open(img_file, "rb") as f:
                        img_data = f.read()
                    ext = img_file.suffix.lower().lstrip(".")
                    media_type = f"image/{'jpeg' if ext == 'jpg' else ext}"
                    item = epub.EpubItem(
                        uid=f"img_{img_name}",
                        file_name=f"images/{img_name}",
                        media_type=media_type,
                        content=img_data,
                    )
                    book.add_item(item)
                    image_items.append(item)
                except Exception as e:
                    logger.warning(f"  无法添加图片 {img_name}: {e}")

    # 生成章节
    spine = ["nav"]
    toc_entries = []

    # ── 前页 ──
    for i, fm in enumerate(structure.get("front_matter", [])):
        if not fm.get("keep", False):
            continue
        fm_html = _render_pages_html(
            fm.get("page_start", 0),
            fm.get("page_end", 0),
            pages,
            images_dir,
            noise_pages,
        )
        if not fm_html.strip():
            continue

        fm_type = fm.get("type", "front")
        fm_label = fm.get("label", fm_type)
        chapter = epub.EpubHtml(
            title=fm_label,
            file_name=f"front_{i}_{fm_type}.xhtml",
            lang="zh",
        )
        chapter.content = f"<h1>{fm_label}</h1>\n{fm_html}"
        chapter.add_item(css)
        book.add_item(chapter)
        spine.append(chapter)
        toc_entries.append(epub.Link(chapter.file_name, fm_label, f"fm_{i}"))

    # ── 正文 ──
    for i, ch in enumerate(structure.get("body", [])):
        ch_title = ch.get("title", f"章节 {i+1}")
        ch_level = ch.get("level", 1)
        ch_html = _render_pages_html(
            ch.get("page_start", 0),
            ch.get("page_end", 0),
            pages,
            images_dir,
            noise_pages,
        )
        if not ch_html.strip():
            ch_html = "<p>(空章节)</p>"

        # 后处理：合并异常断裂段落 + 章节名去重
        ch_html = _merge_broken_paragraphs(ch_html)
        pure_name = _chapter_pure_name(ch_title)
        ch_html = _strip_first_title_paragraph(ch_html, pure_name)

        # 章节文件
        heading_tag = f"h{min(ch_level, 3)}"
        safe_name = _sanitize_filename(ch_title) or f"chapter_{i+1}"
        file_name = f"chapter_{i+1:03d}.xhtml"

        chapter = epub.EpubHtml(
            title=ch_title,
            file_name=file_name,
            lang="zh",
        )
        chapter.content = f"<{heading_tag}>{ch_title}</{heading_tag}>\n{ch_html}"
        chapter.add_item(css)
        book.add_item(chapter)
        spine.append(chapter)
        toc_entries.append(epub.Link(file_name, ch_title, f"ch_{i}"))

    # ── 后页 ──
    for i, bm in enumerate(structure.get("back_matter", [])):
        bm_html = _render_pages_html(
            bm.get("page_start", 0),
            bm.get("page_end", 0),
            pages,
            images_dir,
            noise_pages,
        )
        if not bm_html.strip():
            continue

        bm_type = bm.get("type", "back")
        bm_label = bm.get("label", bm_type)
        chapter = epub.EpubHtml(
            title=bm_label,
            file_name=f"back_{i}_{bm_type}.xhtml",
            lang="zh",
        )
        chapter.content = f"<h1>{bm_label}</h1>\n{bm_html}"
        chapter.add_item(css)
        book.add_item(chapter)
        spine.append(chapter)
        toc_entries.append(epub.Link(chapter.file_name, bm_label, f"bm_{i}"))

    # ── 组装 ──
    book.toc = toc_entries
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # 写入文件
    epub_path = Path(output_dir) / f"{_sanitize_filename(title)}.epub"
    epub.write_epub(str(epub_path), book)

    logger.info(f"  EPUB 已生成: {epub_path} ({epub_path.stat().st_size / 1024:.0f} KB)")
    return epub_path
