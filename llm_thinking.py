"""LLM 思考参数协商：能关则关，不能关取最低思考强度；永不因思考参数拦模型。

背景（病例 025）：原实现对全端点硬编码 thinking:{type:disabled}。思考恒开
模型（GLM-5.3 系）直接 400（code 1210「该模型始终思考，暂不支持关闭思考」），
调用方把 400 当普通失败走重试退避——每个分块白烧 ~60s，全书百余分块纯
空转，直至 SageRead 看门狗杀进程（《汉语语义学》转换超时事故）。

协商策略（进程级一次定终身，不逐调用探测——同进程端点/模型不变）：
  disabled →（端点拒思考参数）→ effort_low（reasoning_effort="low"，对齐
  SageRead 思考强度枚举表 reasoning-map.ts 的「恒思考模型取 levels[0]」口径）
  →（仍被拒）→ none（不下发任何思考参数，交由模型默认行为）。

降级只认「思考参数被拒」这一种 400，其余错误原样上抛（交给调用方既有
重试/降级链）。用什么模型是用户的自由选择——恒思考模型"慢但可用"，
绝不拦截。
"""

import logging

logger = logging.getLogger(__name__)

_MODES = ("disabled", "effort_low", "none")
_mode = "disabled"


def current_mode() -> str:
    """当前协商到的思考参数模式（测试与日志观测用）"""
    return _mode


def _extra_body_for(mode: str) -> dict | None:
    if mode == "disabled":
        return {"thinking": {"type": "disabled"}}
    if mode == "effort_low":
        return {"reasoning_effort": "low"}
    return None


def _is_thinking_rejection(exc: Exception) -> bool:
    """是否「端点拒绝思考参数」的 400（GLM code 1210 / 报文提到 thinking/思考）。"""
    if getattr(exc, "status_code", None) != 400:
        return False
    msg = str(exc)
    return "1210" in msg or "thinking" in msg.lower() or "思考" in msg


def chat_create(client, **kwargs):
    """client.chat.completions.create 的思考参数协商包装。

    用法：把 client.chat.completions.create(...) 换成 chat_create(client, ...)，
    不要再传 extra_body 的思考参数（本函数按协商结果注入）。

    首次被拒即降级并对后续所有调用生效；降级后的重试不消耗调用方的重试次数。
    非思考类 400 与模式用尽后的 400 一律上抛。

    并发竞态守卫：多线程下（stage2 四并发）被拒绝的可能是「降档前已在飞」的
    旧模式请求——请求模式与当前模式不一致时只按新模式重发，不再降档
    （否则首波 4 个 disabled 请求的 400 会把模式一路砸到 none，丢掉
    effort_low 的提速——病例 025 实测 glm-5.3-flash：16 vs 3322 output tokens）。
    """
    global _mode
    while True:
        req_mode = _mode
        extra = _extra_body_for(req_mode)
        if extra is None:
            kwargs.pop("extra_body", None)
        else:
            kwargs["extra_body"] = extra
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as e:
            if not _is_thinking_rejection(e):
                raise
            if req_mode != _mode:
                continue  # 在飞旧模式请求的迟到拒绝：直接按协商后的新模式重发
            if req_mode == _MODES[-1]:
                raise
            _mode = _MODES[_MODES.index(_mode) + 1]
            logger.warning(
                f"  端点拒绝思考参数（{req_mode}），降为 {_mode}（本进程后续调用直接生效）: {e}")
