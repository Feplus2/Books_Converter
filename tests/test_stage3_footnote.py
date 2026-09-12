"""stage3 脚注锚定回归（VLM 引擎病例）：正文裸圈码必须能锚定。

根因：claim_footnotes 的裸匹配守卫用 `not mark[0].isalnum()` 判定圈码，
但 ① 的 Unicode 类别是数字，isalnum() 判 True → 裸圈码永远进不了裸匹配
分支。MinerU 内容因有 $^{①}$ 上标约定从未暴露；VLM 引擎的裸圈码全灭。

运行：.venv/Scripts/python.exe tests/test_stage3_footnote.py
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stage3_epub as s3


def _units(popo):
    cl = [{"type": "text", "text": "x"}] * len(popo)
    return s3._render_popo_body(popo, cl, 0, 100, True, "测试",
                                toc_entries=[], translations=None)


class TestCircledMarkerClaim(unittest.TestCase):
    def test_bare_circled_marker_claims(self):
        """裸圈码（VLM/直排引擎形态）→ noteref 链接 + 脚注带 id 回链。"""
        popo = [
            {"id": 0, "type": "title", "content": "第一章 测试", "level": 2, "page": 24, "source_id": "t:0"},
            {"id": 1, "type": "text", "content": "市民法规则组成。① 公法与私法的划分被普遍接受。",
             "page": 24, "source_id": "t:1"},
            {"id": 2, "type": "page_footnote", "content": "① 参见《学说汇纂》（第一卷），第7页。",
             "page": 24, "source_id": "t:2"},
        ]
        html = "".join(p for u in _units(popo) for p in u["parts"])
        self.assertIn('epub:type="noteref"', html)
        self.assertIn('id="fn_', html)
        self.assertIn('href="#fnref_', html)

    def test_ascii_marker_never_bare_matched(self):
        """守卫不松：ASCII 数字标记仍只认 <sup>，正文的裸 "1" 不被误锚。"""
        popo = [
            {"id": 0, "type": "title", "content": "第一章 测试", "level": 2, "page": 24, "source_id": "t:0"},
            {"id": 1, "type": "text", "content": "本节共 1 个要点。首先是定义问题。",
             "page": 24, "source_id": "t:1"},
            {"id": 2, "type": "page_footnote", "content": "1. 参见某书。", "page": 24, "source_id": "t:2"},
        ]
        html = "".join(p for u in _units(popo) for p in u["parts"])
        self.assertNotIn('epub:type="noteref"', html)   # 正文无上标 → 不锚定
        self.assertIn("参见某书", html)                  # 脚注兜底渲染，内容不丢

    def test_sup_marker_still_claims(self):
        """既有 $^{①}$ 约定不破：MinerU 形态照常锚定。"""
        popo = [
            {"id": 0, "type": "title", "content": "第一章 测试", "level": 2, "page": 24, "source_id": "t:0"},
            {"id": 1, "type": "text", "content": "市民法规则组成。$^{①}$ 公法与私法。",
             "page": 24, "source_id": "t:1"},
            {"id": 2, "type": "page_footnote", "content": "① 参见《学说汇纂》。", "page": 24, "source_id": "t:2"},
        ]
        html = "".join(p for u in _units(popo) for p in u["parts"])
        self.assertIn('epub:type="noteref"', html)

    def test_same_page_title_does_not_flush_prematurely(self):
        """病例 033：页中新开章/节，同页脚注不得被标题提前冲走——
        锚点在本页后段才出现时必须仍能锚定。"""
        popo = [
            {"id": 0, "type": "title", "content": "第一章 测试", "level": 2, "page": 24, "source_id": "t:0"},
            {"id": 1, "type": "title", "content": "第一节 新节", "level": 3, "page": 24, "source_id": "t:1"},
            {"id": 2, "type": "text", "content": "正文里有标记① 在本页后段。",
             "page": 24, "source_id": "t:2"},
            {"id": 3, "type": "page_footnote", "content": "① 参见某书。", "page": 24, "source_id": "t:3"},
        ]
        html = "".join(p for u in _units(popo) for p in u["parts"])
        self.assertIn('epub:type="noteref"', html)
        self.assertIn("参见某书", html)


class TestLatexAmpersandStrip(unittest.TestCase):
    def test_alignment_ampersand_stripped(self):
        """对齐 & 标记不得渲染成可见字符（QFT eq.5.46 亲读病例）；
        '\\&'=换行+对位同样要剥；\\&（字面 ampersand）保留。"""
        mathml = s3._latex_to_mathml(
            r"\begin{aligned}&\prod_{i=1}^{m}\int d^4x_i\\&=a+b\end{aligned}", True)
        self.assertIsNotNone(mathml)
        alt = re.search(r'alttext="([^"]*)"', mathml)
        self.assertIsNotNone(alt)
        # alttext 里不许有未转义的 &（&amp; 是 HTML 实体，不算）
        self.assertNotRegex(alt.group(1).replace("&amp;", ""), r"&")
        # 字面 \& 不丢（R&D 场景）
        mathml2 = s3._latex_to_mathml(r"\text{A\&B}", False)
        if mathml2 is not None:
            self.assertIn("&amp;", mathml2)

    def test_sanitize_latex_typos(self):
        """模型 LaTeX 笔误窄守卫：\\qqud→\\qquad；\\slashedp→\\slashed{p}；
        \\hbar 等固有命令不误伤。"""
        self.assertEqual(s3._sanitize_latex(r"x \qqud (3.47)"), r"x \qquad (3.47)")
        self.assertEqual(s3._sanitize_latex(r"\slashedp"), r"\slashed{p}")
        self.assertEqual(s3._sanitize_latex(r"\slashed{D}"), r"\slashed{D}")
        self.assertEqual(s3._sanitize_latex(r"\hbar"), r"\hbar")
        self.assertEqual(s3._sanitize_latex(r"\bar \psi"), r"\bar \psi")


if __name__ == "__main__":
    unittest.main(verbosity=2)
