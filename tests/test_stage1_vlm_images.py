# -*- coding: utf-8 -*-
"""VLM 图片提取守卫回归：img_path 回写 db 后续跑不重提取（病例 044）。

运行：.venv/Scripts/python tests/test_stage1_vlm_images.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stage1_vlm import _needs_image_extract


class NeedsImageExtractTest(unittest.TestCase):
    def test_no_image_blocks(self):
        self.assertFalse(_needs_image_extract({"blocks": []}, "/x"))
        self.assertFalse(_needs_image_extract(
            {"blocks": [{"t": "text", "text": "正文"}]}, "/x"))

    def test_block_missing_path(self):
        pj = {"blocks": [{"t": "image", "caption": "图1"}]}
        self.assertTrue(_needs_image_extract(pj, "/x"))

    def test_path_present_but_file_gone(self):
        with tempfile.TemporaryDirectory() as td:
            pj = {"blocks": [{"t": "image", "img_path": "images/p0001_0.png"}]}
            self.assertTrue(_needs_image_extract(pj, td))

    def test_all_settled(self):
        """img_path 已回写 db 且 PNG 在盘 → 跳过（不重烧 bbox API）。"""
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, "p0001_0.png"), "wb").write(b"x")
            pj = {"blocks": [
                {"t": "image", "img_path": "images/p0001_0.png"},
                {"t": "text", "text": "正文"},
            ]}
            self.assertFalse(_needs_image_extract(pj, td))

    def test_mixed_one_unsettled(self):
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, "p0001_0.png"), "wb").write(b"x")
            pj = {"blocks": [
                {"t": "image", "img_path": "images/p0001_0.png"},
                {"t": "image", "caption": "无 path"},
            ]}
            self.assertTrue(_needs_image_extract(pj, td))


if __name__ == "__main__":
    unittest.main(verbosity=2)
