"""raster_snap 连通域光栅重裁单测（合成图，无网络）。

运行：.venv/Scripts/python.exe tests/test_raster_snap.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw

from raster_snap import merge_overlaps, snap_box_cc


def _page() -> tuple[Image.Image, list[int]]:
    """600x900 白页：图形黑块 (150,200)-(450,500) + 图注细线 y=540-545 + 页眉细线。"""
    img = Image.new("L", (600, 900), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([150, 200, 450, 500], fill=0)              # 图形（大墨迹块）
    d.rectangle([150, 540, 450, 545], fill=0)              # 图注文字行（细带，与图不相连）
    d.rectangle([80, 60, 520, 64], fill=0)                 # 页眉文字行
    truth = [150 / 600 * 1000, 200 / 900 * 1000, 450 / 600 * 1000, 500 / 900 * 1000]
    return img.convert("RGB"), truth


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class TestSnapCC(unittest.TestCase):
    def test_snap_to_content_excludes_caption_and_header(self):
        img, truth = _page()
        rough = [265, 226, 735, 540]  # 内缩粗框
        snapped = snap_box_cc(img, rough)
        self.assertGreater(_iou(snapped, truth), 0.85)
        self.assertLess(snapped[3], 600)   # 不吞图注行（y 540 → 千分位 600）
        self.assertGreater(snapped[1], 66)  # 不窜页眉行（y 60 → 千分位 66）

    def test_oversized_rough_shrinks_to_figure(self):
        """粗框把图注/正文包进来（过大）→ 连通域收缩到图形本体。"""
        img, truth = _page()
        big = [233, 211, 767, 640]   # 含图注行的过大粗框
        snapped = snap_box_cc(img, big)
        self.assertGreater(_iou(snapped, truth), 0.85)
        self.assertLess(snapped[3], 600)

    def test_failopen_blank_area(self):
        """空白区粗框：无连通域 → 回退原框（不动作）。"""
        img, _ = _page()
        rough = [830, 760, 950, 900]
        self.assertEqual(snap_box_cc(img, rough), rough)

    def test_degenerate_box_unchanged(self):
        img, _ = _page()
        self.assertEqual(snap_box_cc(img, [500, 500, 400, 600]), [500, 500, 400, 600])

    def test_area_guard_rejects_collapse(self):
        """整页粗框：连通域正确收缩但面积比越界 → 守卫回退（不动作）。"""
        img, _ = _page()
        big = [50, 50, 950, 950]
        self.assertEqual(snap_box_cc(img, big), big)


class TestMerge(unittest.TestCase):
    def test_merge_overlapping(self):
        # 强重叠/近包含才合并（角落轻触不算同图）
        out = merge_overlaps([[100, 100, 400, 400], [200, 150, 380, 390]])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0], [100, 100, 400, 400])
        out2 = merge_overlaps([[100, 100, 400, 400], [300, 300, 600, 600]])
        self.assertEqual(len(out2), 2)   # 角落轻触（IoU≈0.06）不合并

    def test_keep_distant(self):
        out = merge_overlaps([[100, 100, 300, 300], [600, 600, 800, 800]])
        self.assertEqual(len(out), 2)

    def test_merge_containment(self):
        out = merge_overlaps([[100, 100, 500, 500], [200, 200, 300, 300]])
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
