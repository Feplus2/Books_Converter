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

from unittest import mock  # noqa: E402

import stage1_mineru  # noqa: E402
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


class RunMineruCleanupTest(unittest.TestCase):
    """病例 054 回归：run_tag 曾是 _split_pdf_chunks 局部变量，run_mineru 的
    finally 清理块引用它必 NameError——切片大书即使全部解析成功、缓存落盘
    也报 Stage 1 失败（秦汉史讲义实测）。修复后：run_tag 提到 run_mineru
    顶层，finally 只清本进程切片。"""

    def _run_chunked(self, td: str, pdf: Path):
        """max_pages=3 强制走物理切片路径；返回 (info, chunks_dir)。"""
        real_split = stage1_mineru._split_pdf_chunks

        def split_small(pdf_path, tmp_dir, **kw):
            return real_split(pdf_path, tmp_dir, max_pages=3, **kw)

        fake = _DoneMinerU("t")
        with mock.patch.object(stage1_mineru, "_split_pdf_chunks", split_small), \
                mock.patch.object(stage1_mineru, "MinerU", lambda _t: fake):
            info = stage1_mineru.run_mineru(str(pdf), td)
        chunks_dir = Path(td) / "mineru" / "_chunks"
        return info, chunks_dir, fake

    def test_finally_cleanup_no_crash(self):
        """切片大书全流程：finally 不炸，返回 dict 正常，缓存落盘。"""
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "big.pdf"
            _make_pdf(pdf, [None] * 7)  # 7 页 → max_pages=3 切 3 片
            info, chunks_dir, fake = self._run_chunked(td, pdf)
            self.assertEqual(fake.calls, 3)  # 3 片各解析一次
            self.assertIn("markdown", info)
            self.assertEqual(info["failed_pages"], [])
            self.assertEqual(len(info["content_list"]), 7)
            # 本进程切片已清、目录已删
            self.assertFalse(chunks_dir.exists())
            # md / content_list 缓存落盘（Stage 1 复用的载体）
            self.assertTrue((Path(td) / "mineru" / "big.md").exists())
            self.assertTrue(
                (Path(td) / "mineru" / "big_content_list.json").exists())

    def test_finally_keeps_foreign_chunks(self):
        """047 语义保持：他进程残留切片（run_tag 不同）绝不动。"""
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "big.pdf"
            _make_pdf(pdf, [None] * 7)
            chunks_dir = Path(td) / "mineru" / "_chunks"
            chunks_dir.mkdir(parents=True)
            foreign = chunks_dir / "_chunk_deadbeef_000_1-3.pdf"
            foreign.write_bytes(b"%PDF-foreign-leftover")
            info, _cd, _f = self._run_chunked(td, pdf)
            self.assertIn("markdown", info)
            self.assertTrue(foreign.exists())  # 他进程切片原样保留
            # 本进程切片清零（目录因 foreign 占位而留存属预期）
            own = [p for p in chunks_dir.iterdir()
                   if p.name.startswith(f"_chunk_{os.getpid():x}_")]
            self.assertEqual(own, [])

    def test_finally_cleanup_on_failure(self):
        """解析中途抛错：finally 照常清本进程切片，原异常透出（不二次炸）。"""
        import time as _real_time
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "big.pdf"
            _make_pdf(pdf, [None] * 7)
            real_split = stage1_mineru._split_pdf_chunks

            def split_small(pdf_path, tmp_dir, **kw):
                return real_split(pdf_path, tmp_dir, max_pages=3, **kw)

            class _BoomMinerU:
                def __init__(self, _t):
                    pass

                def extract(self, path, **kw):
                    raise ValueError("构造的中途崩溃")

                def close(self):
                    pass

            # 网络层重试的 10s/20s 睡等待打桩掉（只测 finally 语义）
            fake_time = mock.Mock(wraps=_real_time)
            fake_time.sleep = lambda *_a, **_kw: None
            with mock.patch.object(stage1_mineru, "_split_pdf_chunks",
                                   split_small), \
                    mock.patch.object(stage1_mineru, "MinerU",
                                      lambda _t: _BoomMinerU("t")), \
                    mock.patch.object(stage1_mineru, "time", fake_time):
                with self.assertRaises(RuntimeError):
                    stage1_mineru.run_mineru(str(pdf), td)
            chunks_dir = Path(td) / "mineru" / "_chunks"
            if chunks_dir.exists():
                own = [p for p in chunks_dir.iterdir()
                       if p.name.startswith(f"_chunk_{os.getpid():x}_")]
                self.assertEqual(own, [])


class _DoneMinerU:
    """永远解析成功的 MinerU stub（逐片返回该页数的假内容）。"""

    def __init__(self, token):
        self.calls = 0

    def extract(self, path, **kw):
        import fitz as _f
        doc = _f.open(path)
        n = doc.page_count
        doc.close()
        self.calls += 1

        class _R:
            state = "done"
            markdown = "\n".join(f"第{i + 1}页内容" for i in range(n))
            content_list = [{"type": "text", "text": f"第{i + 1}页内容",
                             "page_idx": i} for i in range(n)]
            images = []
            error = None

        return _R()

    def close(self):
        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
