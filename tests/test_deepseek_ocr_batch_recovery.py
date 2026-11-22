"""Exercise recovery through the Stage 1 entry point with saved and live responses.

A rejected PDF may keep its shared resources after a page split. A successful
batch may contain one truncated page. Retrying the book must reuse its good
ranges and the repaired page, keeping all page indices in source order.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
import requests

from stage1_deepseek_ocr import DeepSeekOcrProvider


def response(items, status=200):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps({"page_count": len(items), "pages": items}).encode()
    return r


def item(index, text, reason="stop"):
    return {"index": index, "finish_reason": reason,
            "text": f"<｜ref｜>text<｜/ref｜><｜box｜>[[5,5,900,80]]<｜/box｜> {text}"}


class BatchRecovery(unittest.TestCase):
    def test_resume_after_rejections_and_repair_only_the_truncated_page(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "sample.pdf"
            with pymupdf.open() as doc:
                for i in range(5):
                    doc.new_page(width=200, height=200).insert_text((20, 30), str(i))
                doc.save(pdf)
            cache = root / "deepseek-ocr/batches" / hashlib.sha256(pdf.read_bytes()).hexdigest()
            cache.mkdir(parents=True)
            (cache / "0-1.json").write_bytes(response([item(0, "cached")]).content)
            replies = [
                response([], 413), response([], 413),
                response([item(0, "one"), item(1, "incomplete", "length")]),
                response([item(0, "recovered")]),
                response([item(0, "three"), item(1, "four")]),
            ]
            with patch.dict("os.environ", {"DEEPSEEK_OCR_API_KEY": "test"}), \
                 patch("requests.post", side_effect=replies) as post:
                result = DeepSeekOcrProvider().parse(pdf, root)
                self.assertEqual([c.kwargs["files"]["file"][2] for c in post.call_args_list],
                                 ["application/pdf"] * 3 + ["image/jpeg", "application/pdf"])
            expected = list(enumerate(["cached", "one", "recovered", "three", "four"]))
            self.assertEqual([(b["page_idx"], b["text"]) for b in result["content_list"]], expected)
            with patch("requests.post", side_effect=AssertionError("unexpected upload")):
                replay = DeepSeekOcrProvider().parse(pdf, root)
            self.assertEqual([(b["page_idx"], b["text"]) for b in replay["content_list"]], expected)


if __name__ == "__main__":
    unittest.main()
