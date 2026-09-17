# -*- coding: utf-8 -*-
"""VLM 管道表抢救回归（T3①，SageRead 清单 #3，FG 实测 10 处形态）。

运行：.venv/Scripts/python tests/test_stage1_vlm_table.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stage1_vlm import (
    VlmProvider, _split_embedded_table, _table_md_to_html,
)


def cl_of(blocks: list[dict]) -> list[dict]:
    """单页 pages_json → content_list（走正式 _to_content_list 路径）。"""
    return VlmProvider._to_content_list(None, {0: {"blocks": blocks, "footnotes": []}},
                                        [], set())


class TableMdToHtmlTest(unittest.TestCase):
    def test_basic_table(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        h = _table_md_to_html(md)
        self.assertIn("<th>A</th><th>B</th>", h)
        self.assertIn("<td>1</td><td>2</td>", h)

    def test_double_header_row(self):
        """FG p28 Anger Scale：分组行+子标题行双表头，分隔行在第 3 行。"""
        md = ("| Anger Scale | | Relationship Satisfaction Scale | |\n"
              "| Score | Meaning | Score | Meaning |\n"
              "|---|---|---|---|\n"
              "| 0 | No anger | 0–10 | Extremely dissatisfied |")
        h = _table_md_to_html(md)
        self.assertIsNotNone(h)
        self.assertIn("<th>Anger Scale</th>", h)
        self.assertIn("<th>Score</th>", h)
        self.assertEqual(h.split("</thead>")[0].count("<tr>"), 2)

    def test_single_column_checklist(self):
        """FG p129 (✓) 单列表：ncol==1 放行。"""
        md = "| (✓) |\n|---|\n| Yes, I think so. |\n| No, I don't. |"
        h = _table_md_to_html(md)
        self.assertIsNotNone(h)
        self.assertIn("<th>(✓)</th>", h)
        self.assertIn("<td>Yes, I think so.</td>", h)

    def test_no_separator_returns_none(self):
        self.assertIsNone(_table_md_to_html("| a | b |\n| c | d |"))
        self.assertIsNone(_table_md_to_html("普通文本"))
        self.assertIsNone(_table_md_to_html(""))

    def test_squashed_single_line(self):
        """FG p309：整表挤成一行 → 还原解析（Empathy 五技术表）。"""
        md = ("| E = Empathy | | |---|---| | 1. The Disarming Technique. | "
              "Find some truth in what the other person is saying. |")
        h = _table_md_to_html(md)
        self.assertIsNotNone(h)
        self.assertIn("<th>E = Empathy</th>", h)
        self.assertIn("<td>1. The Disarming Technique.</td>", h)
        self.assertIn("<td>Find some truth", h)

    def test_squashed_truncated_tail_kept_as_text(self):
        """跨页截断（FG ch25）：整行部分成表，残片退表后文本，内容不丢。"""
        md = ("| Test | Chapter 1 | Now | Change | |---|---|---|---| "
              "| Depression | | | | | Anxiety | | | | |29 | Feelin")
        h = _table_md_to_html(md)
        self.assertIsNotNone(h)
        self.assertIn("<td>Depression</td>", h)
        self.assertIn("<td>Anxiety</td>", h)
        # 残片 '|29 | Feelin' 不进表（不足一行），由调用方文本兜底

    def test_squashed_no_sep_untouched(self):
        self.assertIsNone(_table_md_to_html("| 只有管道 | 没有分隔 |"))


class SplitEmbeddedTableTest(unittest.TestCase):
    def test_leading_text_kept(self):
        """FG p272：标题文本 + 空行 + 真表 → 前导文本 + table。"""
        txt = ("**Cost-Benefit Analysis**\n\n\"I'm a hopeless case.\"\n\n"
               "| Advantages | Disadvantages |\n|---|---|\n|  | Dead in two years. |")
        parts = _split_embedded_table(txt)
        self.assertIsNotNone(parts)
        self.assertEqual([p["kind"] for p in parts], ["text", "table"])
        self.assertIn("Cost-Benefit Analysis", parts[0]["text"])
        self.assertIn("<th>Advantages</th>", parts[1]["html"])

    def test_pure_table_text(self):
        """FG p223：整个 text 块就是一张表。"""
        txt = ("| | Necessary Condition | Sufficient Condition |\n"
               "|---|---|---|\n"
               "| Emotional Distress | negative thought | believe it |")
        parts = _split_embedded_table(txt)
        self.assertIsNotNone(parts)
        self.assertEqual([p["kind"] for p in parts], ["table"])

    def test_trailing_text_kept(self):
        txt = "| A |\n|---|\n| 1 |\n\n表后正文段落。"
        parts = _split_embedded_table(txt)
        self.assertEqual([p["kind"] for p in parts], ["table", "text"])
        self.assertIn("表后正文", parts[1]["text"])

    def test_no_separator_none(self):
        self.assertIsNone(_split_embedded_table("| 只有管道 | 没有分隔行 |"))
        self.assertIsNone(_split_embedded_table("普通文本"))


class ContentListIntegrationTest(unittest.TestCase):
    def test_embedded_table_rescued(self):
        """t=text 内嵌表 → content_list 出 table 块 + 前导 text 块。"""
        blocks = [{"t": "text",
                   "text": '**Cost-Benefit Analysis**\n\n'
                           "| Advantages | Disadvantages |\n|---|---|\n|  | Dead. |"}]
        out = cl_of(blocks)
        kinds = [b["type"] for b in out]
        self.assertEqual(kinds, ["text", "table"])
        self.assertIn("<table>", out[1]["table_body"])

    def test_pseudo_table_pipes_stripped(self):
        """t=table 解析失败且全管道行 → 剥管道纯文本，不塞裸 markdown。"""
        blocks = [{"t": "table",
                   "text": "| 9. **Labeling.** You label yourself. |\n"
                           "| 10. **Blame.** You find fault. |"}]
        out = cl_of(blocks)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["type"], "text")
        self.assertNotIn("|", out[0]["text"])
        self.assertIn("Labeling.", out[0]["text"])

    def test_single_col_table_now_table(self):
        """t=table 单列表（FG p156 跨页碎片）→ 真 table 块。"""
        blocks = [{"t": "table",
                   "text": "| 9. **Labeling.** You label yourself. |\n|---|\n"
                           "| 10. **Blame.** You find fault. |"}]
        out = cl_of(blocks)
        tables = [b for b in out if b["type"] == "table"]
        self.assertEqual(len(tables), 1)
        self.assertIn("Labeling.", tables[0]["table_body"])

    def test_plain_text_untouched(self):
        """无分隔行的普通 text 块原样通过（不动作）。"""
        blocks = [{"t": "text", "text": "这是普通正文，没有表格。"}]
        out = cl_of(blocks)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "这是普通正文，没有表格。")

    def test_natural_pipe_text_untouched(self):
        """版权页天然管道（LCCN | ISBN）无分隔行 → 不动。"""
        blocks = [{"t": "text", "text": "Identifiers: LCCN 123 (print) | ISBN 456 (hardback)"}]
        out = cl_of(blocks)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["type"], "text")
        self.assertIn("| ISBN", out[0]["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
