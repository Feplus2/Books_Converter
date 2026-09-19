# -*- coding: utf-8 -*-
"""失败提示音回归（病例 054）：play_failure_sound 静默失败方向 +
pipeline 失败路径接线（PDF 缺失 / Stage 1 异常）+ 错误提示分类
（环境类走运维清单，意料外走"疑似程序 bug"）。

运行：.venv/Scripts/python tests/test_completion_sound.py
"""
import io
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import completion_sound  # noqa: E402
import pipeline  # noqa: E402

import fitz  # noqa: E402


def _make_pdf(path: Path, pages: int = 3) -> None:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


class PlayFailureSoundTest(unittest.TestCase):
    def test_missing_file_silent(self):
        """音频文件缺失 → 静默跳过，绝不抛错（失败方向=不动作）。"""
        with mock.patch.object(completion_sound, "_DEFAULT_FAIL_WAV",
                               Path("D:/no/such/fail.wav")):
            completion_sound.play_failure_sound()  # 不炸即通过

    def test_env_off_silent(self):
        """CONVERT_FAIL_SOUND=off → 不调 winsound。"""
        with mock.patch.dict(os.environ, {"CONVERT_FAIL_SOUND": "off"}), \
                mock.patch("winsound.PlaySound") as ps:
            completion_sound.play_failure_sound()
        ps.assert_not_called()

    def test_default_wav_exists_and_plays(self):
        """默认失败音 assets/fail.wav 在场且被播放（PCM WAV）。"""
        self.assertTrue(completion_sound._DEFAULT_FAIL_WAV.exists())
        with mock.patch("winsound.PlaySound") as ps:
            completion_sound.play_failure_sound()
        ps.assert_called_once()
        self.assertIn("fail.wav", ps.call_args[0][0])

    def test_play_error_swallowed(self):
        """winsound 自身抛错也被吞掉。"""
        with mock.patch("winsound.PlaySound", side_effect=RuntimeError("x")):
            completion_sound.play_failure_sound()


class PipelineFailSoundTest(unittest.TestCase):
    def _run_main(self, argv):
        with mock.patch.object(sys, "argv", argv), \
                mock.patch("completion_sound.play_failure_sound") as pf, \
                mock.patch("completion_sound.play_completion_sound") as pc, \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as cm:
                pipeline.main()
        return cm.exception.code, pf, pc

    def test_missing_pdf_plays_failure(self):
        """PDF 不存在：exit 1 + 失败音响起 + 完成音不响。"""
        code, pf, pc = self._run_main(
            ["pipeline.py", "D:/no/such/book.pdf", "--headless"])
        self.assertEqual(code, 1)
        pf.assert_called_once()
        pc.assert_not_called()

    def test_stage1_env_error_plays_failure(self):
        """Stage 1 环境类失败：exit 1 + 失败音 + 运维清单提示。"""
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "b.pdf"
            _make_pdf(pdf)
            err = RuntimeError("重试 3 次仍失败: 网络抖动")
            logbuf = io.StringIO()
            handler = logging.StreamHandler(logbuf)
            logging.getLogger("pipeline").addHandler(handler)
            try:
                with mock.patch.object(sys, "argv",
                                       ["pipeline.py", str(pdf), "--headless",
                                        "--engine", "mineru"]), \
                        mock.patch("pipeline.get_provider",
                                   side_effect=err), \
                        mock.patch("completion_sound.play_failure_sound") as pf, \
                        mock.patch("sys.stdout", new_callable=io.StringIO):
                    with self.assertRaises(SystemExit) as cm:
                        pipeline.main()
            finally:
                logging.getLogger("pipeline").removeHandler(handler)
            self.assertEqual(cm.exception.code, 1)
            pf.assert_called_once()
            self.assertIn("网络连接", logbuf.getvalue())
            self.assertNotIn("疑似程序 bug", logbuf.getvalue())

    def test_stage1_unexpected_error_suggests_bug(self):
        """Stage 1 意料外异常（如 NameError）：不报网络清单，
        改报"疑似程序 bug"+ traceback 摘要（054 修复点）。"""
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "b.pdf"
            _make_pdf(pdf)
            err = NameError("name 'run_tag' is not defined")
            logbuf = io.StringIO()
            handler = logging.StreamHandler(logbuf)
            logging.getLogger("pipeline").addHandler(handler)
            try:
                with mock.patch.object(sys, "argv",
                                       ["pipeline.py", str(pdf), "--headless",
                                        "--engine", "mineru"]), \
                        mock.patch("pipeline.get_provider",
                                   side_effect=err), \
                        mock.patch("completion_sound.play_failure_sound") as pf, \
                        mock.patch("sys.stdout", new_callable=io.StringIO):
                    with self.assertRaises(SystemExit) as cm:
                        pipeline.main()
            finally:
                logging.getLogger("pipeline").removeHandler(handler)
            self.assertEqual(cm.exception.code, 1)
            pf.assert_called_once()
            out = logbuf.getvalue()
            self.assertIn("疑似程序 bug", out)
            self.assertIn("run_tag", out)  # traceback 摘要落日志
            self.assertNotIn("网络连接", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
