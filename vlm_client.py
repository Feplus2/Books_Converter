"""多模态 LLM 客户端（OpenAI 兼容端点）：图像消息 + 思考档位映射 + 重试 + JSON 抽取。

思考档位映射移植自 SageRead reasoning-map.ts（2026-09-10 版）：
- 恒思考 effort 型（glm-5.3-flash：low/high/max，**不可关**，传 disabled 直接 400）
- 开关型 thinking:{type:disabled/enabled}：GLM 4.x 系、deepseek-flash、doubao（火山）
- budget 型 enable_thinking+thinking_budget：qwen/dashscope
未收录型号：不下发任何思考参数（默认放行，与 SageRead 口径一致）。

失败方向约定（铁律 0）：chat() 永不抛出；返回 {"ok": False, ...}，由调用方
决定降级（本页留空标记 error，绝不编造内容）。
"""
import base64
import json
import logging
import re
import time

from openai import OpenAI

logger = logging.getLogger(__name__)

_GLM_ALWAYS_ON = {"glm-5.3-flash", "glm-5.3"}


def reasoning_extra(base_url: str, model: str, level: str | None) -> dict:
    """按 (端点, 型号, 档位) 生成 extra_body 思考参数。level: off/low/medium/high/max/on。"""
    if level in (None, "auto"):
        return {}
    host = (base_url or "").lower()
    m = (model or "").lower()
    if "deepseek" in host:
        return {"thinking": {"type": "disabled"}} if level == "off" else {"reasoning_effort": level}
    if "bigmodel" in host or "z.ai" in host:
        if m in _GLM_ALWAYS_ON:
            return {"reasoning_effort": "low" if level == "off" else level}
        if level == "off":
            return {"thinking": {"type": "disabled"}}
        if level == "on":
            return {"thinking": {"type": "enabled"}}
        return {"reasoning_effort": level}
    if "dashscope" in host:
        if level == "off":
            return {"enable_thinking": False}
        budget = {"low": 1024, "medium": 8192, "high": 32768}.get(level)
        return {"enable_thinking": True, "thinking_budget": budget} if budget else {}
    if "volces" in host:
        if level == "off":
            return {"thinking": {"type": "disabled"}}
        if level == "on":
            return {"thinking": {"type": "enabled"}}
        return {}
    return {}


_JSON_RE = re.compile(r"\{.*\}", re.S)


def extract_json(content: str) -> dict | None:
    """从模型输出抽取最外层 JSON 对象；容忍 ```json 围栏。失败返回 None。"""
    if not content:
        return None
    m = _JSON_RE.search(content)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


class VlmClient:
    """单个 (base_url, api_key, model) 组合的多模态调用器。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 reasoning: str | None = "low", timeout: float = 300.0,
                 max_retries: int = 3):
        self.base_url = base_url
        self.model = model
        self.reasoning = reasoning
        self.max_retries = max_retries
        self.client = OpenAI(base_url=base_url, api_key=api_key,
                             timeout=timeout, max_retries=0)

    def chat(self, prompt: str, images: list[bytes] | tuple = (),
             max_tokens: int = 16384, temperature: float = 0.0,
             want_json: bool = True) -> dict:
        """调用一次（含重试）。images 为 PNG/JPEG bytes（图在前文在后）。

        返回 {"ok", "content", "json", "usage", "latency_s", "finish_reason", "error"}。
        重试策略：网络/5xx 错误 → 退避重试；finish=length 或 JSON 不闭合 →
        加大 max_tokens 重试；全部失败 → ok=False（不编造任何内容）。
        """
        content_parts = [
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(im).decode()}}
            for im in images
        ]
        content_parts.append({"type": "text", "text": prompt})
        eb = reasoning_extra(self.base_url, self.model, self.reasoning)

        mt = max_tokens
        last_err = ""
        for attempt in range(self.max_retries):
            t0 = time.time()
            try:
                r = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": content_parts}],
                    max_tokens=mt, temperature=temperature,
                    extra_body=eb or None,
                )
                msg = r.choices[0].message
                finish = r.choices[0].finish_reason
                text = msg.content or ""
                rec = {
                    "ok": True, "content": text, "finish_reason": finish,
                    "usage": r.usage.model_dump() if r.usage else None,
                    "latency_s": round(time.time() - t0, 2),
                }
                if want_json:
                    rec["json"] = extract_json(text)
                    if rec["json"] is None or finish == "length":
                        last_err = f"JSON 不完整/截断 (finish={finish})"
                        logger.warning(f"    VLM 输出截断/非法 JSON，重试 {attempt + 1}: {last_err}")
                        mt = int(mt * 1.5)
                        continue
                return rec
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                wait = (attempt + 1) * 8
                logger.warning(f"    VLM 调用失败（{attempt + 1}/{self.max_retries}），"
                               f"{wait}s 后重试: {last_err[:150]}")
                time.sleep(wait)
        return {"ok": False, "error": last_err, "content": "", "json": None,
                "usage": None, "latency_s": None, "finish_reason": None}
