"""llm_thinking 思考参数协商的单元测试（病例 025）。

正反例覆盖：
- 正：端点接受 disabled → 单次调用直通；
- 正：恒思考模型 400(1210) → 降 effort_low 重试成功，且后续调用直接用 effort_low；
- 正：effort_low 仍被拒 → 降 none（不下发思考参数）成功；
- 反：非思考类 400 / 非 400 错误 → 原样上抛，模式不漂移；
- 反：模式用尽（none 仍 400）→ 上抛。

跑法：.venv/Scripts/python.exe tests/test_llm_thinking.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_thinking  # noqa: E402


class FakeBadRequest(Exception):
    """模拟 openai.BadRequestError 的最小形状（协商判定只看 status_code + 报文）"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class FakeMessage:
    content = "ok"


class FakeChoice:
    message = FakeMessage()


class FakeResponse:
    choices = [FakeChoice()]


class FakeCompletions:
    """按脚本演戏：列表元素为 Exception 实例则抛出，否则返回 FakeResponse。"""

    def __init__(self, script: list):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.script and isinstance(self.script[0], Exception):
            raise self.script.pop(0)
        return FakeResponse()


class FakeClient:
    def __init__(self, script: list):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(script)


def reset_mode():
    llm_thinking._mode = "disabled"


def test_passthrough_when_disabled_accepted():
    reset_mode()
    client = FakeClient([])
    resp = llm_thinking.chat_create(client, model="m", messages=[])
    assert resp.choices[0].message.content == "ok"
    assert client.chat.completions.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert llm_thinking.current_mode() == "disabled"


def test_downgrade_to_effort_low_on_thinking_rejection():
    reset_mode()
    glm_1210 = FakeBadRequest("Error code: 400 - {'error': {'code': '1210', "
                              "'message': '该模型始终思考，暂不支持关闭思考，请使用 low、high 或 max。'}}")
    client = FakeClient([glm_1210])
    resp = llm_thinking.chat_create(client, model="glm-5.3-flash", messages=[])
    assert resp.choices[0].message.content == "ok"
    calls = client.chat.completions.calls
    assert len(calls) == 2
    assert calls[1]["extra_body"] == {"reasoning_effort": "low"}
    assert llm_thinking.current_mode() == "effort_low"
    # 协商结果对后续调用直接生效（不重复探测）
    client2 = FakeClient([])
    llm_thinking.chat_create(client2, model="glm-5.3-flash", messages=[])
    assert client2.chat.completions.calls[0]["extra_body"] == {"reasoning_effort": "low"}


def test_downgrade_to_none_when_effort_also_rejected():
    reset_mode()
    reject = FakeBadRequest("Error code: 400 - unknown parameter: reasoning_effort (thinking)")
    client = FakeClient([reject, reject])
    resp = llm_thinking.chat_create(client, model="weird", messages=[])
    assert resp.choices[0].message.content == "ok"
    assert llm_thinking.current_mode() == "none"
    assert "extra_body" not in client.chat.completions.calls[-1]


def test_non_thinking_400_raises_and_mode_stays():
    reset_mode()
    client = FakeClient([FakeBadRequest("Error code: 400 - model not found")])
    try:
        llm_thinking.chat_create(client, model="ghost", messages=[])
        raise AssertionError("应原样上抛非思考类 400")
    except FakeBadRequest:
        pass
    assert llm_thinking.current_mode() == "disabled"


def test_non_400_raises():
    reset_mode()
    client = FakeClient([FakeBadRequest("Error code: 500 - server error", status_code=500)])
    try:
        llm_thinking.chat_create(client, model="m", messages=[])
        raise AssertionError("应原样上抛 500")
    except FakeBadRequest:
        pass
    assert llm_thinking.current_mode() == "disabled"


def test_mode_exhausted_raises():
    reset_mode()
    reject = FakeBadRequest("Error code: 400 - 思考参数不被支持")
    client = FakeClient([reject, reject, reject])
    try:
        llm_thinking.chat_create(client, model="m", messages=[])
        raise AssertionError("模式用尽后应上抛")
    except FakeBadRequest:
        pass
    assert llm_thinking.current_mode() == "none"


def test_stale_inflight_rejection_does_not_double_downgrade():
    """并发竞态：请求以 disabled 发出，在飞期间另一线程已降档到 effort_low；
    该请求的迟到 400 只应触发按新模式重发，不得再把 effort_low 砸成 none。"""
    reset_mode()

    class RacingCompletions(FakeCompletions):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                # 模拟另一线程此刻完成降档，然后本请求以旧模式被拒
                llm_thinking._mode = "effort_low"
                raise FakeBadRequest("Error code: 400 - {'error': {'code': '1210', "
                                     "'message': '该模型始终思考，不支持关闭思考'}}")
            return FakeResponse()

    client = FakeClient([])
    client.chat.completions = RacingCompletions([])
    resp = llm_thinking.chat_create(client, model="glm-5.3-flash", messages=[])
    assert resp.choices[0].message.content == "ok"
    calls = client.chat.completions.calls
    assert len(calls) == 2
    assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert calls[1]["extra_body"] == {"reasoning_effort": "low"}
    assert llm_thinking.current_mode() == "effort_low"  # 未被砸到 none


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"全部 {len(tests)} 例通过")
