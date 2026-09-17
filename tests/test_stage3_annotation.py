# -*- coding: utf-8 -*-
"""MathML annotation 注入（T1）/ img 物理尺寸（T4）/ 输出目录撞名避让回归。

运行：.venv/Scripts/python tests/test_stage3_annotation.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stage3_epub import _latex_to_mathml, _img_size_attr
from pipeline import _unique_book_dir


class MathmlAnnotationTest(unittest.TestCase):
    """阅读器复制公式取 annotation 里的 LaTeX 源码（SageRead 清单 #1）。"""

    def test_inline_has_annotation(self):
        mm = _latex_to_mathml(r"\sqrt{x} + \frac{1}{2}", display=False)
        self.assertIsNotNone(mm)
        self.assertIn('<annotation encoding="application/x-tex">', mm)
        self.assertIn(r"\sqrt{x}", mm.split("application/x-tex", 1)[1])
        self.assertIn('display="inline"', mm)

    def test_display_has_annotation(self):
        mm = _latex_to_mathml(r"E = mc^2", display=True)
        self.assertIn('<annotation encoding="application/x-tex">E = mc^2</annotation>',
                      mm)
        self.assertIn('display="block"', mm)

    def test_annotation_xml_escaped(self):
        """含 < > & 的源码必须实体转义，否则 EPUB 序列化炸 XML。"""
        mm = _latex_to_mathml(r"a < b & c > d", display=False)
        self.assertIsNotNone(mm)
        ann = mm.split("application/x-tex", 1)[1]
        self.assertNotIn("< b &", ann)
        self.assertIn("&lt;", ann)

    def test_annotation_after_sanitize(self):
        """annotation 落的是笔误清洗后的源码（\qqud → \qquad）。"""
        mm = _latex_to_mathml(r"x \qqud (3.47)", display=True)
        self.assertIsNotNone(mm)
        self.assertIn(r"\qquad", mm.split("application/x-tex", 1)[1])
        self.assertNotIn(r"\qqud", mm.split("application/x-tex", 1)[1])

    def test_alttext_still_present(self):
        """annotation 不顶替 alttext 兜底属性。"""
        mm = _latex_to_mathml(r"\alpha", display=False)
        self.assertIn("alttext=", mm)
        self.assertIn("<annotation", mm)


class ImgSizeAttrTest(unittest.TestCase):
    """img 标签写物理像素尺寸，防阅读器滚动占位塌陷（SageRead 清单）。"""

    def test_real_png(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            Image.new("RGB", (640, 480)).save(os.path.join(td, "a.png"))
            self.assertEqual(_img_size_attr(td, "a.png"),
                             ' width="640" height="480"')

    def test_missing_file_empty(self):
        """读不到宁可不写（铁律 0：失败方向 = 不动作）。"""
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(_img_size_attr(td, "ghost.png"), "")

    def test_empty_args_empty(self):
        self.assertEqual(_img_size_attr("", "a.png"), "")
        self.assertEqual(_img_size_attr("/tmp", ""), "")

    def test_cache_hit(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            Image.new("RGB", (10, 20)).save(os.path.join(td, "c.png"))
            first = _img_size_attr(td, "c.png")
            os.remove(os.path.join(td, "c.png"))
            self.assertEqual(_img_size_attr(td, "c.png"), first)


class UniqueBookDirTest(unittest.TestCase):
    """同书名重转不覆盖旧产物：书名/ → 书名 (1)/ → 书名 (2)/…
    只有缓存（无 epub/md/tex 交付产物）不算撞名——重跑复用缓存照常交付。"""

    def test_first_time_plain(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(_unique_book_dir(Path(td), "高等数学"), "高等数学")

    def test_cache_only_not_collision(self):
        """work_dir 与交付根同目录：纯缓存（vlm_state.db 等）不触发避让。"""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "高等数学"
            d.mkdir()
            (d / "vlm_state.db").write_text("cache", encoding="utf-8")
            (d / "structure.json").write_text("{}", encoding="utf-8")
            (d / "images").mkdir()
            self.assertEqual(_unique_book_dir(Path(td), "高等数学"), "高等数学")

    def test_empty_product_dir_not_collision(self):
        """交付中断留下的空格式目录不算产物。"""
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "高等数学" / "epub").mkdir(parents=True)
            self.assertEqual(_unique_book_dir(Path(td), "高等数学"), "高等数学")

    def test_delivered_products_bumped(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            d = base / "高等数学"
            (d / "epub").mkdir(parents=True)
            (d / "epub" / "高等数学.epub").write_text("x", encoding="utf-8")
            self.assertEqual(_unique_book_dir(base, "高等数学"), "高等数学 (1)")
            d1 = base / "高等数学 (1)"
            (d1 / "md").mkdir(parents=True)
            (d1 / "md" / "高等数学.md").write_text("x", encoding="utf-8")
            self.assertEqual(_unique_book_dir(base, "高等数学"), "高等数学 (2)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
