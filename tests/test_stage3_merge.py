# -*- coding: utf-8 -*-
"""_merge_broken_paragraphs 信号制合并回归（病例 018）。

运行：.venv/Scripts/python tests/test_stage3_merge.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stage3_epub import (
    _merge_broken_paragraphs,
    _should_merge,
    _visible_text,
    _is_lone_math_para,
)

_MATH = ('<math alttext="{alt}" display="inline" '
         'xmlns="http://www.w3.org/1998/Math/MathML">'
         '<mrow><mi>x</mi></mrow></math>')
_LONG_ALT = (r"\int \mathrm{e}^{ax}\sin^n bx\mathrm{d}x = "
             r"\frac{1}{a^2 + b^2n^2}\mathrm{e}^{ax}\sin^{n-1}bx")


def merge(html):
    return _merge_broken_paragraphs(html)


class MergeBasicTest(unittest.TestCase):
    def test_plain_break_merged(self):
        html = ("<p>There are three hundred seats, and five</p>\n"
                "<p>hundred people are crammed into them</p>")
        out, n = merge(html)
        self.assertEqual(n, 1)
        self.assertIn("five hundred people", out)
        self.assertEqual(out.count("<p>"), 1)

    def test_inline_tag_paragraph_merged(self):
        """含内联标记的段落参与合并（旧 _BROKEN_P 的 [^<] 完全跳过）"""
        html = (f"<p>the value of {_MATH.format(alt='f(x)')} and the value "
                f"of its derivative</p>\n"
                "<p>are both strictly positive on the interval</p>")
        out, n = merge(html)
        self.assertEqual(n, 1)
        self.assertIn("derivative are", out)

    def test_terminal_p1_not_merged(self):
        html = ("<p>他说完了整句话，这是一个足够长的段落，超过二十五字。</p>\n"
                "<p>这是一个新的段落，长度足够超过三十个字的限制，不会被合并掉的。</p>")
        out, n = merge(html)
        self.assertEqual(n, 0)
        self.assertEqual(out.count("<p>"), 2)

    def test_lowercase_signal_overrides_terminal(self):
        """p1 句点收尾但 p2 小写起首 → 续行强信号，合并"""
        html = ("<p>他说完了整句话，这是一个足够长的段落，超过二十五字。</p>\n"
                "<p>and then he left the room without saying a word</p>")
        out, n = merge(html)
        self.assertEqual(n, 1)

    def test_short_p2_fragment_merged(self):
        html = ("<p>这是一个完整的长段落，前面有足够多的文字内容，结尾正常。</p>\n"
                "<p>续。</p>")
        out, n = merge(html)
        self.assertEqual(n, 1)


class JoinSpacingTest(unittest.TestCase):
    def test_hyphen_dehyphenation(self):
        """英文断词：去连字符直拼（ar- + chaeology → archaeology）"""
        html = ("<p>this, and not the much debated and widely cited ar-</p>\n"
                "<p>chaeology of discursive formations</p>")
        out, _ = merge(html)
        self.assertIn("archaeology", out)
        self.assertNotIn("ar- chaeology", out)

    def test_ascii_space_join(self):
        """英-英词边界补空格"""
        html = ("<p>There are three hundred seats, and five</p>\n"
                "<p>hundred people</p>")
        out, _ = merge(html)
        self.assertIn("five hundred", out)

    def test_cjk_direct_join(self):
        """中文片段直拼，不补空格"""
        html = ("<p>这一段话在页末被扫描仪硬生生地截断了没有说完的话很多</p>\n"
                "<p>下一页继续讲完。</p>")
        out, _ = merge(html)
        self.assertIn("很多下一页", out)


class FormulaExceptionTest(unittest.TestCase):
    def test_lone_math_p2_not_merged(self):
        """后接 display 公式块不合并：p1 多为公式引导语（高等数学实测 1953 处）"""
        html = ("<p>由牛顿-莱布尼茨公式可以得到如下所示的重要结果与推论公式</p>\n"
                f"<p>{_MATH.format(alt=_LONG_ALT)}</p>")
        out, n = merge(html)
        self.assertEqual(n, 0)
        self.assertEqual(out.count("<p>"), 2)

    def test_lone_math_with_number_prefix_not_merged(self):
        """带编号的孤公式段（"(1) <math>…</math>"）同样豁免"""
        html = ("<p>于是我们得到下面的计算公式与相应的结果表达式如下所示</p>\n"
                f"<p>(1) {_MATH.format(alt=_LONG_ALT)}</p>")
        out, n = merge(html)
        self.assertEqual(n, 0)

    def test_lone_math_p1_not_merged(self):
        """p1 是孤公式段时不吞并后续行文（"其中 x 为…"说明句，实测 637 处）"""
        html = (f"<p>{_MATH.format(alt=_LONG_ALT)}</p>\n"
                "<p>其中 x 为常数，下同。</p>")
        out, n = merge(html)
        self.assertEqual(n, 0)

    def test_raw_dollar_lone_math_not_merged(self):
        """裸 $$…$$（mathmlify 失败退化 / 直喂原文）同样认作孤公式段"""
        html = (r"<p>于是我们得到下面的重要极限结果与相应的计算公式如下</p>" + "\n"
                r"<p>$$ \lim_{x\to\infty} \frac{1}{x} = 0 $$</p>")
        out, n = merge(html)
        self.assertEqual(n, 0)
        self.assertTrue(_is_lone_math_para(r"$$ x^2 + y^2 = z^2 $$"))
        self.assertTrue(_is_lone_math_para('(2) <code class="latex">x^2</code>'))
        self.assertFalse(_is_lone_math_para(r"$$ x^2 $$ 成立"))

    def test_math_with_narrative_not_exempt(self):
        """含叙述字的公式段不是公式块，正常参与合并"""
        html = (f"<p>{_MATH.format(alt='x=1')} "
                f"成立时我们有如下结论与推导过程需要继续展开说明和讨论</p>\n"
                "<p>并且进一步可以得到更多结果。</p>")
        self.assertFalse(_is_lone_math_para(_MATH.format(alt='x=1') + " 成立时我们有结论"))
        out, n = merge(html)
        self.assertEqual(n, 1)


class GuardTest(unittest.TestCase):
    def test_footnote_end_guard(self):
        html = ("<p>正文中被截断的长句子需要足够多的文字内容来避免标题的判定①</p>\n"
                "<p>and the continuation follows here</p>")
        out, n = merge(html)
        self.assertEqual(n, 0)

    def test_numbered_short_p2_guard(self):
        html = ("<p>上面这些习题的完整内容都在这里列出来了请大家认真作答</p>\n"
                "<p>2. 求下列极限</p>")
        out, n = merge(html)
        self.assertEqual(n, 0)

    def test_heading_p1_guard(self):
        html = ("<p>第一章 函数与极限</p>\n<p>设有两个映射</p>")
        out, n = merge(html)
        self.assertEqual(n, 0)

    def test_heading_p2_guard(self):
        """p2 是短标题行时不合并（防章标题被吞进段落）"""
        html = ("<p>存在，那么此极限 k 是割线斜率的极限，也就是切线的斜率在内</p>\n"
                "<p>第二章 导数与微分</p>")
        out, n = merge(html)
        self.assertEqual(n, 0)

    def test_toc_line_p2_guard(self):
        """目录条目行（小写起首但含连续大写）不享受小写豁免"""
        html = ("<p>Introduction: Arnold I. Davidson</p>\n"
                "<p>one 7 JANUARY 1976</p>")
        out, n = merge(html)
        self.assertEqual(n, 0)

    def test_short_p1_gate(self):
        """p1 极短且无标点收尾 → 页码/星标碎片，不并"""
        for p1 in ("xxiii", "*", "定义", "图1-23"):
            html = f"<p>{p1}</p>\n<p>and a lowercase continuation that follows here</p>"
            out, n = merge(html)
            self.assertEqual(n, 0, p1)

    def test_short_p1_sentence_punct_not_gated(self):
        """p1 极短但以句末点收尾时不触发碎片闸（用户裁定口径：拦'无标点'）"""
        html = ("<p>他走了。</p>\n<p>and then she also left the room quietly</p>")
        out, n = merge(html)
        self.assertEqual(n, 1)

    def test_caption_class_hard_boundary(self):
        """带 class 的段（no_indent 图注 / footnote）不参与合并也不被跨过"""
        html = ("<p>正文段落在这里被截断没有句末标点因此需要继续下去才行</p>\n"
                '<p class="no_indent"><small>图1-23 函数图像</small></p>\n'
                "<p>and continues here with more text to follow</p>")
        out, n = merge(html)
        self.assertEqual(n, 0)
        self.assertIn('<p class="no_indent">', out)

    def test_chain_limit(self):
        """无标点短行排的防过度合并兜底：链长限 _MERGE_CHAIN_MAX"""
        frag = "and the row continues onward with enough words here"
        html = "<p>" + "</p>\n<p>".join(
            ["The first line starts the sequence without ending"] + [frag] * 8
        ) + "</p>"
        out, n = merge(html)
        # 链满 6 次后从第 7 段起开新累积段，再并 1 次：共 7 次、2 段
        self.assertEqual(n, 7)
        self.assertEqual(out.count("<p>"), 2)

    def test_non_adjacent_not_merged(self):
        """中间隔标题 → 硬边界"""
        html = ("<p>这一段在节标题前被截断了没有说完的话</p>\n"
                "<h3>小节标题</h3>\n"
                "<p>and continues after the heading here</p>")
        out, n = merge(html)
        self.assertEqual(n, 0)

    def test_heading_tag_not_swallowed(self):
        """标题标签原样保留、不被并进相邻段落；疑似标题段不作累积头
        （病例019 排查锁回归：合并永不跨 h 标签边界）"""
        html = ("<p>上一段在标题前的话没有说完</p>\n"
                "<h2>第三节 函数的极限</h2>\n"
                "<p>映射是现代数学中的一个基本概念而函数是微积分的研究对象</p>\n"
                "<p>第一节 映射与函数</p>\n"
                "<p>本节主要介绍映射函数及有关概念函数的性质与运算等</p>")
        out, n = merge(html)
        self.assertEqual(n, 0)
        self.assertIn("<h2>第三节 函数的极限</h2>", out)
        self.assertIn("<p>第一节 映射与函数</p>", out)


class TocLookupTest(unittest.TestCase):
    def test_fullwidth_slash_page_stripped(self):
        """目录条目展示文本剥'标题／页码'尾巴（病例019：扫描本自制书签形态）"""
        from stage3_epub import _build_toc_lookup, _enrich_title
        lookup = _build_toc_lookup(
            [{"text": "第一章 函数与极限／1", "level": 1, "page": 16}])
        self.assertEqual(_enrich_title("第一章", lookup), "第一章 函数与极限")


class VisibleTextTest(unittest.TestCase):
    def test_entity_unescaped(self):
        self.assertEqual(_visible_text("a &gt; b"), "a > b")

    def test_tags_stripped(self):
        # MathML 的 token 文本（<mi>x</mi>）属可见文本，参与信号判定
        self.assertEqual(
            _visible_text(f"值 {_MATH.format(alt='x')} <sup>①</sup>"),
            "值 x ①")


if __name__ == "__main__":
    unittest.main()
