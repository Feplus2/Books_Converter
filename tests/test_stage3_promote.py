# -*- coding: utf-8 -*-
"""promote_lone_display_math 升格判定回归（病例 015 旧行为 + 病例 016 扩展）。

运行：.venv/Scripts/python tests/test_stage3_promote.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stage3_epub import promote_lone_display_math

_MATH = '<math alttext="{alt}" display="inline" xmlns="http://www.w3.org/1998/Math/MathML"><mrow><mi>x</mi></mrow></math>'
_LONG = r"\int \mathrm{e}^{ax}\sin^n bx\mathrm{d}x = \frac{1}{a^2 + b^2n^2}\mathrm{e}^{ax}\sin^{n-1}bx"
_LEFT = r"f(x)=\left\{\begin{aligned}x^{2},&0\leqslant x\leqslant1,\\ 2-x,&1<x\leqslant2;\end{aligned}\right."
_SH = r"\operatorname{sh} x = \frac{\mathrm{e}^x - \mathrm{e}^{-x}}{2}"


def _p(pre, alt, post=""):
    return f"<p>{pre}{_MATH.format(alt=alt)}{post}</p>"


class PromoteLoneDisplayMathTest(unittest.TestCase):
    # ── 旧行为保持 ──
    def test_bare_formula_promoted(self):
        out = promote_lone_display_math(_p("", _LONG))
        self.assertIn('display="block"', out)

    def test_numbered_prefix_promoted(self):
        out = promote_lone_display_math(_p("(1) ", _LEFT))
        self.assertIn('display="block"', out)

    def test_short_formula_untouched(self):
        html = _p("", "a = 1")
        self.assertEqual(promote_lone_display_math(html), html)

    def test_narrative_post_blocked(self):
        html = _p("", _LONG, " 成立。")
        self.assertEqual(promote_lone_display_math(html), html)

    def test_inline_citation_blocked(self):
        html = _p("其中 ", _LONG, " 为常数")
        self.assertEqual(promote_lone_display_math(html), html)

    # ── 病例 016 扩展 ──
    def test_circled_number_promoted(self):
        out = promote_lone_display_math(_p("⑦ ", r"\int \sin x\mathrm{d}x = -\cos x + C + \int_0^1 x^2\mathrm{d}x"))
        self.assertIn('display="block"', out)

    def test_short_narrative_prefix_promoted(self):
        out = promote_lone_display_math(_p("双曲正弦 ", _SH, "；"))
        self.assertIn('display="block"', out)

    def test_jie_prefix_promoted(self):
        out = promote_lone_display_math(_p("解 ", r"D=\left|\begin{matrix}2&3\\ 1&-2\end{matrix}\right|=2\times(-2)-3\times1=-7", ","))
        self.assertIn('display="block"', out)

    def test_long_narrative_prefix_blocked(self):
        html = _p("我们知道并证明了 ", _LONG, "。")
        self.assertEqual(promote_lone_display_math(html), html)

    def test_idempotent_on_block(self):
        html = '<p><math alttext="x" display="block" xmlns="http://www.w3.org/1998/Math/MathML"><mrow/></math></p>'
        self.assertEqual(promote_lone_display_math(html), html)

    def test_double_display_attr_all_replaced(self):
        # 病例 016：_latex_to_mathml 曾产生双 display 属性（插入的 + latex2mathml
        # 自带），promote 只换第一个会被序列化后的 inline 抵消
        html = ('<p>(1) <math alttext="f(x)=\\left\\{\\begin{aligned}x^2,&0\\leqslant x\\leqslant1;\\end{aligned}\\right."'
                ' display="inline" xmlns="http://www.w3.org/1998/Math/MathML" display="inline">'
                '<mrow><mi>f</mi></mrow></math></p>')
        out = promote_lone_display_math(html)
        self.assertNotIn('display="inline"', out)
        self.assertIn('display="block"', out)

    def test_no_cross_paragraph_swallowing(self):
        # 病例 016：前段含 math 但 post 超长/判定不合格时，正则 .*? 回溯会跨
        # 段落吞并后段——后段永远失去单独匹配机会（整文与单段行为分歧根源）
        bad = '<p>试指出 <math alttext="f(x)" display="inline" xmlns="m"><mrow/></math> 的全部间断点，并对可去间断点补充或修改函数值的定义，使它成为连续点。</p>'
        good = _p("(1) ", _LEFT)
        out = promote_lone_display_math(bad + "\n" + good)
        self.assertIn('display="block"', out)
        # 前段短公式不得被升格
        self.assertIn('alttext="f(x)" display="inline"', out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
