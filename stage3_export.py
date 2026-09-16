"""Stage 3 平行导出器：Markdown / TeX。

复用 stage3 的单元判定（`_render_popo_body` 产出的 units 已完成切章/段落
合并/脚注锚定），转换其 HTML parts：
- 公式经 `<math alttext>` 回收 LaTeX 源码——display="block"/"inline" 即
  display/行内判定，与 `promote_lone_display_math` 同一口径（不重判，
  病例 015 教训）；
- 导出前对拼接后的章级 HTML 先调用 promote_lone_display_math（与 EPUB
  发射同一函数，判别结果与 EPUB 逐位一致）。

产物（<输出目录>/<词干>/ 下）：
  <书名>.md            Markdown（单文件形态）+ images/
  <书名>_md/           Markdown（分章形态）：index.md + chapters/NN-标题.md + images/
  <书名>.tex           TeX 完整文档（xelatex 可编译）或片段（--tex-fragment）
  images/              图片副本（各形态共用同一目录布局）

选项全部用户可选：--format epub,md,tex 多选；md 单文件/分章、GFM/Pandoc；
tex 完整/片段；导出语言 原文/译文/双出。
"""
import html as _html
import json
import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 单元构建（复用 stage3 全部语义判定）
# ---------------------------------------------------------------------------


def _load_cache(work_dir: Path, engine: str) -> dict:
    """读 <work_dir>/<engine>/ 的 Stage 1 缓存（与 pipeline._load_stage1_cache 同口径）。"""
    eng_dir = work_dir / engine
    cl_files = sorted(eng_dir.glob("*content_list*.json"))
    content_list = json.loads(cl_files[0].read_text(encoding="utf-8")) if cl_files else []
    md_files = sorted(eng_dir.glob("*.md"))
    markdown = md_files[0].read_text(encoding="utf-8") if md_files else ""
    return {"content_list": content_list, "markdown": markdown,
            "images_dir": str(eng_dir / "images")}


def build_units(work_dir: str | Path, use_translations: bool = True) -> list:
    """从工作目录缓存重建 stage3 单元（HTML parts 已含全部结构判定）。"""
    import stage3_epub as s3
    work_dir = Path(work_dir)
    structure = json.loads((work_dir / "structure.json").read_text(encoding="utf-8"))
    blocks_file = structure.get("popo_blocks_file")
    popo_blocks = json.loads((work_dir / blocks_file).read_text(encoding="utf-8")) if blocks_file else []
    content_list = []
    images_dir = ""
    for eng in (structure.get("engine") or "", "vlm", "mineru", "paddleocr"):
        if eng and (work_dir / eng).is_dir():
            cache = _load_cache(work_dir, eng)
            content_list, images_dir = cache["content_list"], cache["images_dir"]
            break
    translations = None
    tfile = work_dir / "translations.json"
    if use_translations and tfile.is_file():
        translations = json.loads(tfile.read_text(encoding="utf-8"))
    total_pages = max((b.get("page_idx", 0) + 1 for b in content_list), default=0)
    body_start, body_end = s3._body_range(structure, total_pages)
    meta = structure.get("metadata", {})
    units = s3._render_popo_body(
        popo_blocks, content_list, body_start, body_end,
        meta.get("language", "zh") == "zh",
        meta.get("title") or work_dir.name,
        toc_entries=structure.get("toc_entries"),
        translations=translations,
    )
    return units


def _unit_html(unit: dict) -> str:
    """单元 parts → 章级 HTML（与 stage3 发射同口径：先 promote 判别公式）。"""
    import stage3_epub as s3
    return s3.promote_lone_display_math("\n".join(unit.get("parts", [])))


# ---------------------------------------------------------------------------
# HTML → 中间事件流（md/tex 共用的最小 DOM 遍历）
# ---------------------------------------------------------------------------

_FN_DEF_RE = re.compile(r'^(?:<a[^>]*>)?(.*?)(?:</a>)?\s*$', re.S)


def _text_of(node) -> str:
    return node.get_text() if hasattr(node, "get_text") else str(node)


def _walk(node, events: list, in_footnotes: bool = False) -> None:
    """把章级 HTML 走成事件流：[("h", level, text)]/[("p", html)]/[("math", display, latex)]/
    [("img", src, alt)]/[("table", rows)]/[("fnref", fid, label)]/[("fndef", fid, text)]/
    [("quote", text)]/[("li", text, ordered, idx)]… 块级粒度，p 内联 html 保留给发射器。"""
    from bs4 import BeautifulSoup, NavigableString, Tag

    if isinstance(node, NavigableString):
        return
    if not isinstance(node, Tag):
        return
    name = node.name
    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        events.append(("h", int(name[1]), _text_of(node).strip()))
    elif name == "aside" and "footnotes" in (node.get("class") or []):
        for p in node.find_all("p", class_="footnote"):
            fid = (p.get("id") or "").removeprefix("fn_")
            backlink = p.find("a")
            if backlink:
                backlink.extract()      # 剥回链（①），只留注文
            if fid:
                events.append(("fndef", fid, _text_of(p).strip()))
            else:
                events.append(("orphanfn", _text_of(p).strip()))
    elif name == "math":
        latex = _html.unescape(node.get("alttext", ""))
        display = node.get("display") == "block"
        events.append(("math", display, latex))
    elif name == "img":
        events.append(("img", node.get("src", ""), node.get("alt", "")))
    elif name == "table":
        rows = []
        for tr in node.find_all("tr"):
            rows.append([_text_of(td).strip() for td in tr.find_all(["th", "td"])])
        if rows:
            events.append(("table", rows))
    elif name in ("p", "blockquote", "li"):
        html_inner = "".join(str(c) for c in node.children)
        kind = {"p": "p", "blockquote": "quote", "li": "li"}[name]
        events.append((kind, html_inner))
    else:
        for c in node.children:
            _walk(c, events)


def _inline_html_to_text(s: str) -> str:
    """段落内联 HTML → 含占位的文本：
    <math> → \\x00MATHI/B latex \\x00；noteref 链接 → \\x00FNREF fid \\x00；
    其余标签剥壳留文字。占位在发射器各自的转义**之后**替换（防 LaTeX 源码
    被 tex_escape 摧毁——初版教训）。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(s, "html.parser")
    for m in soup.find_all("math"):
        latex = _html.unescape(m.get("alttext", ""))
        kind = "MATHB" if m.get("display") == "block" else "MATHI"
        m.replace_with(f"\x00{kind}\x00{latex}\x00")
    for a in soup.find_all("a"):
        href = a.get("href", "")
        if "noteref" in (a.get("epub:type") or ""):
            a.replace_with(f"\x00FNREF\x00{href.removeprefix('#fn_')}\x00")
        else:
            a.replace_with(a.get_text())
    return soup.get_text()


_MARK_RE = re.compile(r"\x00(MATHI|MATHB|FNREF)\x00(.*?)\x00", re.S)


def _emit_tokens_md(txt: str) -> str:
    def rep(m):
        kind, body = m.group(1), _html.unescape(m.group(2))
        if kind == "MATHI":
            return f"${body}$"
        if kind == "MATHB":
            return f"\n$${body}$$\n"
        return f"[^{body}]"
    return _MARK_RE.sub(rep, txt)


def _emit_tokens_tex(txt: str, fns: dict[str, str]) -> str:
    parts = _MARK_RE.split(txt)
    out = []
    # split 结果：[text, kind, body, text, kind, body, ...]
    i = 0
    while i < len(parts):
        if i + 2 <= len(parts) - 1 and parts[i + 1] in ("MATHI", "MATHB", "FNREF"):
            out.append(_tex_escape(parts[i]))
            kind, body = parts[i + 1], parts[i + 2]
            if kind == "MATHI":
                out.append(f"${body}$")
            elif kind == "MATHB":
                out.append(f"\n\\[{body}\\]\n")
            else:
                out.append(f"\\footnote{{{_tex_escape(fns.get(body, ''))}}}")
            i += 3
        else:
            out.append(_tex_escape(parts[i]))
            i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Markdown 发射器（GFM / Pandoc 方言）
# ---------------------------------------------------------------------------

_MD_IMG_RE = re.compile(r"^images/")


def _md_escape_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render_unit_md(unit: dict, dialect: str = "gfm") -> str:
    """一个单元 → Markdown 文本。脚注定义收章末（[^N]: … 语法）。"""
    from bs4 import BeautifulSoup
    out = []
    fndefs: list[tuple[str, str]] = []
    orphans: list[str] = []
    if unit.get("kind") == "divider":
        out.append(f"\n# {unit['title']}\n")
    else:
        out.append(f"\n# {unit['title']}\n")
    events: list = []
    soup = BeautifulSoup(_unit_html(unit), "html.parser")
    for el in soup.find_all(recursive=False):
        _walk(el, events)
    # soup 顶层可能只有一层包装
    if not events:
        for el in soup.contents:
            _walk(el, events)
    fn_n = [0]

    def inline(s: str) -> str:
        return _emit_tokens_md(_inline_html_to_text(s))

    title_key = re.sub(r"\s+", "", unit.get("title", ""))
    for ev in events:
        if ev[0] == "h":
            if re.sub(r"\s+", "", ev[2]) == title_key:
                continue                      # 章题与单元标题重复 → 不重复吐
            out.append(f"\n{'#' * min(ev[1], 6)} {ev[2]}\n")
        elif ev[0] == "p":
            t = inline(ev[1]).strip()
            if t:
                out.append(t + "\n")
        elif ev[0] == "quote":
            t = inline(ev[1]).strip()
            out.append("> " + t.replace("\n", "\n> ") + "\n")
        elif ev[0] == "li":
            out.append("- " + inline(ev[1]).strip())
        elif ev[0] == "math":
            _, display, latex = ev
            out.append(f"\n$${latex}$$\n" if display else f"${latex}$")
        elif ev[0] == "img":
            src, alt = ev[1], ev[2]
            out.append(f"\n![{alt}]({src})\n")
        elif ev[0] == "table":
            rows = ev[1]
            ncol = max(len(r) for r in rows)
            rows = [(r + [""] * ncol)[:ncol] for r in rows]
            out.append("")
            out.append("| " + " | ".join(_md_escape_cell(c) for c in rows[0]) + " |")
            out.append("|" + "---|" * ncol)
            for r in rows[1:]:
                out.append("| " + " | ".join(_md_escape_cell(c) for c in r) + " |")
            out.append("")
        elif ev[0] == "fndef":
            fndefs.append((ev[1], ev[2]))
        elif ev[0] == "orphanfn":
            orphans.append(ev[1])
    if fndefs:
        out.append("\n---\n")
        for fid, text in fndefs:
            out.append(f"[^{fid}]: {text}")
    if orphans:
        # 未锚定尾注：GFM 不渲染无引用的 [^] 定义 → 退化为显式注释列表
        # （GFM/Pandoc 均安全；失败方向=不动作，内容不丢）
        out.append("\n**注释**\n")
        for t in orphans:
            out.append(f"- {t}")
    return "\n".join(out).replace("\n\n\n", "\n\n").strip() + "\n"


# ---------------------------------------------------------------------------
# TeX 发射器
# ---------------------------------------------------------------------------

_TEX_SPECIAL = str.maketrans({
    "#": r"\#", "$": r"\$", "%": r"\%", "&": r"\&",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}", "\\": r"\textbackslash{}",
})
_TEX_LEVEL = {1: "chapter", 2: "section", 3: "subsection", 4: "subsubsection", 5: "paragraph"}


def _tex_escape(s: str) -> str:
    return s.translate(_TEX_SPECIAL)


def render_unit_tex(unit: dict) -> str:
    """一个单元 → TeX 片段。脚注直接用 \footnote{…} 内联。"""
    from bs4 import BeautifulSoup
    out = []
    fns: dict[str, str] = {}
    orphans: list[str] = []
    events: list = []
    soup = BeautifulSoup(_unit_html(unit), "html.parser")
    for el in soup.find_all(recursive=False):
        _walk(el, events)
    if not events:
        for el in soup.contents:
            _walk(el, events)
    for ev in events:
        if ev[0] == "fndef":
            fns[ev[1]] = ev[2]
        elif ev[0] == "orphanfn":
            orphans.append(ev[1])
    if unit.get("kind") == "divider":
        out.append(f"\\part{{{_tex_escape(unit['title'])}}}\n")
    else:
        out.append(f"\\chapter{{{_tex_escape(unit['title'])}}}\n")

    def inline(s: str) -> str:
        return _emit_tokens_tex(_inline_html_to_text(s), fns)

    title_key = re.sub(r"\s+", "", unit.get("title", ""))
    for ev in events:
        if ev[0] == "h":
            if re.sub(r"\s+", "", ev[2]) == title_key:
                continue
            cmd = _TEX_LEVEL.get(min(ev[1], 5), "paragraph")
            out.append(f"\\{cmd}{{{_tex_escape(ev[2])}}}\n")
        elif ev[0] == "p":
            t = inline(ev[1]).strip()
            if t:
                out.append(t + "\n")
        elif ev[0] == "quote":
            out.append("\\begin{quote}\n" + inline(ev[1]).strip() + "\n\\end{quote}\n")
        elif ev[0] == "li":
            out.append("\\begin{itemize}\n\\item " + inline(ev[1]).strip() + "\n\\end{itemize}\n")
        elif ev[0] == "math":
            _, display, latex = ev
            out.append(f"\n\\[{latex}\\]\n" if display else f"${latex}$")
        elif ev[0] == "img":
            src, alt = ev[1], ev[2]
            cap = f"\\caption{{{_tex_escape(alt)}}}" if alt else ""
            out.append("\\begin{figure}[h]\n\\centering\n"
                       f"\\includegraphics[width=\\linewidth]{{{src}}}\n{cap}\n\\end{{figure}}\n")
        elif ev[0] == "table":
            rows = ev[1]
            ncol = max(len(r) for r in rows)
            rows = [(r + [""] * ncol)[:ncol] for r in rows]
            spec = "l" * ncol
            out.append("\\begin{longtable}{" + spec + "}\\toprule")
            out.append(" & ".join(f"\\textbf{{{_tex_escape(c)}}}" for c in rows[0])
                       + " \\\\ \\midrule")
            for r in rows[1:]:
                out.append(" & ".join(_tex_escape(c) for c in r) + " \\\\")
            out.append("\\bottomrule\\end{longtable}\n")
    if orphans:
        # 未锚定尾注：章末显式注释列表（内容不丢）
        out.append("\n\\paragraph{注释}\n\\begin{itemize}")
        for t in orphans:
            out.append("\\item " + _tex_escape(t))
        out.append("\\end{itemize}\n")
    return "\n".join(out).replace("\n\n\n", "\n\n")


# ---------------------------------------------------------------------------
# 出口编排
# ---------------------------------------------------------------------------

def _slug(title: str, idx: int) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", title.strip())[:48].strip("_")
    return f"{idx:02d}-{s or 'untitled'}"


def _safe_stem(name: str) -> str:
    """文件名安全化（Windows 禁冒号——否则写进 ADS 流，文件在目录里不可见）。"""
    s = re.sub(r'[\\/:*?"<>|]+', "_", name).rstrip(" .")
    return s or "book"


def _copy_images(images_dir: str, out_dir: Path) -> str:
    """把图片复制到 out_dir/images/，返回相对链接前缀 'images/'。无图则空目录。"""
    dst = out_dir / "images"
    dst.mkdir(exist_ok=True)
    if images_dir and Path(images_dir).is_dir():
        for f in Path(images_dir).iterdir():
            if f.is_file():
                shutil.copy2(f, dst / f.name)
    return "images"


def export_markdown(units: list, out_dir: Path, book_name: str, title: str,
                    images_dir: str, *, split: bool = False,
                    dialect: str = "gfm") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    front = (f"---\ntitle: {title}\n---\n" if dialect == "pandoc"
             else f"# {title}\n")
    if split:
        # 分章形态自含交付：<书名>_md/ 内含 index.md + chapters/ + images/
        bundle = out_dir / f"{_safe_stem(book_name)}_md"
        bundle.mkdir(parents=True, exist_ok=True)
        _copy_images(images_dir, bundle)
        chapters_dir = bundle / "chapters"
        chapters_dir.mkdir(exist_ok=True)
        index_lines = [front, "\n## 目录\n"]
        idx = 0
        for u in units:
            md = render_unit_md(u, dialect)
            fn = f"chapters/{_slug(u['title'], idx)}.md"
            (bundle / fn).write_text(md, encoding="utf-8")
            index_lines.append(f"- [{u['title']}]({fn})")
            idx += 1
        idx_path = bundle / "index.md"
        idx_path.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
        return [bundle]
    _copy_images(images_dir, out_dir)
    body = front + "\n" + "\n\n".join(render_unit_md(u, dialect) for u in units)
    md_path = out_dir / f"{_safe_stem(book_name)}.md"
    md_path.write_text(body, encoding="utf-8")
    return [md_path]


_TEX_PREAMBLE_ZH = r"""% !TeX program = xelatex
\documentclass[UTF8,12pt]{ctexbook}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{hyperref}
"""
_TEX_PREAMBLE_EN = r"""% !TeX program = xelatex
\documentclass[12pt]{book}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{hyperref}
"""


def export_tex(units: list, out_dir: Path, book_name: str, title: str,
               images_dir: str, *, full: bool = True, zh: bool = True,
               author: str = "") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    _copy_images(images_dir, out_dir)
    body = "\n".join(render_unit_tex(u) for u in units)
    if full:
        pre = _TEX_PREAMBLE_ZH if zh else _TEX_PREAMBLE_EN
        doc = (pre + f"\\title{{{_tex_escape(title)}}}\n"
               + (f"\\author{{{_tex_escape(author)}}}\n" if author else "")
               + "\\begin{document}\n\\maketitle\n\\tableofcontents\n"
               + body + "\n\\end{document}\n")
    else:
        doc = body + "\n"
    tex_path = out_dir / f"{_safe_stem(book_name)}.tex"
    tex_path.write_text(doc, encoding="utf-8")
    return [tex_path]


def export_book(work_dir: str | Path, formats: set[str], *,
                md_split: bool = False, md_dialect: str = "gfm",
                tex_full: bool = True, export_lang: str = "auto",
                progress=None) -> dict:
    """导出编排：按语言选项构建单元（原文/译文/双出），按形态写产物。"""
    _report = progress or (lambda *a, **kw: None)
    work_dir = Path(work_dir)
    structure = json.loads((work_dir / "structure.json").read_text(encoding="utf-8"))
    meta = structure.get("metadata", {})
    title = meta.get("title") or work_dir.name
    author = ", ".join(meta.get("authors", []) or [])
    zh = meta.get("language", "zh") == "zh"

    has_trans = (work_dir / "translations.json").is_file()
    langs = (["orig", "trans"] if export_lang == "both" and has_trans
             else ["trans"] if export_lang in ("auto", "trans") and has_trans
             else ["orig"])

    produced: dict[str, list] = {}
    for lang in langs:
        units = build_units(work_dir, use_translations=(lang == "trans"))
        suffix = "_原文" if (lang == "orig" and "trans" in langs) else \
                 "" if lang == "trans" or not has_trans else ""
        name = _safe_stem(f"{title}{suffix}")
        images_dir = ""
        for eng in (structure.get("engine") or "", "vlm", "mineru", "paddleocr"):
            if eng and (work_dir / eng).is_dir():
                images_dir = str(work_dir / eng / "images")
                break
        if "md" in formats:
            _report(f"导出 Markdown（{lang}）…", None)
            produced.setdefault("md", []).extend(export_markdown(
                units, work_dir, name, title, images_dir,
                split=md_split, dialect=md_dialect))
        if "tex" in formats:
            _report(f"导出 TeX（{lang}）…", None)
            produced.setdefault("tex", []).extend(export_tex(
                units, work_dir, name, title, images_dir,
                full=tex_full, zh=zh, author=author))
    shared_images = work_dir / "images"
    if shared_images.is_dir() and any(shared_images.iterdir()):
        # 单文件 md / tex 与 images/ 同邻相对引用，随产物一并交付（分章 md 目录自含）
        for fmt in ("md", "tex"):
            if any(p.is_file() for p in produced.get(fmt, [])):
                produced[fmt].append(shared_images)
                break
    logger.info(f"  导出完成: {{{', '.join(f'{k}: {len(v)}' for k, v in produced.items())}}}")
    return produced
