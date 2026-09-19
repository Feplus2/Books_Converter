"""导出器单测（tests/test_stage3_export.py）：md/tex 双发射器 + 编排选项。
全合成数据，无网络。

运行：.venv/Scripts/python.exe tests/test_stage3_export.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stage3_export import (build_units, export_book, export_markdown, export_tex,
                           render_unit_md, render_unit_tex)

_HTML = """
<h2>第一章 测试</h2>
<p>正文一段，含行内公式 <math alttext="E=mc^2" display="inline"></math> 与
<a epub:type="noteref" id="fnref_1" href="#fn_1">①</a> 脚注引用。</p>
<p><math alttext="H = \\sum_i p_i \\dot q_i - L \\qquad (3.47)" display="block"></math></p>
<p>含 &amp; 符号 &amp; 特殊%字符 的段落。</p>
<blockquote>引用块。</blockquote>
<img src="images/p0033_0.png" alt="图1-1 示例"/>
<table><thead><tr><th>情绪</th><th>程度</th></tr></thead>
<tbody><tr><td>焦虑 | 紧张</td><td>80%</td></tr></tbody></table>
<aside class="footnotes"><hr/><p class="footnote" id="fn_1"><a href="#fnref_1">①</a> 参见《某书》第 7 页。</p></aside>
"""


def _unit(kind="chapter", title="第一章 测试"):
    return {"kind": kind, "title": title, "parts": [_HTML], "subs": []}


class TestMarkdown(unittest.TestCase):
    def test_basic_shapes(self):
        md = render_unit_md(_unit())
        self.assertIn("# 第一章 测试", md)
        self.assertIn("$E=mc^2$", md)                                  # 行内
        self.assertIn("$$H = \\sum_i p_i \\dot q_i - L \\qquad (3.47)$$", md)  # display
        self.assertIn("[^1]", md)                                      # 脚注引用
        self.assertIn("[^1]: 参见《某书》第 7 页。", md)                  # 脚注定义（回链已剥）
        self.assertIn("![图1-1 示例](images/p0033_0.png)", md)
        self.assertIn("\n\n$$H = \\sum_i", md)  # display 公式空行环绕成独立段落（居中渲染前提）
        self.assertIn("| 情绪 | 程度 |", md)
        self.assertIn("焦虑 \\| 紧张", md)                             # 单元格 | 转义
        self.assertIn("> 引用块。", md)
        self.assertNotIn("<", md)                                      # 无 HTML 残留

    def test_special_chars_not_escaped_in_md(self):
        md = render_unit_md(_unit())
        self.assertIn("含 & 符号 & 特殊%字符", md)                     # md 不需转义


class TestTex(unittest.TestCase):
    def test_basic_shapes(self):
        tex = render_unit_tex(_unit())
        self.assertIn("\\chapter*{第一章 测试}", tex)   # 星号命令+手动入 TOC（标题自带书编号）
        self.assertIn("\\addcontentsline{toc}{chapter}{第一章 测试}", tex)
        self.assertIn("$E=mc^2$", tex)
        self.assertIn("\\[H = \\sum_i p_i \\dot q_i - L \\qquad (3.47)\\]", tex)
        self.assertIn("\\footnote{参见《某书》第 7 页。}", tex)          # 内联脚注
        self.assertIn("含 \\& 符号 \\& 特殊\\%字符", tex)               # 特殊字符转义
        self.assertIn("\\begin{figure}[H]", tex)            # 图片精确就位不漂移
        self.assertIn("\\includegraphics[width=\\linewidth]{images/p0033_0.png}", tex)
        self.assertIn("\\begin{longtable}", tex)
        self.assertIn("\\toprule", tex)

    def test_full_vs_fragment(self):
        with tempfile.TemporaryDirectory() as td:
            full = export_tex([_unit()], Path(td), "t", "测试书", "", full=True, lang="zh")[0]
            frag = export_tex([_unit()], Path(td), "t2", "测试书", "", full=False, lang="zh")[0]
            self.assertIn("\\documentclass", full.read_text(encoding="utf-8"))
            self.assertIn("ctexbook", full.read_text(encoding="utf-8"))
            self.assertNotIn("\\documentclass", frag.read_text(encoding="utf-8"))

    def test_full_first_line_xelatex_magic(self):
        """病例 056 防呆：完整文档首行 = xelatex magic comment
        （TeXworks/TeXShop/LaTeX-Workshop 据此自动选 xelatex，
        防 pdflatex 误编译中文文档）。"""
        with tempfile.TemporaryDirectory() as td:
            for lang in ("zh", "en", "ja"):
                p = export_tex([_unit()], Path(td), f"t-{lang}", "测试书", "",
                               full=True, lang=lang)[0]
                first = p.read_text(encoding="utf-8").splitlines()[0]
                self.assertEqual(first, "% !TeX program = xelatex")

    def test_fragment_header_warns_not_compilable(self):
        """病例 056 防呆：片段首行起为注释警告块（不能直接编译、\\input
        用法、开启「完整 TeX 文档」指引），且正文内容不受注释影响。"""
        with tempfile.TemporaryDirectory() as td:
            p = export_tex([_unit()], Path(td), "t", "测试书", "",
                           full=False, lang="zh")[0]
            txt = p.read_text(encoding="utf-8")
            lines = txt.splitlines()
            self.assertTrue(lines[0].startswith("%"))
            self.assertIn("不能直接编译", txt)
            self.assertIn("\\input", txt)
            self.assertIn("完整 TeX 文档", txt)
            # 注释不影响既有内容（正文断言与整文档形态一致）
            self.assertIn("\\chapter*{第一章 测试}", txt)
            self.assertNotIn("\\documentclass", txt)

    def test_booktabs_rule_relax(self):
        """booktabs 规则后 \\relax：首列以 '(' 起头的行不被吞成 trim 参数
        （FG '(Sad) blue…' 单元格 207 个 Undefined control sequence 实测）。"""
        tex = render_unit_tex(_unit())
        self.assertIn("\\toprule\\relax", tex)
        self.assertIn("\\midrule\\relax", tex)
        self.assertNotIn("\rel", tex.replace("\\relax", ""))  # 防 \r 回车逃逸

    def test_empty_body_table_midrule_no_relax(self):
        """病例 056 编译验证实测：单行表（仅表头无数据行）\midrule\relax
        紧跟 \bottomrule 断 \noalign 连锁（Misplaced \noalign）→ 空表体
        省略 \\relax；有数据行时保留（FG '(' 防护不回归）。"""
        one_row = {"kind": "chapter", "title": "章",
                   "parts": ["<table><thead><tr><th>甲</th><th>乙</th></tr></thead>"
                             "<tbody></tbody></table>"], "subs": []}
        tex1 = render_unit_tex(one_row)
        self.assertIn("\\midrule\n", tex1)          # 空表体：无 \relax
        self.assertNotIn("\\midrule\\relax", tex1)
        two_row = {"kind": "chapter", "title": "章",
                   "parts": ["<table><thead><tr><th>甲</th><th>乙</th></tr></thead>"
                             "<tbody><tr><td>(Sad) blue</td><td>x</td></tr>"
                             "</tbody></table>"], "subs": []}
        tex2 = render_unit_tex(two_row)
        self.assertIn("\\midrule\\relax", tex2)     # 有数据行：\relax 保留

    def test_preamble_lang_selection(self):
        """ja → xeCJK+日文字体（izuno lmroman 缺字 xelatex 段错误实测）；
        三 preamble 全带 slashed（QFT \\slashed undefined 实测）。"""
        with tempfile.TemporaryDirectory() as td:
            ja = export_tex([_unit()], Path(td), "ja", "伊豆の踊子", "",
                            full=True, lang="ja")[0].read_text(encoding="utf-8")
            self.assertIn("xeCJK", ja)
            self.assertIn("Yu Gothic", ja)
            self.assertIn("slashed", ja)
            en = export_tex([_unit()], Path(td), "en", "QFT", "",
                             full=True, lang="en")[0].read_text(encoding="utf-8")
            self.assertIn("slashed", en)
            self.assertIn("xeCJK", en)  # EN 也带 CJK 兜底（[插图：]占位符实测）
            # fontspec 必须先于 \IfFontExistsTF（EN preamble 顺序事故实测）
            self.assertLess(en.find("fontspec"), en.find("IfFontExistsTF"))

    def test_sub_accent_atom(self):
        """E_\\bar\\nu → E_{\\bar{\\nu}}（QFT l.8272 Missing { 实测）。"""
        from stage3_export import _tex_math_sanitize
        self.assertEqual(_tex_math_sanitize(r"E_\bar\nu"), r"E_{\bar{\nu}}")
        self.assertEqual(_tex_math_sanitize(r"x_\hat p"), r"x_{\hat{p}}")
        # 已带花括号的原子不动（不动作）
        self.assertEqual(_tex_math_sanitize(r"E_{\bar{\nu}}"), r"E_{\bar{\nu}}")

    def test_ctrl_chars_stripped(self):
        """C0 控制字符（VLM 工件，must_defend U+0019 实测）进 tex 前剥除。"""
        from stage3_export import _tex_escape
        self.assertEqual(_tex_escape("abc\x19def"), "abcdef")
        self.assertEqual(_tex_escape("换行\n保留\t制表"), "换行\n保留\t制表")

    def test_checkmark_mapped(self):
        """✓/✔ → $\\checkmark$（FG 量表 (✓) 列 lmroman 缺字形实测）。"""
        from stage3_export import _tex_escape, _tex_math_sanitize
        self.assertEqual(_tex_escape("(✓)"), r"($\checkmark$)")
        self.assertEqual(_tex_math_sanitize("✓"), r"\checkmark")

    def test_notes_label_localized(self):
        """尾注标题随书语言：英文书 'Notes'（FG '注释' 缺字形实测），日文 '注'。"""
        html = ('<p>正文。</p><aside class="footnotes"><hr/>'
                '<p class="footnote">¹ orphan.</p></aside>')
        unit = {"kind": "chapter", "title": "Ch", "parts": [html], "subs": []}
        self.assertIn("\\paragraph{Notes}", render_unit_tex(unit, lang="en"))
        self.assertIn("\\paragraph{注释}", render_unit_tex(unit, lang="zh"))
        self.assertIn("**Notes**", render_unit_md(unit, lang="en"))

    def test_display_math_demoted_in_cell(self):
        """单元格内 display 数学降级行内（机械手册 21 处 Missing $ 级联实测）。"""
        html = ('<table><thead><tr><th>公式</th><th>说明</th></tr></thead>'
                '<tbody><tr><td><math alttext="\\Delta P" display="block"></math></td>'
                '<td>单元格</td></tr></tbody></table>')
        tex = render_unit_tex({"kind": "chapter", "title": "Ch", "parts": [html], "subs": []})
        self.assertNotIn("\\[\n", tex)
        self.assertIn("$\\Delta P$", tex)

    def test_col_spec_weighted_p(self):
        """longtable 列宽加权 p 列（'l' 列 4386pt Overfull 实测）。"""
        from stage3_export import _col_spec_tex
        rows = [["型号", "一段很长很长很长很长很长很长很长很长的说明文字"], ["G1", "短"]]
        spec = _col_spec_tex(rows, 2)
        self.assertIn("\\raggedright", spec)
        self.assertIn("p{", spec)
        self.assertNotIn("}{l}", spec)

    def test_bracket_cell_after_rowbreak(self):
        """非末行 \\ 后 \\relax：'[' 起头单元格不被吞成 \\\\[dimen]（机械手册
        '[图]'/'[图：…]' 单元格实测）；末行无 \\relax——\\bottomrule 的
        \\noalign 必须紧跟 \\\\（Misplaced \\noalign 599 实测）。"""
        html = ('<table><thead><tr><th>A</th><th>B</th></tr></thead>'
                '<tbody><tr><td>x</td><td>y</td></tr>'
                '<tr><td>[图]</td><td>说明</td></tr></tbody></table>')
        tex = render_unit_tex({"kind": "chapter", "title": "Ch", "parts": [html], "subs": []})
        self.assertRegex(tex, r"x & y \\\\ \\relax")
        self.assertRegex(tex, r"\[图\] & 说明 \\\\(\s*)\\bottomrule")
        self.assertNotIn("\\relax\\bottomrule", tex)

    def test_fullwidth_space_mapped(self):
        """U+3000 全角空格 → 空格（Times 无字形，机械手册 1582 处实测）。"""
        from stage3_export import _tex_escape
        self.assertEqual(_tex_escape("值　≦　☆"), "值 $\\leqq$ $\\star$")

    def test_text_shell_with_superscript_unwrapped(self):
        """\\text{^\\circ\\mathrm{C}} 文本壳塞上下标必炸 → 纯 ASCII 拆壳
        （机械手册 Missing $ 实测）；含 CJK 的合法 \\text 不动。"""
        from stage3_export import _tex_math_sanitize
        self.assertEqual(_tex_math_sanitize(r"(\text{^\circ\mathrm{C}})"),
                         r"(^\circ\mathrm{C})")
        # ℃ 先被 unicode 映射进壳内再拆（顺序事故，机械手册 l.12350 实测）
        self.assertEqual(_tex_math_sanitize(r"(\text{℃})"), r"(^\circ\mathrm{C})")
        self.assertEqual(_tex_math_sanitize(r"\text{实际值}"), r"\text{实际值}")


class TestMarkdownLayout(unittest.TestCase):
    def test_single_vs_split(self):
        with tempfile.TemporaryDirectory() as td:
            units = [_unit("chapter", "第一章 甲"), _unit("chapter", "第二章 乙")]
            single = export_markdown(units, Path(td) / "s", "书", "书", "", split=False)
            self.assertEqual(len(single), 1)
            self.assertIn("第二章 乙", single[0].read_text(encoding="utf-8"))
            split = export_markdown(units, Path(td) / "sp", "书", "书", "", split=True)
            # 分章形态自含交付：返回 <书名>_md/ 目录，内含 index.md + chapters/ + images/
            self.assertEqual(split, [Path(td) / "sp" / "书_md"])
            bundle = split[0]
            idx = (bundle / "index.md").read_text(encoding="utf-8")
            self.assertIn("chapters/00-第一章_甲.md", idx)
            self.assertTrue((bundle / "chapters" / "01-第二章_乙.md").is_file())
            self.assertTrue((bundle / "images").is_dir())

    def test_pandoc_front_matter(self):
        with tempfile.TemporaryDirectory() as td:
            out = export_markdown([_unit()], Path(td), "书", "我的书", "", dialect="pandoc")
            self.assertTrue(out[0].read_text(encoding="utf-8").startswith("---\ntitle: 我的书"))


_TABLE_MATH_HTML = """
<h2>第二章 微分</h2>
<table><thead><tr><th>导数公式</th></tr></thead>
<tbody><tr><td><math alttext="(x^{\\mu})#x27;=\\mu x^{\\mu-1}" display="inline"></math> 链式</td></tr></tbody></table>
<p>公式尾脚注 <math alttext="E=1\\;&lt;a href=&quot;#fn_1&quot;&gt;&lt;sup&gt;①&lt;/sup&gt;&lt;/a&gt;\\;," display="block"></math></p>
"""


class TestCleanLatex(unittest.TestCase):
    def test_alttext_sanitize(self):
        from stage3_export import _clean_latex
        # 实体残骸修复 + 泄漏标签剥除 + 不误杀正常 LaTeX
        self.assertEqual(_clean_latex("(x^{\\mu})#x27;=\\mu"), "(x^{\\mu})'=\\mu")
        self.assertEqual(_clean_latex('E=1\\;<a href="#fn_1"><sup>①</sup></a>\\;,'),
                         "E=1\\;①\\;,")
        self.assertEqual(_clean_latex("\\frac{1}{2}"), "\\frac{1}{2}")

    def test_table_cell_math_survives(self):
        unit = {"kind": "chapter", "title": "第二章 微分",
                "parts": [_TABLE_MATH_HTML], "subs": []}
        md = render_unit_md(unit)
        self.assertIn("$(x^{\\mu})'=\\mu x^{\\mu-1}$", md)  # 单元格公式不再扁平
        self.assertNotIn("#x27", md)
        self.assertIn("$$E=1\\;①\\;,$$", md)                # alttext 标签剥除
        tex = render_unit_tex(unit)
        self.assertIn("$(x^{\\mu})'=\\mu x^{\\mu-1}$", tex)
        self.assertNotIn("#x27", tex)
        self.assertIn("\\text{\\textcircled{1}}", tex)  # 数学模式圈码走 \textcircled


class TestExportBookLang(unittest.TestCase):
    def _mk_workdir(self, with_trans: bool) -> Path:
        td = Path(tempfile.mkdtemp())
        (td / "structure.json").write_text(json.dumps({
            "engine": "vlm", "metadata": {"title": "测试书", "language": "zh"},
            "toc_entries": [], "front_matter": [], "back_matter": [],
            "popo_blocks_file": "popo_blocks.json"}), encoding="utf-8")
        (td / "popo_blocks.json").write_text(json.dumps([
            {"id": 0, "type": "title", "content": "第一章 测试", "level": 1, "page": 1, "source_id": "t:0"},
            {"id": 1, "type": "text", "content": "正文。", "page": 1, "source_id": "t:1"},
        ]), encoding="utf-8")
        (td / "vlm").mkdir()
        (td / "vlm" / "x_content_list.json").write_text(json.dumps([
            {"type": "text", "text": "x", "bbox": [0, 0, 1, 1], "page_idx": 0}]), encoding="utf-8")
        if with_trans:
            (td / "translations.json").write_text(json.dumps({"t:1": "译文。"}), encoding="utf-8")
        return td

    def test_lang_selection(self):
        td = self._mk_workdir(with_trans=True)
        r = export_book(td, {"md"}, export_lang="both")
        names = [p.name for p in r["md"]]
        self.assertIn("测试书_原文.md", names)
        self.assertIn("测试书.md", names)
        # auto：有译文用译文
        r2 = export_book(td, {"md"}, export_lang="auto")
        self.assertEqual([p.name for p in r2["md"]], ["测试书.md"])
        # orig
        r3 = export_book(td, {"md"}, export_lang="orig")
        self.assertEqual([p.name for p in r3["md"]], ["测试书.md"])
        self.assertIn("正文。", r3["md"][0].read_text(encoding="utf-8"))

    def test_images_dir_shipped_with_products(self):
        # images/ 落在工作目录与产物同邻；produced 只报文件/目录本体，
        # 交付层（pipeline._deliver）按 <书名>/<格式>/ 复制并附带 images/
        td = self._mk_workdir(with_trans=False)
        imgs = Path(td) / "vlm" / "images"
        imgs.mkdir(exist_ok=True)
        (imgs / "p0001_0.png").write_bytes(b"\x89PNG")
        r = export_book(td, {"md", "tex"})
        self.assertTrue((Path(td) / "images" / "p0001_0.png").is_file())
        self.assertTrue(all(p.is_file() for f in ("md", "tex") for p in r[f]))

    def test_front_back_matter_included(self):
        # 前页（前言 p1 keep）与后页（附录 p3 keep）不丢：md/tex 导出包含
        td = self._mk_workdir(with_trans=False)
        st = json.loads((Path(td) / "structure.json").read_text(encoding="utf-8"))
        st["front_matter"] = [{"type": "preface", "label": "前言",
                               "page_start": 1, "page_end": 1, "keep": True}]
        st["back_matter"] = [{"type": "appendix", "label": "附录",
                              "page_start": 3, "page_end": 3, "keep": True}]
        (Path(td) / "structure.json").write_text(
            json.dumps(st, ensure_ascii=False), encoding="utf-8")
        pb = json.loads((Path(td) / "popo_blocks.json").read_text(encoding="utf-8"))
        pb[0]["page"], pb[1]["page"] = 2, 2  # 正文 → p2
        pb.append({"id": 90, "type": "text", "content": "前言正文。",
                   "page": 1, "source_id": "t:1"})
        pb.append({"id": 91, "type": "text", "content": "附录正文。",
                   "page": 3, "source_id": "t:1"})
        (Path(td) / "popo_blocks.json").write_text(
            json.dumps(pb, ensure_ascii=False), encoding="utf-8")
        units = build_units(td, use_translations=False)
        titles = [u["title"] for u in units]
        self.assertEqual(titles[0], "前言")
        self.assertEqual(titles[-1], "附录")
        self.assertIn("第一章 测试", titles)
        self.assertIn("前言正文。", units[0]["parts"][0])

    def test_no_trans_falls_back_orig(self):
        td = self._mk_workdir(with_trans=False)
        r = export_book(td, {"md", "tex"}, export_lang="auto")
        self.assertIn("正文。", r["md"][0].read_text(encoding="utf-8"))
        self.assertTrue(r["tex"][0].read_text(encoding="utf-8").startswith("% !TeX"))

    def test_empty_engine_dir_skipped(self):
        """病例 054：structure.engine=hybrid（非缓存目录名），工作目录残留
        只有 vlm_state.db 的空壳 vlm/ → 必须跳过，继续找有缓存的 mineru/；
        否则空 content_list 让 body_range 塌空，md/tex 只剩前后页（秦汉史实测）。"""
        td = Path(tempfile.mkdtemp())
        (td / "structure.json").write_text(json.dumps({
            "engine": "hybrid", "metadata": {"title": "测试书", "language": "zh"},
            "toc_entries": [], "front_matter": [], "back_matter": [],
            "popo_blocks_file": "popo_blocks.json"}), encoding="utf-8")
        (td / "popo_blocks.json").write_text(json.dumps([
            {"id": 0, "type": "title", "content": "第一章 测试", "level": 1, "page": 1, "source_id": "t:0"},
            {"id": 1, "type": "text", "content": "正文。", "page": 1, "source_id": "t:1"},
        ], ensure_ascii=False), encoding="utf-8")
        # 空壳 vlm/（只有 state db，无缓存）+ 真缓存 mineru/
        (td / "vlm").mkdir()
        (td / "vlm" / "vlm_state.db").write_bytes(b"sqlite")
        (td / "mineru").mkdir()
        (td / "mineru" / "x_content_list.json").write_text(json.dumps([
            {"type": "text", "text": "x", "bbox": [0, 0, 1, 1], "page_idx": 0}]), encoding="utf-8")
        units = build_units(td, use_translations=False)
        self.assertIn("第一章 测试", [u["title"] for u in units])


class TestOrphanFootnotes(unittest.TestCase):
    """未锚定尾注（aside 无 id）：md 退化为显式注释列表、tex 注释列表，
    绝不产 [^]: 空标（QFT 90 处上标数字尾注实测）。"""
    _HTML_ORPHAN = """
    <p>正文一段。</p>
    <aside class="footnotes"><hr/>
    <p class="footnote">² 无锚尾注内容。</p></aside>
    """

    def test_split_chapter_images_up_one_level(self):
        """分章 md：chapters/ 内图片链接 '../images/…'（index.md 在包级保持
        'images/…'）——分章打开全部图片加载失败实测。"""
        from stage3_export import export_markdown
        unit = {"kind": "chapter", "title": "第一章 测试",
                "parts": ['<img src="images/p0001_0.png" alt="图"/>'], "subs": []}
        with tempfile.TemporaryDirectory() as td:
            bundle = export_markdown([unit], Path(td), "t", "测试书", "",
                                     split=True)[0]
            ch = next((bundle / "chapters").glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("](../images/p0001_0.png)", ch)
            idx = (bundle / "index.md").read_text(encoding="utf-8")
            self.assertNotIn("](../images/", idx)

    def test_md_orphan(self):
        md = render_unit_md({"kind": "chapter", "title": "章", "parts": [self._HTML_ORPHAN]})
        self.assertNotIn("[^]:", md)
        self.assertIn("**注释**", md)
        self.assertIn("² 无锚尾注内容。", md)

    def test_tex_orphan(self):
        tex = render_unit_tex({"kind": "chapter", "title": "章", "parts": [self._HTML_ORPHAN]})
        self.assertIn("\\paragraph{注释}", tex)
        self.assertIn("无锚尾注内容", tex)


if __name__ == "__main__":
    unittest.main(verbosity=2)
