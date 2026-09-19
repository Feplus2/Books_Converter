"""pipeline 进度接线诚实化测试（病例 050）。

- start/progress 事件的 engine 字段必须是真实引擎（mineru/paddleocr/vlm），
  不再是硬编码 "hybrid"——用假 HeadlessProgress 截停 main()，验证构造接线；
- 「OCR: 强制/自动」日志行只在 mineru 时出现（该旗标仅 MinerU 真消费），
  其他引擎明示不适用；
- MinerU 的 Stage 1 预估收窄到实测口径 ≈ 2.0 s/页（旧 0.80 偏快约 2 倍）。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline  # noqa: E402


class _FakeProgress:
    """记录构造参数；start() 在 Stage 1 之前截停 main()（只验证接线）。"""

    instances: list = []

    def __init__(self, book_name, engine=None, stage_estimates=None, translate=False):
        self.kwargs = {"engine": engine, "stage_estimates": stage_estimates,
                       "translate": translate}
        _FakeProgress.instances.append(self)

    def start(self):
        raise KeyboardInterrupt

    def close(self):
        pass


def _make_pdf(path: Path, pages: int = 5) -> None:
    import fitz
    with fitz.open() as doc:
        for _ in range(pages):
            doc.new_page()
        doc.save(str(path))


class TestEngineHonesty(unittest.TestCase):
    def setUp(self):
        _FakeProgress.instances.clear()
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.pdf = Path(self._td.name) / "书.pdf"
        _make_pdf(self.pdf)

    def _run_main(self, *extra: str):
        argv = ["pipeline.py", str(self.pdf), "--headless",
                "-o", self._td.name, *extra]
        with mock.patch.object(sys, "argv", argv), \
             mock.patch.object(pipeline, "HeadlessProgress", _FakeProgress):
            with self.assertRaises(KeyboardInterrupt):
                pipeline.main()
        return _FakeProgress.instances[0]

    def test_start_event_engine_not_hybrid(self):
        for engine in ("vlm", "mineru", "paddleocr"):
            with self.subTest(engine=engine):
                _FakeProgress.instances.clear()
                pw = self._run_main("--engine", engine)
                self.assertEqual(pw.kwargs["engine"], engine)
                self.assertNotEqual(pw.kwargs["engine"], "hybrid")

    def test_stage_estimates_passed_to_progress(self):
        pw = self._run_main("--engine", "vlm")
        est = pw.kwargs["stage_estimates"]
        self.assertEqual(len(est), 3)          # 无翻译 → 3 阶段
        self.assertEqual(est[0], max(5 * 3.0, 30))  # VLM ≈ 3 s/页


class TestOcrLogLine(unittest.TestCase):
    def test_mineru_shows_ocr_flag(self):
        self.assertEqual(pipeline._ocr_log_line("mineru", True), "OCR: 强制")
        self.assertEqual(pipeline._ocr_log_line("mineru", False), "OCR: 自动")

    def test_other_engines_marked_not_applicable(self):
        line = pipeline._ocr_log_line("vlm", True)
        self.assertIn("不适用", line)
        self.assertIn("VLM", line)
        line = pipeline._ocr_log_line("paddleocr", False)
        self.assertIn("不适用", line)
        self.assertIn("PaddleOCR", line)


class TestEstimateSeconds(unittest.TestCase):
    def test_mineru_slowed_to_measured_pace(self):
        est = pipeline._estimate_stage_seconds(100, "mineru", False, False)
        self.assertEqual(est[0], 200.0)   # ≈ 2.0 s/页（旧口径 0.80 偏快约 2 倍）

    def test_other_engines_unchanged(self):
        self.assertEqual(pipeline._estimate_stage_seconds(100, "vlm", False, False)[0], 300.0)
        self.assertEqual(pipeline._estimate_stage_seconds(100, "paddleocr", False, False)[0], 50.0)

    def test_skip_and_translate_shapes(self):
        self.assertEqual(pipeline._estimate_stage_seconds(100, "mineru", False, True)[0], 1.0)
        self.assertEqual(len(pipeline._estimate_stage_seconds(100, "mineru", True, False)), 4)
        self.assertEqual(len(pipeline._estimate_stage_seconds(100, "mineru", False, False)), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
