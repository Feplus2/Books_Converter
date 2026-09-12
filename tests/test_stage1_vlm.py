"""VLM 引擎（stage1_vlm）单测：契约映射 / 守卫 / 状态库 / 失败方向。
全部 mock，无网络。对应 wiki/06-vlm-pipeline.md §8 实验结论的固化用例。

运行：.venv/Scripts/python.exe tests/test_stage1_vlm.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stage1_vlm
from stage1_vlm import VlmProvider, _StateDB, _synth_bbox, _toc_scheme_hint
from vlm_client import extract_json, reasoning_extra


def _make_pdf(path: Path, n_pages: int = 3) -> None:
    import fitz
    doc = fitz.open()
    for i in range(n_pages):
        pg = doc.new_page()
        pg.insert_text((72, 72), f"test page {i}")
    doc.save(str(path))
    doc.close()


class _FakeClient:
    """按调用顺序返回脚本化页 JSON 的假 VlmClient。"""
    script: list[dict | Exception] = []
    fail_all = False

    def __init__(self, **kw):
        self.client = SimpleNamespace(api_key="fake")
        self.model = kw.get("model", "fake-model")
        self.reasoning = kw.get("reasoning")
        self._idx = 0

    def chat(self, prompt, images=(), max_tokens=16384, temperature=0.0, want_json=True):
        if _FakeClient.fail_all:
            return {"ok": False, "error": "mock: always fails", "content": "",
                    "json": None, "usage": None, "latency_s": 0, "finish_reason": None}
        i = min(self._idx, len(_FakeClient.script) - 1)
        self._idx += 1
        item = _FakeClient.script[i]
        return {"ok": True, "content": json.dumps(item), "json": item,
                "usage": None, "latency_s": 0.01, "finish_reason": "stop"}


def _page(kind="body", blocks=None, fns=None, **kw):
    return {"kind": kind, "printed_page": kw.get("pp"),
            "running_head": kw.get("rh"), "blocks": blocks or [],
            "footnotes": fns or [], "has_image": kw.get("hi", False)}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pdf = self.tmp / "tiny.pdf"
        _make_pdf(self.pdf)
        self._orig = stage1_vlm.VlmClient
        stage1_vlm.VlmClient = _FakeClient
        _FakeClient.fail_all = False

    def tearDown(self):
        stage1_vlm.VlmClient = self._orig

    def run_provider(self, script, **opts):
        _FakeClient.script = script
        p = VlmProvider()
        return p.parse(str(self.pdf), str(self.tmp), workers=1,
                       api_key="fake", no_images=True, **opts)

    def content_list(self):
        return json.loads(next(self.tmp.glob("vlm/*_content_list.json")).read_text(encoding="utf-8"))


class TestContractMapping(_Base):
    def test_block_types_and_footnote_marker(self):
        self.run_provider([
            _page(blocks=[{"t": "title", "text": "第一章 民法概念论", "level": 2},
                          {"t": "text", "text": "民法是……"},
                          {"t": "image", "caption": "图1-1 示例"}],
                  fns=[{"marker": "①", "text": "参见某书。"}], pp=1, rh="民法总论"),
            _page(kind="blank"),
            _page(blocks=[{"t": "text", "text": "第三页正文"}]),
        ])
        cl = self.content_list()
        types = [b["type"] for b in cl]
        self.assertIn("header", types)          # running_head
        self.assertIn("page_number", types)     # printed_page
        title = next(b for b in cl if b.get("text") == "第一章 民法概念论")
        self.assertEqual(title["type"], "text")
        self.assertEqual(title["text_level"], 2)
        fn = next(b for b in cl if b["type"] == "page_footnote")
        self.assertTrue(fn["text"].startswith("① "), fn["text"])
        # 无 bbox 客户端时 image 降级为文字占位——内容永不丢
        img = next(b for b in cl if "插图" in b.get("text", ""))
        self.assertEqual(img["type"], "text")
        self.assertIn("图1-1 示例", img["text"])
        # blank 页无块；所有块都带 0-1000 bbox 与 page_idx
        self.assertFalse(any(b["page_idx"] == 1 for b in cl))
        for b in cl:
            self.assertEqual(len(b["bbox"]), 4)
            self.assertTrue(all(0 <= v <= 1000 for v in b["bbox"]))

    def test_title_level_clamp_and_bad_block_guard(self):
        self.run_provider([_page(blocks=[
            {"t": "title", "text": "过深标题", "level": 99},
            {"t": "title", "text": "无层级标题"},
            {"t": "weird", "text": "未知类型块"},
            {"t": "text", "text": "   "},          # 空块丢弃
            {"t": "text", "text": "正常"},
        ])] + [_page() for _ in range(2)])
        cl = self.content_list()
        deep = next(b for b in cl if b.get("text") == "过深标题")
        self.assertEqual(deep["text_level"], 6)
        nolv = next(b for b in cl if b.get("text") == "无层级标题")
        self.assertEqual(nolv["text_level"], 1)
        weird = next(b for b in cl if b.get("text") == "未知类型块")
        self.assertNotIn("text_level", weird)
        self.assertFalse(any(b.get("text", "").strip() == "" for b in cl))
    def test_marker_wrap_convention(self):
        """正文圈码→$^{①}$ 上标约定（stage3 noteref 依赖）；无对应注文的圈码不包。"""
        self.run_provider([
            _page(blocks=[{"t": "text", "text": "组成。① 有对应脚注。"}],
                  fns=[{"marker": "①", "text": "参见某书。"}]),
            _page(blocks=[{"t": "text", "text": "列举 ① 甲 ② 乙（内联列举，无脚注）"}]),
            _page(),
        ])
        cl = self.content_list()
        b0 = next(b for b in cl if b["page_idx"] == 0 and b["type"] == "text")
        self.assertIn("$^{①}$", b0["text"])
        b1 = next(b for b in cl if b["page_idx"] == 1 and b["type"] == "text")
        self.assertIn("① 甲", b1["text"])            # 不动作：内联列举保持裸字
        self.assertNotIn("$^{", b1["text"])


class TestTocPrior(_Base):
    def test_toc_dedup_and_contract_shape(self):
        e1 = {"t": "toc", "text": "第一章 民法概念论", "level": 2, "page": 3}
        pad = [{"t": "toc", "text": f"第{x}章 凑数", "level": 2, "page": x} for x in "二三四"]
        self.run_provider([
            _page(kind="toc", blocks=[{"t": "toc", "text": "简目", "level": 1, "page": None}, e1] + pad),
            _page(kind="toc", blocks=[{"t": "toc", "text": "第一章 民法概念论", "level": 2, "page": 3}] + pad),
            _page(),
        ])
        cl = self.content_list()
        toc_blocks = [b for b in cl if "民法概念论 … 3" in b.get("text", "")]
        self.assertEqual(len(toc_blocks), 2)  # 两页各一条（content_list 保留页级现场）
        self.assertTrue(all(b["text_level"] == 1 for b in toc_blocks))
        # 先验库去重
        db = _StateDB(self.tmp / "vlm" / "vlm_state.db")
        entries = json.loads(db.get("toc_entries"))
        db.close()
        self.assertEqual(len(entries), 5)     # 简目 + 第一章 + 3 凑数（详目重复被去重）

    def test_scheme_hint_majority_vote(self):
        entries = [{"text": "第一章 甲", "level": 2}, {"text": "第二章 乙", "level": 2},
                   {"text": "第三章 丙", "level": 2}, {"text": "第四章 丁", "level": 5}]
        hint = _toc_scheme_hint(entries)
        self.assertIn("第X章=L2", hint)       # 3:1 众数，错票不污染


class TestFailureDirection(_Base):
    def test_all_fail_no_fabrication(self):
        _FakeClient.fail_all = True
        out = self.run_provider([_page()] * 3)
        self.assertEqual(out["content_list"], [])   # 不编造任何内容
        meta = json.loads((self.tmp / "vlm" / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(meta["failed_pages"]), [0, 1, 2])

    def test_resume_skips_done_pages(self):
        self.run_provider([_page(blocks=[{"t": "text", "text": "第一版"}])] + [_page()] * 2)
        # 二跑换脚本：已完成页必须保持第一版（断点续跑）
        cl = self.run_provider([_page(blocks=[{"t": "text", "text": "第二版"}])] * 3)["content_list"]
        texts = [b.get("text") for b in cl if b["type"] == "text"]
        self.assertIn("第一版", texts)
        self.assertNotIn("第二版", texts)
    def test_minitoc_downgrade_e2e(self):
        """章首 mini-TOC 不算目录页：降级普通正文、剥页码后缀（病例 034）。"""
        self.run_provider([
            _page(kind="toc", blocks=[{"t": "toc", "text": f"第{i}章 条目", "level": 2, "page": i}
                                      for i in range(4)]),
            _page(kind="toc", blocks=[{"t": "toc", "text": f"第{i+4}章 条目", "level": 2, "page": i + 4}
                                      for i in range(4)]),
            _page(blocks=[{"t": "toc", "text": "3.1 The action principle … 43", "level": 2, "page": 43},
                          {"t": "toc", "text": "3.2 Noether's theorem … 46", "level": 2, "page": 46},
                          {"t": "text", "text": "正文段落"}]),
        ])
        cl = self.content_list()
        dense = [b for b in cl if b["page_idx"] == 0 and b.get("text_level") == 1]
        self.assertEqual(len(dense), 4)                      # 真目录区保留 MinerU 形态
        self.assertIn("… 0", dense[0]["text"])
        mini = [b for b in cl if b["page_idx"] == 2 and "action principle" in b.get("text", "")]
        self.assertEqual(len(mini), 1)
        self.assertNotIn("text_level", mini[0])              # 不晋升标题候选
        self.assertNotIn("…", mini[0]["text"])               # 剥掉页码后缀


class TestHelpers(unittest.TestCase):
    def test_toc_region_pages(self):
        """目录区判定：密集连续组入选；孤立 mini-TOC 不入选。"""
        from stage1_vlm import _toc_region_pages, _strip_page_suffix
        pj = {i: {"blocks": [{"t": "toc", "text": f"x{j}", "level": 2, "page": j}
                             for j in range(4)]} for i in range(6, 10)}
        pj[59] = {"blocks": [{"t": "toc", "text": "a … 43", "level": 2, "page": 43},
                             {"t": "toc", "text": "b … 46", "level": 2, "page": 46}]}
        self.assertEqual(_toc_region_pages(pj), {6, 7, 8, 9})
        self.assertEqual(_toc_region_pages({59: pj[59]}), set())   # 孤立+不足 5 条 → 无目录区
        self.assertEqual(_strip_page_suffix("7.1 The QED Lagrangian … 180"),
                         "7.1 The QED Lagrangian")
        self.assertEqual(_strip_page_suffix("1.1 Overview …… 1"), "1.1 Overview")

    def test_drop_minitoc_lines(self):
        """mini-TOC 行四条件齐才丢：命中先验 + 同页成串 + 下游有真标题 +
        不在目录区；孤行/下游无标题/目录区 一律保留。"""
        from stage1_vlm import _drop_minitoc_lines, _toc_region_pages
        toc = [{"text": "3.1 The action principle", "level": 3, "page": 43},
               {"text": "3.2 Noether's theorem", "level": 3, "page": 46}]
        pj = {
            6: {"blocks": [{"t": "toc", "text": f"x{i}", "level": 2, "page": i} for i in range(4)]},
            7: {"blocks": [{"t": "toc", "text": f"y{i}", "level": 2, "page": i} for i in range(4)]},
            59: {"blocks": [{"t": "toc", "text": "3.1 The action principle … 43", "level": 2, "page": 43},
                            {"t": "toc", "text": "3.2 Noether's theorem … 46", "level": 2, "page": 46},
                            {"t": "title", "text": "3.1 The action principle", "level": 3},
                            {"t": "text", "text": "正文"}]},   # 真节题与 mini-TOC 同页（QFT 实态）
            61: {"blocks": [{"t": "text", "text": "3.2 Noether's theorem"}]},
            70: {"blocks": [{"t": "toc", "text": "x9", "level": 2, "page": 9}]},   # 孤行不成串
        }
        region = _toc_region_pages(pj)
        n = _drop_minitoc_lines(pj, region, toc)
        self.assertEqual(n, 2)                                   # mini-TOC 两行被丢
        self.assertEqual(len(pj[59]["blocks"]), 2)               # 同页真节题+正文 保留
        self.assertEqual(pj[59]["blocks"][0]["t"], "title")
        self.assertEqual(len(pj[70]["blocks"]), 1)               # 孤行保留
        self.assertEqual(len(pj[6]["blocks"]), 4)                # 目录区不动
        # 下游无真标题 → 不丢
        pj2 = {80: {"blocks": [{"t": "toc", "text": "唯一条 Z … 1", "level": 2, "page": 1},
                               {"t": "toc", "text": "唯二条 W … 2", "level": 2, "page": 2}]}}
        self.assertEqual(_drop_minitoc_lines(pj2, set(), toc), 0)
        self.assertEqual(len(pj2[80]["blocks"]), 2)

    def test_head_is_running(self):
        """章节内书眉（标题=当前章题、页码已深入）不再发射 header；
        章首页书眉（页码==条目页，病例 004 晋升通道）保留；页码缺失保留。"""
        from stage1_vlm import _head_is_running
        toc = [{"text": "3.1 The action principle", "level": 3, "page": 43}]
        self.assertTrue(_head_is_running("3.1 The action principle", 45, toc))
        self.assertTrue(_head_is_running("3.1 The action principle", 44, toc))
        self.assertFalse(_head_is_running("3.1 The action principle", 43, toc))   # 章首页保留
        self.assertFalse(_head_is_running("3.1 The action principle", None, toc))  # 页码缺失保留
        self.assertFalse(_head_is_running("别的书眉", 45, toc))                    # 非章题保留
        self.assertFalse(_head_is_running("3.1 The action principle", 45,
                                          [{"text": "3.1 The action principle", "page": None}]))

    def test_merge_bare_number_titles(self):
        from stage1_vlm import _merge_bare_number_titles
        out = _merge_bare_number_titles([
            {"t": "title", "text": "7", "level": 2},
            {"t": "title", "text": "Quantum electrodynamics", "level": 2},
            {"t": "text", "text": "正文"}])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["text"], "7 Quantum electrodynamics")
        self.assertEqual(out[0]["level"], 2)
        # 不合并：裸编号后紧跟正文
        out = _merge_bare_number_titles([{"t": "title", "text": "7", "level": 2},
                                         {"t": "text", "text": "正文"}])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["text"], "7")
        # 不合并：非裸编号
        out = _merge_bare_number_titles([{"t": "title", "text": "第七章 民法", "level": 2},
                                         {"t": "title", "text": "另一标题", "level": 2}])
        self.assertEqual(len(out), 2)

    def test_table_md_to_html(self):
        """markdown pipe 表格 → 良构 HTML（转义/补列）；坏格式回退 None（不动作）。"""
        from stage1_vlm import _table_md_to_html
        md = "| 情绪 | 程度 |\n|---|---|\n| 焦虑 | 80 |\n| 羞愧 <羞耻> | 60 |"
        html = _table_md_to_html(md)
        self.assertIn("<table>", html)
        self.assertIn("<th>情绪</th>", html)
        self.assertIn("羞愧 &lt;羞耻&gt;", html)   # 单元格转义
        # 列数不齐自动补齐
        md2 = "| A | B | C |\n|---|---|---|\n| 1 | 2 |"
        self.assertIn("<td></td>", _table_md_to_html(md2))
        # 无分隔行 → None（回退文本）
        self.assertIsNone(_table_md_to_html("普通文本\n没有表格"))
        # 单列 → None
        self.assertIsNone(_table_md_to_html("| A |\n|---|\n| 1 |"))

    def test_fix_dangling_eq_numbers(self):
        """游离公式编号归位：\\qqud(3.47) 裸源码并入 $$；块首 (3.47) 移入上块；
        '(1) 第一点' 列表标记不吃。"""
        from stage1_vlm import _fix_dangling_eq_numbers
        # A) 块内游离
        out = _fix_dangling_eq_numbers([
            {"t": "text", "text": "$$E=mc^2$$ \\qqud (3.47) The interaction is"}])
        self.assertIn("\\qquad (3.47)$$", out[0]["text"])
        self.assertIn("The interaction", out[0]["text"])
        # B) 块首裸编号
        out = _fix_dangling_eq_numbers([
            {"t": "text", "text": "$$H=\\sum_i p_i\\dot q_i-L$$"},
            {"t": "text", "text": "(3.47)The next sentence"}])
        self.assertIn("\\qquad (3.47)$$", out[0]["text"])
        self.assertEqual(out[1]["text"], "The next sentence")
        # 列表标记不吃
        out = _fix_dangling_eq_numbers([
            {"t": "text", "text": "$$E=mc^2$$"},
            {"t": "text", "text": "(1) 第一点：需要"}])
        self.assertEqual(out[0]["text"], "$$E=mc^2$$")
        self.assertIn("(1)", out[1]["text"])

    def test_running_head_demote(self):
        """页眉泄漏降级：数字页码+书眉候选 → header；章题/年份/页中 不动。"""
        from stage1_vlm import _demote_running_heads, _head_candidates
        cands = {"民法总论", "第一章民法概念论"}
        out = _demote_running_heads(
            [{"t": "text", "text": "012 民法总论"}, {"t": "text", "text": "正文"}], cands)
        self.assertEqual(out[0]["t"], "header")
        self.assertEqual(out[1]["t"], "text")
        out = _demote_running_heads(
            [{"t": "text", "text": "第一章 民法概念论 013"}, {"t": "text", "text": "正文"}], cands)
        self.assertEqual(out[0]["t"], "header")
        # 章题本身无页码 → 豁免；年份开头正文 → 不动；页中（idx>1）→ 不动
        out = _demote_running_heads([{"t": "title", "text": "第一章 民法概念论", "level": 2}], cands)
        self.assertEqual(out[0]["t"], "title")
        out = _demote_running_heads([{"t": "text", "text": "1997 年版教材认为……"}], cands)
        self.assertEqual(out[0]["t"], "text")
        out = _demote_running_heads([{"t": "text", "text": "甲"}, {"t": "text", "text": "乙"},
                                     {"t": "text", "text": "012 民法总论"}], cands)
        self.assertEqual(out[2]["t"], "text")
        # 候选来源：running_head 频次 ≥3 入选，<3 不入选
        pj = {i: {"running_head": "民法总论"} for i in range(3)}
        pj[9] = {"running_head": "偶发页眉"}
        got = _head_candidates(pj, [{"text": "第二章 权利主体", "level": 1}])
        self.assertIn("民法总论", got)
        self.assertIn("第二章权利主体", got)
        self.assertNotIn("偶发页眉", got)

    def test_split_head(self):
        """书眉拆分归一：页码归 page_number、标题归 header（防 popo 首现留正文）。"""
        from stage1_vlm import _split_head
        self.assertEqual(_split_head("012 民法总论"), ("012", "民法总论"))
        self.assertEqual(_split_head("第一章 民法概念论 013"), ("013", "第一章 民法概念论"))
        self.assertEqual(_split_head("民法总论"), (None, "民法总论"))
        self.assertEqual(_split_head("012"), ("012", None))
        self.assertEqual(_split_head(""), (None, None))

    def test_synth_bbox_monotonic(self):
        ys = [_synth_bbox(i, 10)[1] for i in range(10)]
        self.assertEqual(ys, sorted(ys))
        self.assertEqual(len(set(ys)), 10)

    def test_extract_json(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json('前文废话 {"a": 2} 后记'), {"a": 2})
        self.assertIsNone(extract_json('{"a": 1'))
        self.assertIsNone(extract_json(''))
        self.assertIsNone(extract_json(None))

    def test_reasoning_map(self):
        # glm-5.3-flash 恒思考：off 也必须落到 low，永不下发 disabled（400 实证）
        self.assertEqual(reasoning_extra("https://api.z.ai/api/paas/v4", "glm-5.3-flash", "off"),
                         {"reasoning_effort": "low"})
        self.assertEqual(reasoning_extra("https://open.bigmodel.cn/api/paas/v4", "glm-4.6v", "off"),
                         {"thinking": {"type": "disabled"}})
        self.assertEqual(reasoning_extra("https://api.deepseek.com", "deepseek-flash", "off"),
                         {"thinking": {"type": "disabled"}})
        self.assertEqual(reasoning_extra("https://api.deepseek.com", "deepseek-flash", "low"),
                         {"reasoning_effort": "low"})
        self.assertEqual(reasoning_extra("https://dashscope.aliyuncs.com/compatible-mode/v1",
                                         "qwen3-vl-flash", "off"), {"enable_thinking": False})
        self.assertEqual(reasoning_extra("https://ark.cn-beijing.volces.com/api/v3",
                                         "doubao-seed-2-1-turbo-260628", "off"),
                         {"thinking": {"type": "disabled"}})
        self.assertEqual(reasoning_extra("https://unknown.example.com", "m", "off"), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
