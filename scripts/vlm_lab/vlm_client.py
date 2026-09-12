"""多模态 chat 客户端：OpenAI 兼容端点 + 思考档位映射（移植自 SageRead
reasoning-map.ts 2026-09-10 版）+ JSONL 调用日志（token/延迟/finish_reason）。"""
import base64
import json
import time

from openai import OpenAI

from .common import load_providers

# ---- 思考档位映射（移植自 SageRead reasoning-map.ts，仅保留实验相关型号）----
# 恒思考不可关（effort 型）：glm-5.3-flash levels=[low,high,max]，传 disabled 直接 400
_GLM_ALWAYS_ON = {"glm-5.3-flash", "glm-5.3"}
# GLM 4.x 开关型：thinking:{type:disabled/enabled}（v 系视觉同基座）
# deepseek-flash：thinking disabled 开关 + reasoning_effort low/high/max
# qwen3-vl-flash：enable_thinking + thinking_budget(1-32768)
# doubao-seed-1-6-flash：thinking:{type} 三态开关，无 effort 档


def reasoning_extra(provider: str, model: str, level: str | None) -> dict:
    if level in (None, "auto"):
        return {}
    if provider == "deepseek":
        return {"thinking": {"type": "disabled"}} if level == "off" else {"reasoning_effort": level}
    if provider in ("zai", "bigmodel"):
        if model in _GLM_ALWAYS_ON:
            return {"reasoning_effort": "low" if level == "off" else level}
        if level == "off":
            return {"thinking": {"type": "disabled"}}
        if level == "on":
            return {"thinking": {"type": "enabled"}}
        return {"reasoning_effort": level}
    if provider == "dashscope":
        if level == "off":
            return {"enable_thinking": False}
        budget = {"low": 1024, "medium": 8192, "high": 32768}.get(level)
        return {"enable_thinking": True, "thinking_budget": budget} if budget else {}
    if provider == "ark":
        if level == "off":
            return {"thinking": {"type": "disabled"}}
        if level == "on":
            return {"thinking": {"type": "enabled"}}
        return {}
    return {}


_clients: dict = {}


def get_client(provider: str) -> OpenAI:
    if provider not in _clients:
        p = load_providers()[provider]
        _clients[provider] = OpenAI(base_url=p["base_url"], api_key=p["api_key"], timeout=300.0, max_retries=0)
    return _clients[provider]


def chat(provider: str, model: str, prompt: str, images: list[bytes] | tuple = (),
         reasoning: str | None = None, max_tokens: int = 8192, temperature: float = 0.0,
         log: str | None = None, extra: dict | None = None) -> dict:
    """images 为 PNG bytes 列表（图在前文在后）。返回含 content/usage/latency/error 的 dict。"""
    content = [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(im).decode()}}
        for im in images
    ]
    content.append({"type": "text", "text": prompt})
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    eb = {**reasoning_extra(provider, model, reasoning), **(extra or {})}
    rec = {"provider": provider, "model": model, "reasoning": reasoning,
           "n_images": len(images), "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    t0 = time.time()
    try:
        r = get_client(provider).chat.completions.create(**body, extra_body=eb or None)
        msg = r.choices[0].message
        rec.update(
            latency_s=round(time.time() - t0, 2),
            content=msg.content,
            finish_reason=r.choices[0].finish_reason,
            usage=r.usage.model_dump() if r.usage else None,
        )
    except Exception as e:  # 记录后继续，失败方向=不动作
        rec.update(latency_s=round(time.time() - t0, 2), error=f"{type(e).__name__}: {e}")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec
