"""provider 连通性冒烟：每个 provider 发一条纯文本 ping，验证 key/端点/模型 ID。

用法：.venv/Scripts/python.exe -m scripts.vlm_lab.ping [provider ...]
"""
import sys

from .common import RUNS
from .vlm_client import chat

PINGS = {
    "zai": "glm-5.3-flash",
    "dashscope": "qwen3-vl-flash",
    "deepseek": "deepseek-flash",
    "ark": "doubao-seed-1-6-flash",
    "moonshot": "kimi-k2.6",
    "cherryin": "google/gemini-3-flash-preview",
}

if __name__ == "__main__":
    only = sys.argv[1:]
    for prov, model in PINGS.items():
        if only and prov not in only:
            continue
        r = chat(prov, model, "回复'pong'两个字即可。", reasoning="off", max_tokens=16,
                 log=str(RUNS / "ping.jsonl"))
        if "error" in r:
            print(f"{prov:10s} {model:32s} ERROR {r['error'][:150]}")
        else:
            u = r.get("usage") or {}
            print(f"{prov:10s} {model:32s} OK {r['latency_s']:6.2f}s "
                  f"in={u.get('prompt_tokens')} out={u.get('completion_tokens')} "
                  f"content={str(r.get('content'))[:40]!r}")
