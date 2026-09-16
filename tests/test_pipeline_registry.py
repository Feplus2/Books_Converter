"""产物登记处（pipeline._register_product）契约测试。

登记处是 GUI 产物库的数据源（wiki/08）：每完成一本向 <输出目录>/_registry.jsonl
追加一行 JSON。铁律 0：登记失败方向必须是"不动作"——只告警，绝不抛出、
绝不影响转换主流程。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import _register_product  # noqa: E402


class TestRegisterProduct(unittest.TestCase):
    def test_append_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            rec = {"v": 1, "title": "民法总论", "engine": "vlm",
                   "products": {"epub": ["X:/a/民法总论.epub"]}}
            _register_product(out, rec)
            _register_product(out, {**rec, "title": "高等数学"})
            lines = (out / "_registry.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)  # 追加而非覆盖
            r0 = json.loads(lines[0])
            self.assertEqual(r0["title"], "民法总论")          # 中文不转义直写
            self.assertEqual(r0["products"]["epub"], ["X:/a/民法总论.epub"])
            self.assertEqual(json.loads(lines[1])["title"], "高等数学")

    def test_creates_missing_dir(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "a" / "b"
            _register_product(out, {"v": 1})
            self.assertTrue((out / "_registry.jsonl").exists())

    def test_failure_direction_no_action(self):
        # 非法路径（含 NUL）→ 只告警不抛出（登记绝不影响转换）
        _register_product(Path("Z:/bad\x00path"), {"v": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
