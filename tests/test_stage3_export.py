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
        self.assertIn("\\chapter{第一章 测试}", tex)
        self.assertIn("$E=mc^2$", tex)
        self.assertIn("\\[H = \\sum_i p_i \\dot q_i - L \\qquad (3.47)\\]", tex)
        self.assertIn("\\footnote{参见《某书》第 7 页。}", tex)          # 内联脚注
        self.assertIn("含 \\& 符号 \\& 特殊\\%字符", tex)               # 特殊字符转义
        self.assertIn("\\includegraphics[width=\\linewidth]{images/p0033_0.png}", tex)
        self.assertIn("\\begin{longtable}", tex)
        self.assertIn("\\toprule", tex)

    def test_full_vs_fragment(self):
        with tempfile.TemporaryDirectory() as td:
            full = export_tex([_unit()], Path(td), "t", "测试书", "", full=True, zh=True)[0]
            frag = export_tex([_unit()], Path(td), "t2", "测试书", "", full=False, zh=True)[0]
            self.assertIn("\\documentclass", full.read_text(encoding="utf-8"))
            self.assertIn("ctexbook", full.read_text(encoding="utf-8"))
            self.assertNotIn("\\documentclass", frag.read_text(encoding="utf-8"))


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
        # 单文件 md / tex 与 images/ 同邻相对引用：共享 images 目录随产物交付（只挂一次）
        td = self._mk_workdir(with_trans=False)
        imgs = Path(td) / "vlm" / "images"
        imgs.mkdir(exist_ok=True)
        (imgs / "p0001_0.png").write_bytes(b"\x89PNG")
        r = export_book(td, {"md", "tex"})
        dirs = [p for f in ("md", "tex") for p in r.get(f, []) if p.is_dir()]
        self.assertEqual([p.name for p in dirs], ["images"])
        self.assertTrue((Path(td) / "images" / "p0001_0.png").is_file())

    def test_no_trans_falls_back_orig(self):
        td = self._mk_workdir(with_trans=False)
        r = export_book(td, {"md", "tex"}, export_lang="auto")
        self.assertIn("正文。", r["md"][0].read_text(encoding="utf-8"))
        self.assertTrue(r["tex"][0].read_text(encoding="utf-8").startswith("% !TeX"))


class TestOrphanFootnotes(unittest.TestCase):
    """未锚定尾注（aside 无 id）：md 退化为显式注释列表、tex 注释列表，
    绝不产 [^]: 空标（QFT 90 处上标数字尾注实测）。"""
    _HTML_ORPHAN = """
    <p>正文一段。</p>
    <aside class="footnotes"><hr/>
    <p class="footnote">² 无锚尾注内容。</p></aside>
    """

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
