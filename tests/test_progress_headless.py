"""HeadlessProgress 配速爬行与 start 事件契约测试（病例 050）。

进度诚实化三件套：
- 爬行按阶段预估时长配速（爬满跨度 90% 恰好耗时 est 秒）；
  预估缺失/非法 → 回落旧固定爬速（铁律 0：不动作方向）；
- 本阶段已有真实 fraction 时爬行让位，显示值只向真实值缓动逼近（单调、不跳变）；
- start 事件携带真实 engine 与按预估加权的阶段边界（stage_bounds）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import progress_headless  # noqa: E402
from progress_headless import HeadlessProgress  # noqa: E402


def _collector():
    events = []
    return events, events.append


class TestCreepPacing(unittest.TestCase):
    """配速爬行：爬速 = 阶段跨度×90%/预估秒数。"""

    def test_paced_creep_matches_estimate(self):
        # est [100, 20, 5] → 跨度 s1=(0,80) s2=(80,96) s3=(96,100)
        pw = HeadlessProgress("书", engine="vlm", stage_estimates=[100.0, 20.0, 5.0])
        # 每拍 = 80 × 0.9 × 0.5s / 100s = 0.36
        self.assertAlmostEqual(pw._creep_step(1), 0.36)
        pw.update_stage(1, "VLM")
        pw._tick_once()
        self.assertAlmostEqual(pw._target, 0.36)
        for _ in range(9):
            pw._tick_once()
        self.assertAlmostEqual(pw._target, 3.6)

    def test_missing_estimate_fallback_fixed_creep(self):
        # 无预估 → 固定爬速 0.4/拍（旧行为）
        pw = HeadlessProgress("书")
        self.assertEqual(pw._creep_step(1), progress_headless._CREEP_STEP)
        pw.update_stage(1, "MinerU")
        pw._tick_once()
        self.assertAlmostEqual(pw._target, 0.4)
        # 有预估但查不到该阶段 / 预估为 0 → 同样回落
        pw2 = HeadlessProgress("书", stage_estimates=[10.0, 0.0, 5.0])
        self.assertEqual(pw2._creep_step(2), progress_headless._CREEP_STEP)
        self.assertEqual(pw2._creep_step(99), progress_headless._CREEP_STEP)

    def test_creep_capped_at_90_percent_of_span(self):
        # 长时间无真实输入：爬行在跨度 90% 处封顶，不再推进
        pw = HeadlessProgress("书", stage_estimates=[100.0, 20.0, 5.0])
        pw.update_stage(1, "MinerU")
        for _ in range(300):  # 远超爬满所需拍数（0.36/拍 → 200 拍到顶）
            pw._tick_once()
        self.assertAlmostEqual(pw._target, 80.0 * 0.9)

    def test_real_fraction_disables_creep_no_jump(self):
        events, emit = _collector()
        old = progress_headless._emit
        progress_headless._emit = emit
        try:
            pw = HeadlessProgress("书", engine="vlm", stage_estimates=[100.0, 20.0, 5.0])
            # 真实 fraction 0.5 → target = 0 + 0.5×80 = 40；之后爬行让位
            pw.update_stage(1, "VLM", fraction=0.5)
            self.assertAlmostEqual(pw._target, 40.0)
            for _ in range(10):
                pw._tick_once()
            self.assertAlmostEqual(pw._target, 40.0)  # 爬行未推进
            # 显示值缓动逼近 40，单调不减、永不越过（不跳变）
            percents = [e["percent"] for e in events if e["type"] == "progress"]
            self.assertTrue(percents)
            self.assertTrue(all(p <= 40.0 for p in percents))
            self.assertEqual(percents, sorted(percents))
            self.assertAlmostEqual(pw._percent, 40.0, delta=0.5)
        finally:
            progress_headless._emit = old

    def test_creep_resumes_on_next_stage_without_fraction(self):
        pw = HeadlessProgress("书", stage_estimates=[100.0, 20.0, 5.0])
        pw.update_stage(1, "VLM", fraction=0.5)
        pw.complete_stage(1, "VLM", 100.0)   # target 顶到 s1 上限 80
        self.assertAlmostEqual(pw._target, 80.0)
        # 新阶段尚无真实 fraction → 恢复按 s2 预估配速爬行
        # s2 跨度 16，est 20s → 每拍 16×0.9×0.5/20 = 0.36
        pw.update_stage(2, "Hybrid")
        pw._tick_once()
        self.assertAlmostEqual(pw._target, 80.36)


class TestStartEvent(unittest.TestCase):
    def test_start_event_carries_engine_and_bounds(self):
        events, emit = _collector()
        old = progress_headless._emit
        progress_headless._emit = emit
        try:
            pw = HeadlessProgress("书", engine="vlm",
                                  stage_estimates=[100.0, 20.0, 5.0],
                                  translate=False)
            pw.start()
            pw._finished = True  # 立刻停拍（测试不依赖后台节拍线程）
            start = events[0]
            self.assertEqual(start["type"], "start")
            self.assertEqual(start["engine"], "vlm")  # 真实引擎，非硬编码 hybrid
            self.assertEqual(start["stage_bounds"], [80.0, 96.0, 100.0])
        finally:
            progress_headless._emit = old

    def test_start_event_bounds_fallback_without_estimates(self):
        events, emit = _collector()
        old = progress_headless._emit
        progress_headless._emit = emit
        try:
            pw = HeadlessProgress("书", engine="mineru")
            pw.start()
            pw._finished = True
            # 无预估 → 默认三段跨度边界（铁律 0：回落旧形态，不缺字段）
            self.assertEqual(events[0]["stage_bounds"], [40.0, 95.0, 100.0])
        finally:
            progress_headless._emit = old


class TestDoneEvent(unittest.TestCase):
    """病例 055：done 事件携带 product_dir（产物文件夹），队列卡片
    「打开文件夹」按钮的数据源。"""

    def test_done_carries_product_dir(self):
        events, emit = _collector()
        old = progress_headless._emit
        progress_headless._emit = emit
        try:
            pw = HeadlessProgress("书", engine="mineru")
            pw.finish("D:/out/书/epub/书.epub", 12.3,
                      product_dir="D:/out/书")
            done = [e for e in events if e["type"] == "done"]
            self.assertEqual(len(done), 1)
            self.assertEqual(done[0]["product_dir"], "D:/out/书")
            self.assertEqual(done[0]["epub_path"], "D:/out/书/epub/书.epub")
            self.assertEqual(done[0]["percent"], 100.0)
        finally:
            progress_headless._emit = old

    def test_done_without_product_dir_omits_field(self):
        """旧调用形态（不传 product_dir）：字段缺省而非 null/空串
        （前端按无字段走 epub_path 上溯兜底）。"""
        events, emit = _collector()
        old = progress_headless._emit
        progress_headless._emit = emit
        try:
            pw = HeadlessProgress("书", engine="mineru")
            pw.finish("书.epub", 1.0)
            done = [e for e in events if e["type"] == "done"][0]
            self.assertNotIn("product_dir", done)
        finally:
            progress_headless._emit = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
