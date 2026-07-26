r"""
无界面进度报告器 — 供 SageRead sidecar 集成使用。
接口对齐 progress_ui.ProgressWindow，向 stdout 打印 JSON 行。

进度平滑策略（与 GUI 版 _tick 一致）：
- 阶段跨度按预估耗时占比分配（stage_estimates）
- fraction 只推进"目标百分比"（单调不减）
- 后台节拍线程：无 fraction 输入时目标在阶段跨度内缓慢爬行（上限为跨度 90%），
  显示值缓动逼近目标 —— 绝不静止、绝不倒退
- JSON 一律 ensure_ascii=True（Windows 管道 stdout 默认 GBK，\uXXXX 转义全编码安全）
"""

import json
import sys
import threading
import time

_TICK_INTERVAL = 0.5    # 节拍间隔（秒）
_CREEP_STEP = 0.4       # 每拍爬行量（≈0.8%/s，对齐 GUI 的 0.75%/s）
_CREEP_CAP_RATIO = 0.9  # 爬行上限：阶段跨度的 90%
_EASE_FACTOR = 0.35     # 缓动逼近系数
_EASE_MIN = 0.4         # 缓动最小步长

_emit_lock = threading.Lock()


def _emit(obj: dict):
    try:
        with _emit_lock:
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

        # 平滑进度状态：target 由 fraction/完成/爬行共同推进（单调不减），
        # percent 为对外显示的缓动值
        self._target = 0.0
        self._percent = 0.0
        self._active_stage = 0
        self._finished = False
        self._tick_thread: threading.Thread | None = None

    # ── 平滑节拍（对齐 GUI _tick） ─────────────────────────

    def _tick_loop(self):
        while not self._finished:
            time.sleep(_TICK_INTERVAL)
            if self._finished:
                break
            # 无 fraction 输入时，目标在阶段跨度内缓慢爬行（封顶 90%）
            if self._active_stage:
                base, ceiling = self._stage_span.get(self._active_stage, (0.0, 100.0))
                creep_cap = base + (ceiling - base) * _CREEP_CAP_RATIO
                if self._target < creep_cap:
                    self._target = min(self._target + _CREEP_STEP, creep_cap)
            # 显示值缓动逼近目标
            if self._percent < self._target:
                self._percent = min(
                    self._percent + max((self._target - self._percent) * _EASE_FACTOR, _EASE_MIN),
                    self._target,
                )
                obj = {"type": "progress", "percent": round(self._percent, 1)}
                if self._active_stage:
                    obj["stage"] = self._active_stage
                _emit(obj)

    # ── 公共 API（与 ProgressWindow 对齐） ─────────────────

    def start(self):
        _emit({"type": "start", "title": self._book_name, "engine": self._engine,
               "translate": self._translate})
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._tick_thread.start()

    def update_stage(self, stage: int, title: str, detail: str = "",
                     fraction: float | None = None):
        self._active_stage = stage
        if fraction is not None:
            base, ceiling = self._stage_span.get(stage, (0.0, 100.0))
            self._target = max(self._target,
                               base + max(0.0, min(fraction, 1.0)) * (ceiling - base))
        _emit({"type": "progress", "stage": stage, "stage_name": title,
               "detail": detail, "fraction": fraction, "percent": round(self._percent, 1)})

    def complete_stage(self, stage: int, title: str, elapsed: float):
        _base, ceiling = self._stage_span.get(stage, (0.0, 100.0))
        self._target = max(self._target, ceiling)
        if self._active_stage == stage:
            self._active_stage = 0
        _emit({"type": "stage_done", "stage": stage, "stage_name": title,
               "elapsed": round(elapsed, 1), "percent": round(self._percent, 1)})

    def finish(self, epub_path: str, total_elapsed: float):
        self._target = 100.0
        self._percent = 100.0
        self._finished = True
        _emit({"type": "done", "epub_path": str(epub_path), "title": self._book_name,
               "elapsed": round(total_elapsed, 1), "percent": 100.0})

    def close(self):
        self._finished = True


def emit_error(message: str):
    _emit({"type": "error", "message": message})
