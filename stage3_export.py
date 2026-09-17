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
    zh = meta.get("language", "zh") == "zh"

    def _extra_units(entries: list, default_label: str) -> list:
        """前页/后页（前言、版权页、附录等 keep=true 条目）→ 单元。
        每个条目并成一个以 label 命名的单元（内容不丢，封面 keep=false 跳过）。"""
        out = []
        for it in entries:
            if not it.get("keep"):
                continue
            label = it.get("label") or default_label
            us = s3._render_popo_body(
                popo_blocks, content_list,
                int(it.get("page_start", 0)), int(it.get("page_end", 0)),
                zh, label, toc_entries=None, translations=translations)
            if not us:
                continue
            u = us[0]
            for extra in us[1:]:
                u["parts"].extend(extra.get("parts", []))
            u["kind"], u["title"] = "chapter", label
            out.append(u)
        return out

    units = _extra_units(structure.get("front_matter", []), "前页")
    units += s3._render_popo_body(
        popo_blocks, content_list, body_start, body_end,
        zh,
        meta.get("title") or work_dir.name,
        toc_entries=structure.get("toc_entries"),
        translations=translations,
    )
    units += _extra_units(structure.get("back_matter", []), "后页")
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


_ENTITY_WRECK_RE = re.compile(r"&#(x[0-9A-Fa-f]+|\d+);|#(x[0-9A-Fa-f]{2}|\d{2,3});")
_NAMED_WRECK_RE = re.compile(r"(gt|lt|amp|quot|apos);")
_NAMED_WRECK_MAP = {"gt": ">", "lt": "<", "amp": "&", "quot": '"', "apos": "'"}
_TAG_RE = re.compile(r"<[^>]+>")
_NONASCII_RUN_RE = re.compile(r"[^\x00-\x7f]+")

# 数学体 unicode → LaTeX 映射（模型常把 λ/−/₀ 直接写进 $…$；xelatex 数学
# 字体没这些字形。EPUB 侧 latex2mathml 自带 unicode 表，无需此映射）
_TEX_MATH_UNICODE = {
    "−": "-", "–": "-", "―": "-",
    "×": r"\times", "÷": r"\div", "±": r"\pm", "∓": r"\mp",
    "≤": r"\leq", "≥": r"\geq", "≠": r"\neq", "≈": r"\approx", "≡": r"\equiv",
    "∼": r"\sim", "∝": r"\propto", "∞": r"\infty", "∂": r"\partial", "∇": r"\nabla",
    "∈": r"\in", "∉": r"\notin", "∋": r"\ni", "⊂": r"\subset", "⊃": r"\supset",
    "⊆": r"\subseteq", "⊇": r"\supseteq", "∪": r"\cup", "∩": r"\cap",
    "∅": r"\emptyset", "∀": r"\forall", "∃": r"\exists", "¬": r"\neg",
    "∧": r"\wedge", "∨": r"\vee", "⊕": r"\oplus", "⊗": r"\otimes", "⊙": r"\odot",
    "→": r"\to", "←": r"\leftarrow", "↦": r"\mapsto", "↔": r"\leftrightarrow",
    "⇒": r"\Rightarrow", "⇐": r"\Leftarrow", "⇔": r"\Leftrightarrow",
    "↑": r"\uparrow", "↓": r"\downarrow", "↗": r"\nearrow", "↘": r"\searrow",
    "⟨": r"\langle", "⟩": r"\rangle", "⊥": r"\perp", "∥": r"\parallel",
    "∑": r"\sum", "∏": r"\prod", "∫": r"\int", "∮": r"\oint",
    "√": r"\surd", "·": r"\cdot", "⋯": r"\cdots", "…": r"\ldots",
    "∘": r"\circ", "∠": r"\angle", "°": r"^\circ",
    "′": "'", "″": "''", "‴": "'''", "‵": "`",
    "ℏ": r"\hbar", "ℓ": r"\ell", "ℜ": r"\Re", "ℑ": r"\Im", "ℵ": r"\aleph",
    "✓": r"\checkmark", "✔": r"\checkmark", "✗": r"\times", "✘": r"\times",
    "●": r"\bullet", "➡": r"\Rightarrow", "➜": r"\rightarrow",
    "≦": r"\leqq", "≧": r"\geqq", "☆": r"\star", "꜀": "'",
    "⁽": "^(", "⁾": "^)", "❖": r"\blacklozenge",
    "ℝ": r"\mathbb{R}", "ℤ": r"\mathbb{Z}", "ℕ": r"\mathbb{N}",
    "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}",
    "⁰": "^0", "¹": "^1", "²": "^2", "³": "^3", "⁴": "^4", "⁵": "^5",
    "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9", "ⁿ": "^n", "⁺": "^+", "⁻": "^-",
    "₀": "_0", "₁": "_1", "₂": "_2", "₃": "_3", "₄": "_4", "₅": "_5",
    "₆": "_6", "₇": "_7", "₈": "_8", "₉": "_9", "₊": "_+", "₋": "_-", "₌": "_=",
}
_TEX_MATH_UNICODE.update({c: "\\" + n for c, n in zip(
    "αβγδεζηθικλμνξπρστυφχψω",
    "alpha beta gamma delta varepsilon zeta eta theta iota kappa lambda mu "
    "nu xi pi rho sigma tau upsilon phi chi psi omega".split())})
_TEX_MATH_UNICODE.update({c: "\\" + n for c, n in zip(
    "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΠΡΣΤΥΦΧΨΩ",
    "Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota Kappa Lambda Mu "
    "Nu Xi Pi Rho Sigma Tau Upsilon Phi Chi Psi Omega".split())})
_TEX_MATH_UNICODE.update({"ϵ": r"\epsilon", "ϕ": r"\varphi", "ϑ": r"\vartheta",
                          "ς": r"\varsigma", "ϱ": r"\varrho", "ϖ": r"\varpi"})
# 长尾补齐：圈码进 \textcircled（数学字体无圈码字形）；数学粗体字母数字；
# 斜体不等号、修饰字母上标、华氏度
_TEX_MATH_UNICODE.update({chr(0x2460 + i): f"\\text{{\\textcircled{{{i + 1}}}}}"
                          for i in range(20)})
_TEX_MATH_UNICODE.update({chr(0x3251 + i): f"\\text{{\\textcircled{{{21 + i}}}}}"
                          for i in range(14)})
_TEX_MATH_UNICODE.update({chr(0x1D400 + i): f"\\mathbf{{{chr(65 + i)}}}"
                          for i in range(26)})
_TEX_MATH_UNICODE.update({chr(0x1D41A + i): f"\\mathbf{{{chr(97 + i)}}}"
                          for i in range(26)})
_TEX_MATH_UNICODE.update({chr(0x1D7CE + i): f"\\mathbf{{{i}}}"
                          for i in range(10)})
_TEX_MATH_UNICODE.update({
    "⩽": r"\leqslant", "⩾": r"\geqslant", "℉": r"^\circ\mathrm{F}", "℃": r"^\circ\mathrm{C}",
    "ˣ": "^x", "ʸ": "^y", "ʳ": "^r", "ˡ": "^l", "ˢ": "^s", "ʰ": "^h",
    "ᵗ": "^t", "ᵏ": "^k", "ᵐ": "^m", "ⁱ": "^i",
    "□": r"\square", "■": r"\blacksquare", "▪": r"\blacksquare",
    "∶": r"\colon", "△": r"\triangle",
})


_GREEK_GLUE_RE = re.compile(
    r"\\(alpha|beta|gamma|delta|varepsilon|epsilon|zeta|eta|theta|vartheta|iota|"
    r"kappa|lambda|mu|nu|xi|pi|rho|varrho|sigma|varsigma|tau|upsilon|phi|varphi|"
    r"chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega)"
    r"(?=[a-zA-Z])")


_SUB_ACCENT_RE = re.compile(
    r"_(\\(?:bar|hat|tilde|vec|dot|ddot|breve|check|acute|grave))\s*"
    r"(\\[a-zA-Z]+|[a-zA-Z])")
# \text{^\circ\mathrm{C}}：文本壳里塞上下标必炸（Missing $，机械手册实测）；
# 内层为纯 ASCII 数学内容才拆壳（含 CJK 的 \text 是合法用法，不动）
_TEXT_MATH_SHELL_RE = re.compile(
    r"\\text\{((?:[^{}]|\{[^{}]*\})*[\^_](?:[^{}]|\{[^{}]*\})*)\}")
_TEXT_NEST_RE = re.compile(r"\\text\{\\text\{([^{}]*)\}\}")


def _tex_math_sanitize(s: str) -> str:
    """数学体出闸：希腊命令粘连字母补空格（\\lambdax→\\lambda x，unicode 映射
    与模型笔误同源）→ 下标裸重音命令补原子（E_\\bar\\nu→E_{\\bar{\\nu}}，
    QFT l.8272 Missing { 实测）→ unicode 映射（λ→\\lambda、₀→_0、′→'，命令
    后紧跟字母同样补空格）→ 剩余非 ASCII 连续段包 \\text{}。"""
    s = _GREEK_GLUE_RE.sub(r"\\\1 ", s)
    s = _SUB_ACCENT_RE.sub(r"_{\1{\2}}", s)
    s = _TEXT_MATH_SHELL_RE.sub(
        lambda m: m.group(1) if all(ord(c) < 128 for c in m.group(1))
        else m.group(0), s)
    out = []
    for i, ch in enumerate(s):
        rep = _TEX_MATH_UNICODE.get(ch)
        if rep is None:
            out.append(ch)
            continue
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if rep[-1].isalpha() and nxt.isalpha():
            rep += " "
        out.append(rep)
    s = "".join(out)
    # unicode 映射可能在 \text{} 壳内造出上下标（℃→^\circ\mathrm{C}，
    # 机械手册 l.12350 Missing $ 实测）——拆壳在映射后再扫一遍
    s = _TEXT_MATH_SHELL_RE.sub(
        lambda m: m.group(1) if all(ord(c) < 128 for c in m.group(1))
        else m.group(0), s)
    s = _NONASCII_RUN_RE.sub(lambda m: f"\\text{{{m.group(0)}}}", s)
    # 壳内 CJK 被非 ASCII 包裹规则再包一层 → 塌缩回单层（合法但聒噪）
    return _TEXT_NEST_RE.sub(r"\\text{\1}", s)


def _clean_latex(s: str) -> str:
    """alttext 出闸清洗：实体解码 → 实体残骸修复（上游转换器吃掉 & 留下
    #x27; / gt; 之类）→ 剥泄漏的 HTML 标签（脚注锚定可能写进 alttext）。

    失败方向：只删可证明是标签/残骸的片段，其余原样保留。"""
    s = _html.unescape(s)
    s = _NAMED_WRECK_RE.sub(lambda m: _NAMED_WRECK_MAP[m.group(1)], s)

    def _wreck(m):
        g = m.group(1) or m.group(2)
        try:
            return chr(int(g[1:], 16) if g.startswith("x") else int(g))
        except (ValueError, OverflowError):
            return m.group(0)

    s = _ENTITY_WRECK_RE.sub(_wreck, s)
    return _TAG_RE.sub("", s)


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
        latex = _clean_latex(node.get("alttext", ""))
        display = node.get("display") == "block"
        events.append(("math", display, latex))
    elif name == "img":
        events.append(("img", node.get("src", ""), node.get("alt", "")))
    elif name == "table":
        # 单元格走内联通道：$...$/math 提取 alttext（否则 get_text 把公式
        # 压成扁平 unicode——高数导数公式表实测），fnref 一并占位
        rows = []
        for tr in node.find_all("tr"):
            rows.append([_inline_html_to_text(
                "".join(str(c) for c in td.children)).strip()
                for td in tr.find_all(["th", "td"])])
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
        latex = _clean_latex(m.get("alttext", ""))
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
            # 空行环绕成独立段落——否则 Typora/GFM 把行内 $$ 退化为源码/行内
            # （实测：记作 $$…$$ 其中 被渲染成左对齐小字甚至源码块）
            return f"\n\n$${body}$$\n\n"
        return f"[^{body}]"
    return _MARK_RE.sub(rep, txt)


def _emit_tokens_tex(txt: str, fns: dict[str, str], in_cell: bool = False) -> str:
    parts = _MARK_RE.split(txt)
    out = []
    # split 结果：[text, kind, body, text, kind, body, ...]
    i = 0
    while i < len(parts):
        if i + 2 <= len(parts) - 1 and parts[i + 1] in ("MATHI", "MATHB", "FNREF"):
            out.append(_tex_escape(parts[i]))
            kind, body = parts[i + 1], parts[i + 2]
            if kind == "MATHI":
                out.append(f"${_tex_math_sanitize(body)}$")
            elif kind == "MATHB":
                if in_cell:
                    # 表格单元格禁 display 数学（longtable l/p 列内 \\[\\] 必炸，
                    # 机械设计手册 21 处 Missing $ 级联实测）——降级行内
                    out.append(f"${_tex_math_sanitize(body)}$")
                else:
                    out.append(f"\n\\[{_tex_math_sanitize(body)}\\]\n")
            else:
                out.append(f"\\footnote{{{_tex_escape(fns.get(body, ''))}}}")
            i += 3
        else:
            out.append(_tex_escape(parts[i]))
            i += 1
    return "".join(out)


def _col_spec_tex(rows: list[list[str]], ncol: int) -> str:
    """longtable 列宽按各列最大单元格长度加权（'l' 列不换行，长文本单元格
    做出 4386pt Overfull 实测）；raggedright 防窄列两端对齐拉花。"""
    weights: list[int] = []
    for ci in range(ncol):
        w = max((len(_MARK_RE.sub("", r[ci])) for r in rows if ci < len(r)),
                default=1)
        weights.append(max(3, min(w, 30)))
    total = sum(weights)
    return "".join(">{\\raggedright\\arraybackslash}p{%.3f\\linewidth}"
                   % (w / total) for w in weights)


# ---------------------------------------------------------------------------
# Markdown 发射器（GFM / Pandoc 方言）
# ---------------------------------------------------------------------------

_MD_IMG_RE = re.compile(r"^images/")


def _md_escape_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


_NOTES_LABEL = {"zh": "注释", "ja": "注"}
_TOC_LABEL = {"zh": "目录", "ja": "目次"}


def _notes_label(lang: str) -> str:
    """尾注列表标题随书语言（FG 英文书 EN preamble 下 '注释' 缺字形实测）。"""
    return _NOTES_LABEL.get(lang, "Notes")


def render_unit_md(unit: dict, dialect: str = "gfm", lang: str = "zh") -> str:
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
            out.append("| " + " | ".join(_md_escape_cell(_emit_tokens_md(c))
                                          for c in rows[0]) + " |")
            out.append("|" + "---|" * ncol)
            for r in rows[1:]:
                out.append("| " + " | ".join(_md_escape_cell(_emit_tokens_md(c))
                                             for c in r) + " |")
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
        out.append(f"\n**{_notes_label(lang)}**\n")
        for t in orphans:
            out.append(f"- {t}")
    return "\n".join(out).replace("\n\n\n", "\n\n").strip() + "\n"


# ---------------------------------------------------------------------------
# TeX 发射器
# ---------------------------------------------------------------------------

_TEX_SPECIAL_BASE = {
    "#": r"\#", "$": r"\$", "%": r"\%", "&": r"\&",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}", "\\": r"\textbackslash{}",
}
# 文本模式 unicode 兜底：数学命令类包 $…$，上下标补 {} 原子，罗马数字转 ASCII，
# 圈码用 \textcircled（lmroman 字体没这些字形，不映射就是满屏缺字符警告）
_TEX_TEXT_EXTRA: dict[str, str] = {}
for _ch, _cmd in _TEX_MATH_UNICODE.items():
    if _cmd.startswith("\\"):
        _TEX_TEXT_EXTRA[_ch] = f"${_cmd}$"
    elif _cmd.startswith(("^", "_")):
        _TEX_TEXT_EXTRA[_ch] = f"${{}}{_cmd}$"
    elif _cmd == "-":
        _TEX_TEXT_EXTRA[_ch] = "--"
    else:
        _TEX_TEXT_EXTRA[_ch] = _cmd
# 纯文本直替（非数学命令）：制表框线 → 破折号/竖线（izuno 96×'─' 缺字形实测）、
# 方向控制符剥除（must_defend U+200F 实测）
_TEX_TEXT_EXTRA.update({
    "─": "—", "│": "|", "┌": "+", "┐": "+", "└": "+", "┘": "+",
    "├": "+", "┤": "+", "┬": "+", "┴": "+", "┼": "+",
    "‏": "", "‎": "", "­": "",
    "　": " ",  # U+3000 全角空格（Times 无字形，机械手册 1582 处实测）
})
_TEX_TEXT_EXTRA.update({chr(0x2460 + i): f"\\textcircled{{{i + 1}}}"
                        for i in range(20)})
_TEX_TEXT_EXTRA.update({c: t for c, t in zip(
    "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ",
    ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"])})
_TEX_SPECIAL = str.maketrans({**_TEX_SPECIAL_BASE, **_TEX_TEXT_EXTRA})
_TEX_LEVEL = {1: "chapter", 2: "section", 3: "subsection", 4: "subsubsection", 5: "paragraph"}


_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _tex_escape(s: str) -> str:
    # C0 控制字符是 VLM 输出的工件（must_defend U+0019 实测），进 tex 即
    # "Text line contains an invalid character"——剥除，内容无损
    return _CTRL_CHARS_RE.sub("", s).translate(_TEX_SPECIAL)


def render_unit_tex(unit: dict, lang: str = "zh") -> str:
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
    # 标题携带书自身编号（"第二章 导数与微分"），一律星号命令+手动入 TOC，
    # 杜绝自动编号叠加成"第二章 第二章"（页眉/目录双重编号实测）
    if unit.get("kind") == "divider":
        t = _tex_escape(unit['title'])
        out.append(f"\\part*{{{t}}}\n\\addcontentsline{{toc}}{{part}}{{{t}}}\n")
    else:
        t = _tex_escape(unit['title'])
        out.append(f"\\chapter*{{{t}}}\n\\addcontentsline{{toc}}{{chapter}}{{{t}}}\n"
                   f"\\markboth{{{t}}}{{}}\n")

    def inline(s: str) -> str:
        return _emit_tokens_tex(_inline_html_to_text(s), fns)

    title_key = re.sub(r"\s+", "", unit.get("title", ""))
    for ev in events:
        if ev[0] == "h":
            if re.sub(r"\s+", "", ev[2]) == title_key:
                continue
            cmd = _TEX_LEVEL.get(min(ev[1], 5), "paragraph")
            t = _tex_escape(ev[2])
            line = f"\\{cmd}*{{{t}}}\n"
            if cmd in ("section", "subsection"):
                line += f"\\addcontentsline{{toc}}{{{cmd}}}{{{t}}}\n"
            out.append(line)
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
            out.append(f"\n\\[{_tex_math_sanitize(latex)}\\]\n" if display
                       else f"${_tex_math_sanitize(latex)}$")
        elif ev[0] == "img":
            src, alt = ev[1], ev[2]
            cap = f"\\caption*{{{_tex_escape(alt)}}}" if alt else ""
            # [H] 精确就位 + \caption*（书自带图号"图 1-7"，自动编号叠加成
            # "图 11: 图 1-7" 实测；caption 宏包提供星号形式）
            out.append("\\begin{figure}[H]\n\\centering\n"
                       f"\\includegraphics[width=\\linewidth]{{{src}}}\n{cap}\n\\end{{figure}}\n")
        elif ev[0] == "table":
            rows = ev[1]
            ncol = max(len(r) for r in rows)
            rows = [(r + [""] * ncol)[:ncol] for r in rows]
            spec = _col_spec_tex(rows, ncol)
            out.append("\\begin{longtable}{" + spec + "}\\toprule\\relax")
            out.append(" & ".join(f"\\textbf{{{_emit_tokens_tex(c, fns, in_cell=True)}}}" for c in rows[0])
                       + " \\\\ \\midrule\\relax")
            for ri, r in enumerate(rows[1:]):
                # 非末行 \\ 后 \relax：次行首字符为 '[' 的单元格不被吞成 \\[dimen]
                # 可选参数（机械手册 '[图]'/'[图：…]' 单元格实测）；末行不加——
                # \bottomrule 的 \noalign 必须紧跟 \\（Misplaced \noalign 599 实测）
                tail = " \\\\ \\relax" if ri < len(rows) - 2 else " \\\\"
                out.append(" & ".join(_emit_tokens_tex(c, fns, in_cell=True) for c in r)
                           + tail)
            out.append("\\bottomrule\\end{longtable}\n")
    if orphans:
        # 未锚定尾注：章末显式注释列表（内容不丢）
        out.append(f"\n\\paragraph{{{_notes_label(lang)}}}\n\\begin{{itemize}}")
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
    """把图片复制到 out_dir/images/，返回相对链接前缀 'images/'。无图则空目录。
    顺手带封面：工作目录根的 cover.jpg（stage3 封面提取产物）一并复制。"""
    dst = out_dir / "images"
    dst.mkdir(exist_ok=True)
    if images_dir and Path(images_dir).is_dir():
        for f in Path(images_dir).iterdir():
            if f.is_file():
                shutil.copy2(f, dst / f.name)
    for cand in (out_dir / "cover.jpg", out_dir.parent / "cover.jpg"):
        if cand.is_file():
            shutil.copy2(cand, dst / "cover.jpg")
            break
    return "images"


def export_markdown(units: list, out_dir: Path, book_name: str, title: str,
                    images_dir: str, *, split: bool = False,
                    dialect: str = "gfm", lang: str = "zh") -> list[Path]:
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
        index_lines = [front, f"\n## {_TOC_LABEL.get(lang, 'Contents')}\n"]
        idx = 0
        for u in units:
            md = render_unit_md(u, dialect, lang)
            fn = f"chapters/{_slug(u['title'], idx)}.md"
            (bundle / fn).write_text(md, encoding="utf-8")
            index_lines.append(f"- [{u['title']}]({fn})")
            idx += 1
        idx_path = bundle / "index.md"
        if (bundle / "images" / "cover.jpg").is_file():
            index_lines.insert(1, "\n![封面](images/cover.jpg)\n")
        idx_path.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
        return [bundle]
    _copy_images(images_dir, out_dir)
    if (out_dir / "images" / "cover.jpg").is_file():
        front += "\n![封面](images/cover.jpg)\n"
    body = front + "\n" + "\n\n".join(render_unit_md(u, dialect, lang) for u in units)
    md_path = out_dir / f"{_safe_stem(book_name)}.md"
    md_path.write_text(body, encoding="utf-8")
    return [md_path]


_TEX_PREAMBLE_ZH = r"""% !TeX program = xelatex
\documentclass[UTF8,12pt]{ctexbook}
\IfFontExistsTF{Times New Roman}{\setmainfont{Times New Roman}}{%
  \IfFontExistsTF{Liberation Serif}{\setmainfont{Liberation Serif}}{%
    \IfFontExistsTF{Noto Serif}{\setmainfont{Noto Serif}}{}}}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{array}
\usepackage{longtable}
\usepackage{float}
\usepackage{caption}
\usepackage{extarrows}
\usepackage{yhmath}
\usepackage{slashed}
\providecommand{\overparen}[1]{\wideparen{#1}}
\usepackage{hyperref}
"""
_TEX_PREAMBLE_EN = r"""% !TeX program = xelatex
\documentclass[12pt]{book}
\usepackage{fontspec}
\IfFontExistsTF{Times New Roman}{\setmainfont{Times New Roman}}{%
  \IfFontExistsTF{Liberation Serif}{\setmainfont{Liberation Serif}}{%
    \IfFontExistsTF{Noto Serif}{\setmainfont{Noto Serif}}{}}}
\usepackage{xeCJK}
\IfFontExistsTF{Yu Gothic}{\setCJKmainfont{Yu Gothic}}{%
  \IfFontExistsTF{Microsoft YaHei}{\setCJKmainfont{Microsoft YaHei}}{%
    \IfFontExistsTF{SimSun}{\setCJKmainfont{SimSun}}{%
      \IfFontExistsTF{Noto Sans CJK SC}{\setCJKmainfont{Noto Sans CJK SC}}{}}}}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{array}
\usepackage{longtable}
\usepackage{float}
\usepackage{caption}
\usepackage{extarrows}
\usepackage{yhmath}
\usepackage{slashed}
\providecommand{\overparen}[1]{\wideparen{#1}}
\usepackage{hyperref}
"""
_TEX_PREAMBLE_JA = r"""% !TeX program = xelatex
\documentclass[12pt]{book}
\usepackage{xeCJK}
\IfFontExistsTF{Yu Gothic}{\setCJKmainfont{Yu Gothic}}{%
  \IfFontExistsTF{MS Gothic}{\setCJKmainfont{MS Gothic}}{%
    \IfFontExistsTF{Noto Sans CJK JP}{\setCJKmainfont{Noto Sans CJK JP}}{%
      \IfFontExistsTF{MS Mincho}{\setCJKmainfont{MS Mincho}}{}}}}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{array}
\usepackage{longtable}
\usepackage{float}
\usepackage{caption}
\usepackage{extarrows}
\usepackage{yhmath}
\usepackage{slashed}
\providecommand{\overparen}[1]{\wideparen{#1}}
\usepackage{hyperref}
"""


def export_tex(units: list, out_dir: Path, book_name: str, title: str,
               images_dir: str, *, full: bool = True, lang: str = "zh",
               author: str = "") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    _copy_images(images_dir, out_dir)
    body = "\n".join(render_unit_tex(u, lang) for u in units)
    if full:
        pre = {"zh": _TEX_PREAMBLE_ZH, "ja": _TEX_PREAMBLE_JA}.get(
            lang, _TEX_PREAMBLE_EN)
        cover = ("\\begin{center}\\includegraphics[width=0.8\\linewidth]"
                 "{images/cover.jpg}\\end{center}\n"
                 if (out_dir / "images" / "cover.jpg").is_file() else "")
        doc = (pre + f"\\title{{{_tex_escape(title)}}}\n"
               + (f"\\author{{{_tex_escape(author)}}}\n" if author else "")
               + "\\begin{document}\n\\maketitle\n" + cover
               + "\\tableofcontents\n"
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
    book_lang = meta.get("language", "zh") or "zh"

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
                split=md_split, dialect=md_dialect, lang=book_lang))
        if "tex" in formats:
            _report(f"导出 TeX（{lang}）…", None)
            produced.setdefault("tex", []).extend(export_tex(
                units, work_dir, name, title, images_dir,
                full=tex_full, lang=book_lang, author=author))
    logger.info(f"  导出完成: {{{', '.join(f'{k}: {len(v)}' for k, v in produced.items())}}}")
    return produced
