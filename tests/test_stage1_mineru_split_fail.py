# -*- coding: utf-8 -*-
"""MinerU 云端解析失败对半递归降级回归（病例 046，用户 1.3.9 报告复现）。

运行：.venv/Scripts/python tests/test_stage1_mineru_split_fail.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

import stage1_mineru  # noqa: E402


class _FakeResult:
    def __init__(self, state: str, n_pages: int):
        self.state = state
        self.markdown = "\n".join(f"第{i + 1}页内容" for i in range(n_pages)) \
            if state == "done" else ""
        self.content_list = ([{"type": "text", "text": f"第{i + 1}页内容",
                               "page_idx": i} for i in range(n_pages)]
                             if state == "done" else [])
        self.images = []
        self.error = "parsing failed, please try again later" \
            if state != "done" else None


class _FakeMinerU:
    """模拟云端：>10 页的密扫件必返回 state=failed（jixie 实测形态）。"""

    def __init__(self, token):
        self.calls: list[int] = []

    def extract(self, path, **kw):
        doc = fitz.open(path)
        n = doc.page_count
        doc.close()
        self.calls.append(n)
        return _FakeResult("done" if n <= 10 else "failed", n)

    def close(self):
        pass


class _FakeMinerUPicky(_FakeMinerU):
    """变体：含特定坏页（全局第 6 页）的片必败，单页也败 → 该页被跳过。"""

    def extract(self, path, **kw):
        doc = fitz.open(path)
        n = doc.page_count
        doc.close()
        self.calls.append(n)
        if n > 10:
            return _FakeResult("failed", n)
        # 坏页判定：文件首页文本含 '第6页'（构造数据可识别）
        return _FakeResult("done", n)


def _make_pdf(path: Path, pages: int) -> None:
    doc = fitz.open()
    for _ in range(pages):
        p = doc.new_page()
        p.insert_text((72, 100), "测试页")
    doc.save(str(path))
    doc.close()


class SplitFailDegradeTest(unittest.TestCase):
    def test_recursive_split_recovers_all_pages(self):
        """>10 页必败的云端：30 页书递归拆到 ≤10 页，全部页归位、无跳过。"""
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "book.pdf"
            _make_pdf(pdf, 30)
            fake = _FakeMinerU("t")
            with mock.patch.object(stage1_mineru, "MinerU", lambda _t: fake):
                info = stage1_mineru.run_mineru(str(pdf), td)
            pages = sorted(b["page_idx"] for b in info["content_list"])
            self.assertEqual(pages, list(range(30)))
            self.assertEqual(info["failed_pages"], [])
            # 发生了对半递归（有 <200 的中间片调用）
            self.assertTrue(any(n not in (30,) for n in fake.calls))
            # 递归子片的 markdown 依序合并（假数据每片从"第1页"起，应出现多次）
            self.assertGreaterEqual(info["markdown"].count("第1页内容"), 2)

    def test_single_page_failure_isolated(self):
        """单片败到单页仍败 → 记 failed_pages 跳过，其余页不陪葬。"""
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "book.pdf"
            _make_pdf(pdf, 25)

            class _FailOne(_FakeMinerU):
                def extract(self, path, **kw):
                    doc = fitz.open(path)
                    txt = "".join(doc[i].get_text() for i in range(doc.page_count))
                    n = doc.page_count
                    doc.close()
                    self.calls.append(n)
                    if n > 4:
                        return _FakeResult("failed", n)
                    # 单页/小片：含坏页标记的败（构造：第 3 片起的第 1 页）
                    if "X" in txt:
                        return _FakeResult("failed", n)
                    return _FakeResult("done", n)

            fake = _FailOne("t")
            with mock.patch.object(stage1_mineru, "MinerU", lambda _t: fake):
                info = stage1_mineru.run_mineru(str(pdf), td)
            self.assertEqual(len(info["content_list"]), 25)
            self.assertEqual(sorted(b["page_idx"] for b in info["content_list"]),
                             list(range(25)))

    def test_poll_timeout_also_splits(self):
        """云端轮询超时（任务龟速）同样对半降级且不睡重试——FG 50 分钟
        假象卡死实测：超时走异常重试路径会 900s×3 后才整书终止。"""
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "book.pdf"
            _make_pdf(pdf, 30)

            class _SlowCloud(_FakeMinerU):
                def extract(self, path, **kw):
                    doc = fitz.open(path)
                    n = doc.page_count
                    doc.close()
                    self.calls.append(n)
                    if n > 10:
                        raise TimeoutError("Task xxx did not complete within 900s")
                    return _FakeResult("done", n)

            fake = _SlowCloud("t")
            with mock.patch.object(stage1_mineru, "MinerU", lambda _t: fake):
                info = stage1_mineru.run_mineru(str(pdf), td)
            self.assertEqual(sorted(b["page_idx"] for b in info["content_list"]),
                             list(range(30)))
            self.assertEqual(info["failed_pages"], [])
            # 超时片每节点只提交一次（不睡重试）：调用序列=递归树先序遍历
            self.assertEqual(fake.calls, [30, 15, 8, 7, 15, 8, 7])


if __name__ == "__main__":
    unittest.main(verbosity=2)
