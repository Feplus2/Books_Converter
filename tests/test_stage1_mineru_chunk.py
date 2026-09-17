# -*- coding: utf-8 -*-
"""MinerU 双天花板物理切片回归（T6）：页数/字节哪个先到切哪刀。

运行：.venv/Scripts/python tests/test_stage1_mineru_chunk.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from stage1_mineru import _split_pdf_chunks  # noqa: E402


def _noise_jpeg(w: int, h: int, quality: int = 85) -> bytes:
    """随机噪点 JPEG（近不可压缩，模拟高清扫描页）。"""
    import io
    from PIL import Image
    img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _make_pdf(path: Path, page_specs: list) -> int:
    """page_specs: None=空白小页 / (w,h) 噪点图页。返回文件字节数。"""
    doc = fitz.open()
    for spec in page_specs:
        page = doc.new_page(width=612, height=792)
        if spec:
            page.insert_image(page.rect, stream=_noise_jpeg(*spec))
    doc.save(str(path), deflate=True, garbage=3)
    doc.close()
    return path.stat().st_size


def _coverage_ok(chunks: list, total: int) -> bool:
    expect = 0
    for _p, s, e in chunks:
        if s != expect or e < s:
            return False
        expect = e + 1
    return expect == total


class SplitChunksTest(unittest.TestCase):
    def test_small_book_zero_copy(self):
        """整书达标 → 直接用原文件，不产临时切片。"""
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "small.pdf"
            _make_pdf(pdf, [None] * 5)
            chunks = _split_pdf_chunks(pdf, Path(td) / "_c",
                                       max_pages=200, max_bytes=150 * 1024 * 1024)
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0][0], pdf)  # 原文件零拷贝
            self.assertEqual((chunks[0][1], chunks[0][2]), (0, 4))
            self.assertFalse((Path(td) / "_c").exists())

    def test_page_ceiling(self):
        """页数天花板：7 页按 max_pages=3 切 3 片，保序无重叠。"""
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "pages.pdf"
            _make_pdf(pdf, [None] * 7)
            chunks = _split_pdf_chunks(pdf, Path(td) / "_c",
                                       max_pages=3, max_bytes=150 * 1024 * 1024)
            self.assertEqual([(s, e) for _p, s, e in chunks],
                             [(0, 2), (3, 5), (6, 6)])
            self.assertTrue(_coverage_ok(chunks, 7))

    def test_byte_ceiling_halving(self):
        """字节天花板：噪点页对半递归，每片实测 ≤ max_bytes。"""
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "big.pdf"
            _make_pdf(pdf, [(600, 780)] * 10)  # 每页 ~100KB+ 噪点
            max_b = 400 * 1024
            chunks = _split_pdf_chunks(pdf, Path(td) / "_c",
                                       max_pages=200, max_bytes=max_b)
            self.assertGreater(len(chunks), 1)
            self.assertTrue(_coverage_ok(chunks, 10))
            for p, s, e in chunks:
                sz = p.stat().st_size
                if s == e:  # 单页片允许走降采样路径
                    continue
                self.assertLessEqual(sz, max_b, f"{p.name} 超阈值")

    def test_single_giant_page_downsampled(self):
        """单页超阈值：降采样 JPEG 重嵌后 ≤ max_bytes，且仍是合法单页 PDF。"""
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "giant.pdf"
            _make_pdf(pdf, [(2400, 3200)])  # 整页高清噪点
            raw = pdf.stat().st_size
            max_b = 1024 * 1024
            self.assertGreater(raw, max_b)  # 前提：原页确实超阈值
            chunks = _split_pdf_chunks(pdf, Path(td) / "_c",
                                       max_pages=200, max_bytes=max_b)
            self.assertEqual(len(chunks), 1)
            p, s, e = chunks[0]
            self.assertNotEqual(p, pdf)  # 不能是原文件
            self.assertLessEqual(p.stat().st_size, max_b)
            doc = fitz.open(str(p))
            self.assertEqual(doc.page_count, 1)
            doc.close()

    def test_mixed_giant_and_normal(self):
        """巨页夹普通页：覆盖完整，巨页片单独成切且达标。"""
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "mix.pdf"
            _make_pdf(pdf, [None, None, (2400, 3200), None, None])
            max_b = 1024 * 1024
            chunks = _split_pdf_chunks(pdf, Path(td) / "_c",
                                       max_pages=200, max_bytes=max_b)
            self.assertTrue(_coverage_ok(chunks, 5))
            for p, s, e in chunks:
                self.assertLessEqual(p.stat().st_size, max_b * 1.05)


if __name__ == "__main__":
    unittest.main(verbosity=2)
