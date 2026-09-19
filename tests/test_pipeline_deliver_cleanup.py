# -*- coding: utf-8 -*-
"""交付后根级重复清理（病例 049）：_cleanup_root_exports 回归。

运行：.venv/Scripts/python tests/test_pipeline_deliver_cleanup.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import _cleanup_root_exports


def _make_book_dir(td: str) -> Path:
    """造一个含根级重复 + 缓存 + 交付副本的工作目录。"""
    wd = Path(td) / "某书"
    wd.mkdir(parents=True)
    # 根级原始导出（与交付副本字节相同）
    (wd / "某书.epub").write_bytes(b"epub-bytes")
    (wd / "某书.tex").write_text("\\documentclass{}", encoding="utf-8")
    bundle = wd / "某书_md"
    (bundle / "chapters").mkdir(parents=True)
    (bundle / "index.md").write_text("# 某书", encoding="utf-8")
    (wd / "images").mkdir()
    (wd / "images" / "p0001_0.jpg").write_bytes(b"img")
    (wd / "cover.jpg").write_bytes(b"cover")
    # 缓存（绝不可动）
    (wd / "vlm").mkdir()
    (wd / "vlm" / "vlm_state.db").write_bytes(b"db")
    (wd / "vlm" / "某书_content_list.json").write_text("[]", encoding="utf-8")
    (wd / "structure.json").write_text("{}", encoding="utf-8")
    (wd / "popo_blocks.json").write_text("[]", encoding="utf-8")
    (wd / "translations.json").write_text("{}", encoding="utf-8")
    # 交付副本（绝不可动）
    (wd / "epub").mkdir()
    (wd / "epub" / "某书.epub").write_bytes(b"epub-bytes")
    (wd / "md").mkdir()
    (wd / "tex").mkdir()
    return wd


class CleanupHappensTest(unittest.TestCase):
    """全部交付成功 → 根级重复被删，交付副本与缓存原样。"""

    def test_root_duplicates_removed(self):
        with tempfile.TemporaryDirectory() as td:
            wd = _make_book_dir(td)
            exports = [wd / "某书.epub", wd / "某书.tex", wd / "某书_md"]
            removed = _cleanup_root_exports(wd, exports, delivered_ok=True)
            self.assertEqual(sorted(removed),
                             ["cover.jpg", "images", "某书.epub",
                              "某书.tex", "某书_md"])
            for name in ("某书.epub", "某书.tex", "某书_md", "images", "cover.jpg"):
                self.assertFalse((wd / name).exists(), f"{name} 应被清理")
            # 交付副本原样
            self.assertEqual((wd / "epub" / "某书.epub").read_bytes(), b"epub-bytes")
            self.assertTrue((wd / "md").is_dir())
            self.assertTrue((wd / "tex").is_dir())

    def test_caches_untouched(self):
        """Stage 1/2/4 缓存与用户杂物绝不在清理范围。"""
        with tempfile.TemporaryDirectory() as td:
            wd = _make_book_dir(td)
            (wd / "用户笔记.txt").write_text("mine", encoding="utf-8")
            exports = [wd / "某书.epub", wd / "某书.tex", wd / "某书_md"]
            _cleanup_root_exports(wd, exports, delivered_ok=True)
            for name in ("vlm", "structure.json", "popo_blocks.json",
                         "translations.json", "用户笔记.txt",
                         "epub", "md", "tex"):
                self.assertTrue((wd / name).exists(), f"{name} 不应被动")
            self.assertTrue((wd / "vlm" / "vlm_state.db").exists())

    def test_missing_targets_ok(self):
        """只跑了 epub 的场景：md/tex/images 本就不存在，清理照常成功。"""
        with tempfile.TemporaryDirectory() as td:
            wd = _make_book_dir(td)
            removed = _cleanup_root_exports(wd, [wd / "某书.epub"],
                                            delivered_ok=True)
            self.assertIn("某书.epub", removed)
            self.assertIn("cover.jpg", removed)
            self.assertIn("images", removed)   # 旧跑残留的根级 images/ 也清
            # 本轮未导出的旧产物不在清理清单（保守：只删本轮产出的重复）
            self.assertTrue((wd / "某书.tex").exists())
            self.assertTrue((wd / "某书_md").exists())


class FailureDirectionTest(unittest.TestCase):
    """失败方向 = 不动作（铁律 0）。"""

    def test_delivery_failed_no_cleanup(self):
        """交付未全部成功（delivered_ok=False）→ 什么都不删。"""
        with tempfile.TemporaryDirectory() as td:
            wd = _make_book_dir(td)
            exports = [wd / "某书.epub", wd / "某书.tex", wd / "某书_md"]
            removed = _cleanup_root_exports(wd, exports, delivered_ok=False)
            self.assertEqual(removed, [])
            for name in ("某书.epub", "某书.tex", "某书_md", "images", "cover.jpg"):
                self.assertTrue((wd / name).exists(), f"{name} 应保留")

    def test_outside_workdir_never_deleted(self):
        """导出路径不在 work_dir 直系 → 跳过（防路径异常误删他处）。"""
        with tempfile.TemporaryDirectory() as td:
            wd = _make_book_dir(td)
            outsider = Path(td) / "别处.epub"
            outsider.write_bytes(b"keep")
            removed = _cleanup_root_exports(wd, [outsider, wd / "某书.epub"],
                                            delivered_ok=True)
            self.assertTrue(outsider.exists())
            self.assertIn("某书.epub", removed)

    def test_single_item_failure_does_not_abort(self):
        """单项删除失败只告警，其余照删（产物已交付，清理是附属动作）。"""
        with tempfile.TemporaryDirectory() as td:
            wd = _make_book_dir(td)
            # 用一个会抛 OSError 的幽灵路径形态：父项是文件，子项必失败
            ghost = wd / "某书.epub" / "child"
            exports = [ghost, wd / "某书.tex"]
            removed = _cleanup_root_exports(wd, exports, delivered_ok=True)
            self.assertIn("某书.tex", removed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
