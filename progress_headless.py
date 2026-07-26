r"""
无界面进度报告器 — 供 SageRead sidecar 集成使用。
接口对齐 progress_ui.ProgressWindow，向 stdout 打印 JSON 行。

注意：json.dumps 使用 ensure_ascii=True（纯 ASCII 输出）——
Windows 下管道 stdout 默认走本地代码页（GBK），直接输出中文会在
Rust 侧按 UTF-8 读取时乱码；\uXXXX 转义在任何编码下都安全。
"""

import json
import sys


def _emit(obj: dict):
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


class HeadlessProgress:
    def __init__(self, book_name: str, engine: str = "hybrid",
                 stage_estimates: list | None = None,
                 translate: bool = False):
        self._book_name = book_name
        self._engine = engine
        self._translate = translate
        if stage_estimates and sum(stage_estimates) > 0:
            total = sum(stage_estimates)
            span = {}
            acc = 0.0
            for i, est in enumerate(stage_estimates, 1):
                nxt = acc + est / total * 100
                span[i] = (acc, min(nxt, 100.0))
                acc = nxt
            self._stage_span = span
        else:
            self._stage_span = {1: (0.0, 40.0), 2: (40.0, 95.0), 3: (95.0, 100.0)}
        self._percent = 0.0

    def _pct_for(self, stage: int, fraction: float) -> float:
        base, ceiling = self._stage_span.get(stage, (0.0, 100.0))
        return base + max(0.0, min(fraction, 1.0)) * (ceiling - base)

    def start(self):
        _emit({"type": "start", "title": self._book_name, "engine": self._engine,
               "translate": self._translate})

    def update_stage(self, stage: int, title: str, detail: str = "",
                     fraction: float | None = None):
        if fraction is not None:
            self._percent = max(self._percent, self._pct_for(stage, fraction))
        _emit({"type": "progress", "stage": stage, "stage_name": title,
               "detail": detail, "fraction": fraction, "percent": round(self._percent, 1)})

    def complete_stage(self, stage: int, title: str, elapsed: float):
        _base, ceiling = self._stage_span.get(stage, (0.0, 100.0))
        self._percent = max(self._percent, ceiling)
        _emit({"type": "stage_done", "stage": stage, "stage_name": title,
               "elapsed": round(elapsed, 1), "percent": round(self._percent, 1)})

    def finish(self, epub_path: str, total_elapsed: float):
        self._percent = 100.0
        _emit({"type": "done", "epub_path": str(epub_path), "title": self._book_name,
               "elapsed": round(total_elapsed, 1), "percent": 100.0})

    def close(self):
        pass


def emit_error(message: str):
    _emit({"type": "error", "message": message})
