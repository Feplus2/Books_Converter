"""
Stage 3: EPUB 生成

核心设计：层级嵌套渲染，杜绝内容重复。

- 编（level 1）→ 分隔页（part divider）
- 章（level 2）→ 独立 EPUB 章节（spine item）
- 节/小节（level 3-5）→ 内嵌在章内的子标题

每页内容只渲染一次，子标题在 block 流中按位置插入。
MinerU 的 text_level 不可靠——所有标题由 DeepSeek 结构分析提供。
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

from ebooklib import epub
import fitz  # PyMuPDF，用于渲染 PDF 首页作为封面
from PIL import Image

logger = logging.getLogger(__name__)

# ── 标点与正则常量 ──────────────────────────────────────────────

_SENTENCE_END = re.compile(r'[。！？…～;:。」』）\)】""\'?!]\s*$')
# 脚注标记（①②③...）结尾 → 段落完整，不应合并
_FOOTNOTE_END = re.compile(r'[①②③④⑤⑥⑦⑧⑨⑩]\s*$')
_BROKEN_P = re.compile(
    r'<p>([^<]*?)</p>\s*\n?\s*<p>([^<]*?)</p>',
    re.DOTALL,
)
# MinerU LaTeX 风格上标：$^{①}$ $^{②}$ 等
_LATEX_SUP = re.compile(r'\$\^\{(.+?)\}\$')
# 脚注标记（①②③...）后紧跟编号列表项（1. 2. 等）→ 需要分段
_FOOTNOTE_LIST_SPLIT = re.compile(
    r'(<sup>[①②③④⑤⑥⑦⑧⑨⑩]</sup>)\s*(\d+\.)'
)

# 英文 → 中文标点映射（用于中文书籍）
_PUNCT_MAP = str.maketrans({
    ',': '，',
    ':': '：',
    ';': '；',
    '!': '！',
    '?': '？',
    '(': '（',
    ')': '）',
})


def _normalize(text: str) -> str:
    """去除所有空白字符，用于标题文本比对"""
    return re.sub(r'[\s\u3000]+', '', text).strip()


def _convert_latex_sup(text: str) -> str:
    """将 MinerU 的 LaTeX 上标 $^{①}$ 转为 HTML <sup>①</sup>"""
    return _LATEX_SUP.sub(r'<sup>\1</sup>', text)


def _split_after_footnote(text: str) -> str:
    """脚注标记（①②③）后紧跟编号列表项（如 '2.'）时，分段显示"""
    return _FOOTNOTE_LIST_SPLIT.sub(r'\1</p>\n<p>\2', text)


def _convert_punctuation(text: str) -> str:
    """将英文标点转为中文标点（仅用于含中文的文本）"""
    if not re.search(r'[\u4e00-\u9fff]', text):
        return text
    text = text.translate(_PUNCT_MAP)
    # 句号：避免误转数字中的点（如 3.14）
    text = re.sub(r'(?<!\d)\.(?!\d)', '。', text)
    return text


# ── 段落合并 ────────────────────────────────────────────────────

def _merge_broken_paragraphs(html: str) -> str:
    """合并因跨页扫描而异常断裂的段落"""
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
    # 第一段以脚注标记结尾（①②③...）→ 段落完整，不合并
    if _FOOTNOTE_END.search(p1):
        return m.group(0)
    # 第二段以编号开头（如 "2."、"一、"）且较短 → 可能是新条目，不合并
    if re.match(r'^\d+[\.\、]', p2) and len(p2) <= 30:
        return m.group(0)
    # 第一段不以句末标点结尾 → 合并
    if not _SENTENCE_END.search(p1) and not _looks_like_heading(p1):
        return f"<p>{p1}{p2}</p>"
    # 第二段极短（扫描跨页碎片）→ 合并
    if len(p2) <= 30 and not _looks_like_heading(p2):
        return f"<p>{p1}{p2}</p>"
    return m.group(0)


def _looks_like_heading(text: str) -> bool:
    """是否为疑似标题行（不应合并）"""
    t = text.strip()
    return len(t) <= 25 and not t.endswith("。")


# ── CSS ─────────────────────────────────────────────────────────

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
h1.part-title {
    font-size: 2em;
    margin: 30% 0 1em;
}
h2 {
    text-align: center;
    font-size: 1.4em;
    margin: 1.5em 0 0.8em;
    font-weight: bold;
}
h3 {
    text-align: left;
    font-size: 1.2em;
    margin: 1.2em 0 0.6em;
    font-weight: bold;
}
h4 {
    text-align: left;
    font-size: 1.1em;
    margin: 1em 0 0.4em;
    font-weight: bold;
}
h5 {
    text-align: left;
    font-size: 1.0em;
    margin: 0.8em 0 0.3em;
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


# ── 内容分类 ────────────────────────────────────────────────────

def _classify_blocks_by_page(content_list: list) -> dict:
    """将 content_list 按页码分组（1-based 页码）"""
    pages = {}
    for block in content_list:
        page = block.get("page_idx", 0) + 1
        if page not in pages:
            pages[page] = []
        pages[page].append(block)
    return pages


# ── Block → HTML 渲染 ──────────────────────────────────────────

def _render_block_to_html(block: dict, images_dir: str,
                          chinese_punct: bool = False) -> str:
    """将单个 content block 渲染为 HTML。

    核心原则：**永不丢弃内容**。所有 block 都有 HTML 输出。
    标题判断由 `complete_outline` 负责，此处只做忠实渲染。
    """
    btype = block.get("type", "")
    text = block.get("text", "")

    if btype in ("text", "paragraph"):
        if not text.strip():
            return ""
        # 无论 text_level 是多少，都渲染为 <p>
        # 标题判断由 complete_outline 负责
        text = _convert_latex_sup(text)
        if chinese_punct:
            text = _convert_punctuation(text)
        text = _split_after_footnote(text)
        return f"<p>{text}</p>"

    elif btype == "title":
        # 也渲染为 <p>，由 complete_outline 决定是否为标题
        if not text.strip():
            return ""
        text = _convert_latex_sup(text)
        if chinese_punct:
            text = _convert_punctuation(text)
        text = _split_after_footnote(text)
        return f"<p>{text}</p>"

    elif btype == "image":
        img_path = block.get("img_path", "") or block.get("image_path", "")
        caption = block.get("image_caption", "")
        if isinstance(caption, list):
            caption = " ".join(caption) if caption else ""
        img_name = Path(img_path).name if img_path else ""
        html = ""
        if img_name:
            html = f'<img src="images/{img_name}" alt="{caption}"/>'
        if caption:
            if chinese_punct:
                caption = _convert_punctuation(caption)
            html += f'<p class="no_indent"><small>{caption}</small></p>'
        return html

    elif btype == "table":
        body = block.get("table_body", "")
        caption = block.get("table_caption", "")
        if isinstance(caption, list):
            caption = " ".join(caption) if caption else ""
        html = ""
        if caption:
            if chinese_punct:
                caption = _convert_punctuation(caption)
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
        return f'<p class="no_indent"><code>{latex}</code></p>'

    elif btype == "code":
        code = block.get("code_body", "") or block.get("text", "")
        lang = block.get("code_language", "")
        lang_attr = f' class="language-{lang}"' if lang else ""
        return f"<pre><code{lang_attr}>{code}</code></pre>"

    elif btype in ("header", "footer", "page_number", "page_footnote",
                    "aside_text"):
        # 页眉页脚等噪音，跳过
        return ""

    elif btype == "chart":
        return ""

    else:
        if text.strip():
            if chinese_punct:
                text = _convert_punctuation(text)
            return f"<p>{text}</p>"
        return ""


def _sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


# ── 章节渲染（核心：声明式渲染，不丢内容） ──────────────────────
    return 0


# ── 层级分组 ────────────────────────────────────────────────────

def _determine_spine_level(body_items: list) -> int:
    """确定 spine 层级：第二个最小 level（通常是"章"）。

    如果只有一种层级，就用那个层级。
    """
    levels = sorted(set(item.get("level", 1) for item in body_items))
    if len(levels) >= 2:
        return levels[1]
    return levels[0] if levels else 1


def _build_spine_groups(body_items: list, spine_level: int) -> list:
    """将 body 扁平列表分组为 spine 组。

    返回结构：
    [
        {
            "parent": {level 1 item} or None,
            "children": [
                {
                    "item": {spine-level item},
                    "sub_headings": [{level 3+ items}, ...]
                }, ...
            ]
        }, ...
    ]
    """
    all_levels = sorted(set(item.get("level", 1) for item in body_items))

    # 找到 spine_level 之上的父级层级（通常是编）
    parent_level = None
    for lv in reversed(all_levels):
        if lv < spine_level:
            parent_level = lv
            break

    groups = []
    current_child_entry = None

    for item in body_items:
        level = item.get("level", 1)

        if parent_level is not None and level == parent_level:
            # 新的父级（编）
            groups.append({"parent": item, "children": []})

        elif level == spine_level:
            # 新的 spine 项（章）
            current_child_entry = {"item": item, "sub_headings": []}
            if groups:
                groups[-1]["children"].append(current_child_entry)
            else:
                # 无父级
                groups.append({"parent": None, "children": [current_child_entry]})

        elif level > spine_level:
            # 子标题
            if current_child_entry:
                current_child_entry["sub_headings"].append(item)

    return groups


# ── 章节渲染（核心：声明式渲染，不丢内容） ──────────────────────

def _render_chapter_html(
    ch_item: dict,
    sub_headings: list,
    pages: dict,
    images_dir: str,
    noise_pages: set,
    chinese_punct: bool,
    complete_outline: list = None,
) -> str:
    """渲染一个章（spine item）的完整 HTML。

    核心原则：**永不丢弃内容**。
    - 以章标题开头（h2）
    - 逐页渲染 content blocks
    - 遇到匹配 `complete_outline` 中标题的 block → 渲染为 `<h{level}>`
    - 其他所有 block → 渲染为 `<p>` 或对应的 HTML
    - **绝不跳过任何 block**（除非是页眉页脚等噪音类型）
    """
    ch_level = ch_item.get("level", 2)
    ch_tag = f"h{min(ch_level, 2)}"
    ch_title_html = _convert_latex_sup(ch_item["title"])
    parts = [f'<{ch_tag} class="chapter-title">{ch_title_html}</{ch_tag}>']

    # 构建标题查找表（归一化文本 → 标题信息）
    heading_lookup = {}

    # 1. 从 sub_headings（来自 body 结构）添加
    for sub in sub_headings:
        key = _normalize(sub["title"])
        heading_lookup[key] = {"level": sub.get("level", 3), "title": sub["title"]}

    # 2. 从 complete_outline 添加（覆盖/补充）
    if complete_outline:
        for item in complete_outline:
            if item.get("status") == "heading" or "level" in item:
                text = item.get("text", "")
                if text:
                    key = _normalize(text)
                    # 只添加 page 在当前章范围内的
                    page = item.get("page", 0)
                    if ch_item["page_start"] <= page <= ch_item["page_end"]:
                        heading_lookup[key] = {"level": item["level"], "title": text}

    # 章标题本身也需要跳过（避免重复渲染）
    ch_title_normalized = _normalize(ch_item["title"])

    # 逐页渲染
    for page in range(ch_item["page_start"], ch_item["page_end"] + 1):
        if page in noise_pages:
            continue

        for block in pages.get(page, []):
            text = block.get("text", "").strip()
            btype = block.get("type", "")

            # 页眉页脚等噪音类型，跳过
            if btype in ("header", "footer", "page_number", "page_footnote", "aside_text"):
                continue

            # 检查是否匹配标题 → 渲染为 h-tag
            normalized = _normalize(text) if text else ""
            if normalized and normalized in heading_lookup:
                h_info = heading_lookup[normalized]
                htag = f"h{min(h_info['level'], 6)}"
                h_text = _convert_latex_sup(h_info['title'])
                parts.append(f"<{htag}>{h_text}</{htag}>")
                continue

            # 跳过章标题（避免重复）
            if normalized and normalized == ch_title_normalized:
                continue

            # 其他所有 block → 正常渲染（不丢内容）
            rendered = _render_block_to_html(block, images_dir, chinese_punct)
            if rendered:
                parts.append(rendered)

    result = "\n".join(parts)
    result = _merge_broken_paragraphs(result)
    return result


def _render_pages_html(
    page_start: int,
    page_end: int,
    pages: dict,
    images_dir: str,
    noise_pages: set,
    chinese_punct: bool = False,
) -> str:
    """渲染指定页码范围的纯内容（无标题覆写，用于前页/后页/分隔页）"""
    html_parts = []
    for pn in range(page_start, page_end + 1):
        if pn in noise_pages:
            continue
        for block in pages.get(pn, []):
            btype = block.get("type", "")
            # 前页/后页中跳过 title 块（直接渲染为段落）
            if btype == "title":
                text = block.get("text", "")
                if text.strip():
                    level = block.get("text_level", 0) or 1
                    tag = f"h{min(level + 1, 4)}"
                    html_parts.append(f"<{tag}>{_convert_latex_sup(text)}</{tag}>")
                continue
            rendered = _render_block_to_html(block, images_dir, chinese_punct)
            if rendered:
                html_parts.append(rendered)
    return "\n".join(html_parts)


# ── 图片收集 ────────────────────────────────────────────────────

def _collect_images(content_list: list, images_dir: str) -> list:
    """从 content_list 中提取图片信息，尝试从 images_dir 或 CWD 加载"""
    image_info = []
    seen = set()

    for block in content_list:
        if block.get("type") != "image":
            continue
        img_path = block.get("img_path", "") or block.get("image_path", "")
        if not img_path:
            continue
        img_name = Path(img_path).name
        if img_name in seen:
            continue
        seen.add(img_name)
        image_info.append((img_name, img_path))

    # 搜索图片文件的候选目录
    search_dirs = []
    if images_dir and os.path.isdir(images_dir):
        search_dirs.append(images_dir)
    # MinerU SDK 可能将图片保存在 CWD/images/ 下
    cwd_images = Path("images")
    if cwd_images.is_dir():
        search_dirs.append(str(cwd_images))

    loaded = []
    for img_name, img_path in image_info:
        for search_dir in search_dirs:
            candidate = Path(search_dir) / img_name
            if candidate.is_file():
                loaded.append((img_name, str(candidate)))
                break
        else:
            # 也尝试直接用 img_path（如果是绝对路径）
            if os.path.isabs(img_path) and os.path.isfile(img_path):
                loaded.append((img_name, img_path))

    return loaded


# ── 封面提取 ────────────────────────────────────────────────────

def _extract_cover_image(pdf_path: str, output_dir: str) -> Optional[str]:
    """从 PDF 首页渲染高清封面图片

    Returns:
        封面图片路径，失败时返回 None
    """
    try:
        doc = fitz.open(pdf_path)
        page = doc[0]
        # 2x 缩放 = 约 144 DPI，画质足够且体积可控
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)

        cover_path = Path(output_dir) / "cover.jpg"
        pix.save(str(cover_path))
        doc.close()
        logger.info(f"  封面: 从 PDF 首页提取 ({pix.width}x{pix.height})")
        return str(cover_path)
    except Exception as e:
        logger.warning(f"  封面提取失败: {e}")
        return None


# ── 主函数 ──────────────────────────────────────────────────────

def generate_epub(
    book_name: str,
    mineru_info: dict,
    structure: dict,
    output_dir: str,
    pdf_path: str = "",
) -> Path:
    """
    生成 EPUB 文件。

    Args:
        book_name: 书名
        mineru_info: Stage 1 输出 {markdown, content_list, images_dir}
        structure: Stage 2 输出 {metadata, front_matter, body, back_matter, noise_ranges}
        output_dir: 输出目录
        pdf_path: 源 PDF 路径（用于提取封面）
    """
    logger.info("Stage 3 开始: 生成 EPUB")

    content_list = mineru_info.get("content_list", [])
    images_dir = mineru_info.get("images_dir", "")
    meta = structure.get("metadata", {})
    body_items = structure.get("body", [])

    # 噪音页（只跳过空白页）
    noise_pages = set()
    for noise in structure.get("noise_ranges", []):
        if noise.get("type") == "blank_page":
            for p in noise.get("pages", []):
                noise_pages.add(p)

    # 按页组织 blocks
    pages = _classify_blocks_by_page(content_list)

    # 确定 spine 层级和分组
    spine_level = _determine_spine_level(body_items)
    spine_groups = _build_spine_groups(body_items, spine_level)
    logger.info(
        f"  层级: spine=level {spine_level}, "
        f"{len(spine_groups)} 个分组, "
        f"{sum(len(g['children']) for g in spine_groups)} 个 spine 章节"
    )

    # 是否中文标点转换
    chinese_punct = meta.get("language", "zh") == "zh"

    # 创建 EPUB
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

    # 封面（从 PDF 首页截图）
    cover_image_path = None
    if pdf_path and os.path.isfile(pdf_path):
        cover_img_path = _extract_cover_image(pdf_path, output_dir)
        if cover_img_path and os.path.isfile(cover_img_path):
            cover_image_path = cover_img_path
            logger.info(f"  封面已提取: cover.jpg")

    # CSS
    css = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=DEFAULT_CSS,
    )
    book.add_item(css)

    # 图片
    loaded_images = _collect_images(content_list, images_dir)
    for img_name, img_path in loaded_images:
        try:
            with open(img_path, "rb") as f:
                img_data = f.read()
            ext = Path(img_name).suffix.lower().lstrip(".")
            media_type = f"image/{'jpeg' if ext == 'jpg' else ext}"
            item = epub.EpubItem(
                uid=f"img_{img_name}",
                file_name=f"images/{img_name}",
                media_type=media_type,
                content=img_data,
            )
            book.add_item(item)
        except Exception as e:
            logger.warning(f"  无法添加图片 {img_name}: {e}")
    if loaded_images:
        logger.info(f"  加载 {len(loaded_images)} 张图片")

    # Spine 和 TOC
    spine = ["nav"]
    front_toc = []       # 前页 TOC 条目（扁平）
    body_spine_info = [] # 正文：记录 (group_idx, type, epub_obj) 用于构建嵌套 TOC
    back_toc = []        # 后页 TOC 条目（扁平）
    chapter_idx = 0

    # ── 前页 ──
    for i, fm in enumerate(structure.get("front_matter", [])):
        if not fm.get("keep", False):
            continue
        fm_html = _render_pages_html(
            fm.get("page_start", 0),
            fm.get("page_end", 0),
            pages, images_dir, noise_pages, chinese_punct,
        )
        if not fm_html.strip():
            continue

        fm_label = fm.get("label", fm.get("type", "front"))
        chapter = epub.EpubHtml(
            title=fm_label,
            file_name=f"front_{i:02d}.xhtml",
            lang="zh",
        )
        chapter.content = f"<h1>{fm_label}</h1>\n{fm_html}"
        chapter.add_item(css)
        book.add_item(chapter)
        spine.append(chapter)
        front_toc.append(epub.Link(chapter.file_name, fm_label, f"fm_{i}"))

    # ── 正文（层级嵌套） ──
    for group in spine_groups:
        parent = group.get("parent")
        children = group["children"]

        group_info = {"parent_link": None, "children_links": []}

        # 父级分隔页（编）
        if parent:
            divider_parts = [
                f'<h1 class="part-title">{_convert_latex_sup(parent["title"])}</h1>'
            ]
            # 渲染编的引言页（编起始 → 第一个章起始之前）
            if children:
                intro_end = children[0]["item"]["page_start"] - 1
                if parent["page_start"] <= intro_end:
                    intro_html = _render_pages_html(
                        parent["page_start"], intro_end,
                        pages, images_dir, noise_pages, chinese_punct,
                    )
                    if intro_html.strip():
                        divider_parts.append(intro_html)

            divider = epub.EpubHtml(
                title=parent["title"],
                file_name=f"part_{chapter_idx:03d}.xhtml",
                lang="zh",
            )
            divider.content = "\n".join(divider_parts)
            divider.add_item(css)
            book.add_item(divider)
            spine.append(divider)
            group_info["parent_link"] = epub.Link(
                divider.file_name, parent["title"], f"part_{chapter_idx}"
            )
            chapter_idx += 1

        # 每个章（spine item）
        for child in children:
            ch_item = child["item"]
            sub_headings = child["sub_headings"]

            ch_html = _render_chapter_html(
                ch_item, sub_headings, pages, images_dir,
                noise_pages, chinese_punct,
                complete_outline=structure.get("complete_outline", []),
            )

            file_name = f"chapter_{chapter_idx:03d}.xhtml"
            chapter = epub.EpubHtml(
                title=ch_item["title"], file_name=file_name, lang="zh"
            )
            chapter.content = ch_html
            chapter.add_item(css)
            book.add_item(chapter)
            spine.append(chapter)
            group_info["children_links"].append(
                epub.Link(file_name, ch_item["title"], f"ch_{chapter_idx}")
            )
            chapter_idx += 1

        body_spine_info.append(group_info)

    # ── 后页 ──
    for i, bm in enumerate(structure.get("back_matter", [])):
        bm_html = _render_pages_html(
            bm.get("page_start", 0),
            bm.get("page_end", 0),
            pages, images_dir, noise_pages, chinese_punct,
        )
        if not bm_html.strip():
            continue

        bm_label = bm.get("label", bm.get("type", "back"))
        chapter = epub.EpubHtml(
            title=bm_label,
            file_name=f"back_{i:02d}.xhtml",
            lang="zh",
        )
        chapter.content = f"<h1>{bm_label}</h1>\n{bm_html}"
        chapter.add_item(css)
        book.add_item(chapter)
        spine.append(chapter)
        back_toc.append(
            epub.Link(chapter.file_name, bm_label, f"bm_{i}")
        )

    # ── 构建嵌套 TOC ──
    toc_entries = list(front_toc)
    for group_info in body_spine_info:
        parent_link = group_info["parent_link"]
        children = group_info["children_links"]
        if parent_link and children:
            # 有父级（编）→ 嵌套：Section(编标题, (章链接...))
            toc_entries.append((
                epub.Section(parent_link.title),
                tuple(children),
            ))
        elif parent_link:
            # 有父级但无子章 → 单独添加
            toc_entries.append(parent_link)
        else:
            # 无父级 → 扁平添加所有章
            toc_entries.extend(children)
    toc_entries.extend(back_toc)

    # ── 封面页 ──
    if cover_image_path:
        with open(cover_image_path, "rb") as f:
            cover_data = f.read()
        # 添加封面图片
        cover_img_item = epub.EpubItem(
            uid="cover-image",
            file_name="images/cover.jpg",
            media_type="image/jpeg",
            content=cover_data,
        )
        book.add_item(cover_img_item)
        # 添加封面页
        cover_html = epub.EpubHtml(
            title="封面",
            file_name="cover.xhtml",
            lang="zh",
        )
        cover_html.content = (
            '<html><body style="text-align:center;margin:0;padding:0;">'
            '<img src="images/cover.jpg" style="max-width:100%;max-height:100%;"/>'
            '</body></html>'
        )
        cover_html.add_item(css)
        book.add_item(cover_html)
        # 插入到 spine 最前面（在 "nav" 之后）
        spine.insert(1, cover_html)
        # 封面加入 TOC 最前面
        front_toc.insert(0, epub.Link("cover.xhtml", "封面", "cover"))

    # ── 组装 ──
    book.toc = tuple(toc_entries)
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub_path = Path(output_dir) / f"{_sanitize_filename(title)}.epub"
    epub.write_epub(str(epub_path), book)

    logger.info(
        f"  EPUB 已生成: {epub_path} "
        f"({epub_path.stat().st_size / 1024:.0f} KB, "
        f"{chapter_idx} 个章节)"
    )
    return epub_path
