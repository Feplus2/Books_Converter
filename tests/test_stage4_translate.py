#!/usr/bin/env python3
"""stage4 批响应校验回归（Feeling Great 译文错位病例）。

背景：_translate_batch 旧版按序号静默映射，模型合并/跳号时整批错位
（'11 | The Great Escape' 章题被配上 '2. ___'），且缺号译文经断点续翻
缓存复用扩散。修复后只接受 1..N 全覆盖响应，缺号抛错走重试/拆半，
失败方向=保留原文。

运行: python tests/test_stage4_translate.py
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stage4_translate as st  # noqa: E402

passed = failed = 0


def ok(cond, label):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {label}")
    else:
        failed += 1
        print(f"  ✗ {label}")


class _Msg:
    def __init__(self, content):
        self.content = content


class _FakeClient:
    """按预设响应脚本回答的假 OpenAI 客户端"""

    def __init__(self, responder):
        self.responder = responder
        self.calls = 0
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.calls += 1
        return type("R", (), {"choices": [type("C", (), {"message":
                _Msg(self.responder(kw))})]})()


_BATCH = [("10", "Chapter Ten Title"),
          ("11", "Chapter Eleven Title"),
          ("12", "Chapter Twelve Title"),
          ("13", "Chapter Thirteen Title")]

# 不打断测试节奏：重试 sleep 置空
st.time.sleep = lambda *a, **kw: None

print("1. 全覆盖响应正常映射")
full = _FakeClient(lambda kw: json.dumps(
    {"translations": {str(i): f"译文{i}" for i in range(1, 5)}, "terms": {}},
    ensure_ascii=False))
out, _terms = st._translate_batch(full, "m", "sys", _BATCH, "")
ok(out == {"10": "译文1", "11": "译文2", "12": "译文3", "13": "译文4"},
   "1..N 全覆盖逐条对位")
ok(full.calls == 1, "一次通过不重试")

print("2. 彻底坏响应 → 全批保留原文（失败方向=不动作）")
broken = _FakeClient(lambda kw: "这不是JSON")
out, _ = st._translate_batch(broken, "m", "sys", _BATCH, "")
ok(out == {}, "重试+拆半到底仍失败 → 返回空（原文兜底）")
ok(broken.calls > 1, "确实走了重试/拆半")

print("3. 顶层跳号被拒收 → 拆半后各自齐全 → 全部译出")
state3 = {"n": 0}

def gapped(kw):
    state3["n"] += 1
    n_items = sum(1 for line in kw["messages"][1]["content"].split("\n")
                  if line.startswith("["))
    if n_items == 4:  # 顶层缺 3 号（紧凑/跳号错位风险）→ 必须拒收
        return json.dumps({"translations": {"1": "一", "2": "二", "4": "四"},
                           "terms": {}}, ensure_ascii=False)
    return json.dumps({"translations": {str(i): f"子批译文{state3['n']}-{i}"
                                        for i in range(1, n_items + 1)},
                       "terms": {}}, ensure_ascii=False)

gap_client = _FakeClient(gapped)
out, _ = st._translate_batch(gap_client, "m", "sys", _BATCH, "")
ok(len(out) == 4 and all(k in out for k in ("10", "11", "12", "13")),
   "跳号拒收 + 拆半自救后 4 条全部译出")
ok(gap_client.calls > 1, "跳号响应触发了重试/拆半")

print("4. 首次缺号、拆半后各自齐全 → 全部译出")
# 整批缺号；拆半后（每半 2 条）响应齐全
state = {"n": 0}

def flaky(kw):
    state["n"] += 1
    n_items = sum(1 for line in kw["messages"][1]["content"].split("\n")
                  if line.startswith("["))
    if n_items == 4:
        return json.dumps({"translations": {"1": "x"}, "terms": {}})
    return json.dumps({"translations": {str(i): f"半批译文{state['n']}-{i}"
                                        for i in range(1, n_items + 1)},
                       "terms": {}}, ensure_ascii=False)

out, _ = st._translate_batch(_FakeClient(flaky), "m", "sys", _BATCH, "")
ok(len(out) == 4 and all(k in out for k in ("10", "11", "12", "13")),
   "拆半自救后 4 条全部译出")

print(f"\n{passed} 过 / {failed} 挂")
sys.exit(1 if failed else 0)
