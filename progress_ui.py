"""
进度窗口 — 在屏幕上显示实时转换进度。

用法:
    from progress_ui import ProgressWindow

    pw = ProgressWindow("民法总论")
    pw.start()
    pw.update_stage(1, "MinerU", "片 1/3: 第 1-200 页...")
    pw.complete_stage(1, "MinerU", elapsed=154.0)
    pw.finish(epub_path, total_elapsed)
"""

import queue
import threading
import tkinter as tk
from tkinter import ttk
import time

# ── 颜色主题 (Catppuccin Mocha) ───────────────────────────
_BG         = "#1e1e2e"   # 主背景
_SURFACE0   = "#313244"   # 卡片背景
_SURFACE1   = "#45475a"   # 高亮卡片
_SURFACE2   = "#585b70"   # 分隔线
_TEXT       = "#cdd6f4"   # 主文本
_SUBTEXT    = "#a6adc8"   # 次要文本
_OVERLAY    = "#6c7086"   # 等待状态文本
_BLUE       = "#89b4fa"   # 主强调色
_GREEN      = "#a6e3a1"   # 完成
_YELLOW     = "#f9e2af"   # 进行中
_RED        = "#f38ba8"   # 错误
_TEAL       = "#94e2d5"   # 辅助色
_BAR_BG     = "#45475a"   # 进度条背景
_BAR_ACTIVE = "#89b4fa"   # 进度条活跃


# ── 圆角矩形绘制 ──────────────────────────────────────────

def _rounded_rect(canvas, x, y, w, h, r, **kwargs):
    """在 Canvas 上绘制圆角矩形"""
    points = [
        x + r, y,
        x + w - r, y,
        x + w, y,
        x + w, y + r,
        x + w, y + h - r,
        x + w, y + h,
        x + w - r, y + h,
        x + r, y + h,
        x, y + h,
        x, y + h - r,
        x, y + r,
        x, y,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


# ── 主窗口 ────────────────────────────────────────────────

class ProgressWindow:
    """线程安全的 tkinter 进度窗口"""

    def __init__(self, book_name: str):
        self._queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._root = None
        self._book_name = book_name
        self._start_time = time.time()
        self._thread: threading.Thread | None = None

        # GUI 组件引用
        self._stage_frames: list[tk.Frame] = []
        self._stage_icons: list[tk.Label] = []
        self._stage_labels: list[tk.Label] = []
        self._stage_timers: list[tk.Label] = []
        self._detail_label: tk.Label | None = None
        self._elapsed_label: tk.Label | None = None
        self._progress_bar: ttk.Progressbar | None = None
        self._progress_frame: tk.Frame | None = None

    def start(self):
        """在后台线程中启动窗口"""
        self._thread = threading.Thread(target=self._run_gui, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def update_stage(self, stage: int, title: str, detail: str = ""):
        """更新当前阶段和详情"""
        self._queue.put(("update", stage, title, detail))

    def complete_stage(self, stage: int, title: str, elapsed: float):
        """标记阶段完成"""
        self._queue.put(("complete", stage, title, elapsed))

    def finish(self, epub_path: str, total_elapsed: float):
        """显示完成摘要"""
        self._queue.put(("finish", epub_path, total_elapsed))

    def close(self):
        """关闭窗口"""
        self._queue.put(("close",))

    # ── GUI 线程方法 ──────────────────────────────────────

    def _run_gui(self):
        """GUI 线程主循环"""
        root = tk.Tk()
        self._root = root
        root.title(f"Books_Converter — {self._book_name}")
        root.configure(bg=_BG)
        root.resizable(False, False)

        # 高 DPI 支持
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        # 窗口大小和位置（屏幕中央偏上）
        w, h = 720, 560
        sx = root.winfo_screenwidth()
        sy = root.winfo_screenheight()
        x = (sx - w) // 2
        y = max((sy - h) // 3, 50)
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.attributes("-topmost", True)

        # ttk 主题
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Custom.Horizontal.TProgressbar",
                         troughcolor=_BAR_BG,
                         background=_BLUE,
                         thickness=6,
                         borderwidth=0,
                         relief="flat")

        self._build_ui()
        self._ready.set()

        self._poll_queue()
        self._update_elapsed()
        root.mainloop()

    def _build_ui(self):
        """构建完整界面"""
        root = self._root
        outer = tk.Frame(root, bg=_BG)
        outer.pack(fill="both", expand=True, padx=28, pady=20)

        # ━━ 头部 ━━
        header = tk.Frame(outer, bg=_BG)
        header.pack(fill="x", pady=(0, 16))

        tk.Label(
            header, text=f"📖  {self._book_name}",
            bg=_BG, fg=_BLUE,
            font=("Segoe UI", 18, "bold"),
            anchor="w",
        ).pack(side="left")

        self._elapsed_label = tk.Label(
            header, text="00:00",
            bg=_BG, fg=_SUBTEXT,
            font=("Cascadia Code", 16),
            anchor="e",
        )
        self._elapsed_label.pack(side="right")

        # ━━ 分割线 ━━
        sep = tk.Frame(outer, bg=_SURFACE0, height=1)
        sep.pack(fill="x", pady=(0, 12))

        # ━━ 三个阶段卡片 ━━
        stages = [
            ("①", "MinerU", "PDF 解析 + OCR + 图片提取"),
            ("②", "DeepSeek V4 Flash", "语义结构分析 (3-pass)"),
            ("③", "EPUB 生成", "嵌套目录 + 图片 + 排版"),
        ]
        stages_container = tk.Frame(outer, bg=_BG)
        stages_container.pack(fill="x", pady=(0, 12))

        for i, (num, name, desc) in enumerate(stages):
            card = tk.Frame(stages_container, bg=_SURFACE0,
                            highlightbackground=_SURFACE0,
                            highlightthickness=1,
                            bd=0, padx=16, pady=14)
            card.pack(fill="x", pady=4)

            # 左侧：图标 + 序号
            icon = tk.Label(card, text="○", bg=_SURFACE0, fg=_OVERLAY,
                            font=("Segoe UI", 16), width=2)
            icon.pack(side="left", padx=(0, 10))

            # 中间：标题 + 描述
            info = tk.Frame(card, bg=_SURFACE0)
            info.pack(side="left", fill="x", expand=True)

            title_label = tk.Label(
                info, text=name,
                bg=_SURFACE0, fg=_OVERLAY,
                font=("Segoe UI Semibold", 12),
                anchor="w",
            )
            title_label.pack(fill="x")

            desc_label = tk.Label(
                info, text=desc,
                bg=_SURFACE0, fg=_OVERLAY,
                font=("Segoe UI", 9),
                anchor="w",
            )
            desc_label.pack(fill="x")

            # 右侧：计时
            timer = tk.Label(card, text="",
                             bg=_SURFACE0, fg=_OVERLAY,
                             font=("Cascadia Code", 10),
                             anchor="e")
            timer.pack(side="right")

            self._stage_frames.append(card)
            self._stage_icons.append(icon)
            self._stage_labels.append((title_label, desc_label))
            self._stage_timers.append(timer)

        # ━━ 进度条 ━━
        self._progress_frame = tk.Frame(outer, bg=_BG)
        self._progress_frame.pack(fill="x", pady=(4, 12))

        self._progress_bar = ttk.Progressbar(
            self._progress_frame,
            orient="horizontal",
            mode="indeterminate",
            style="Custom.Horizontal.TProgressbar",
        )
        self._progress_bar.pack(fill="x", ipady=2)

        # ━━ 详情区 ━━
        detail_card = tk.Frame(outer, bg=_SURFACE0, padx=16, pady=14,
                                highlightbackground=_SURFACE0,
                                highlightthickness=1, bd=0)
        detail_card.pack(fill="both", expand=True)

        tk.Label(
            detail_card, text="当前状态",
            bg=_SURFACE0, fg=_SUBTEXT,
            font=("Segoe UI Semibold", 10),
            anchor="w",
        ).pack(fill="x", pady=(0, 6))

        self._detail_label = tk.Label(
            detail_card, text="准备开始...",
            bg=_SURFACE0, fg=_YELLOW,
            font=("Microsoft YaHei UI", 11),
            anchor="w", wraplength=650, justify="left",
        )
        self._detail_label.pack(fill="both", expand=True)

    # ── 消息循环 ──────────────────────────────────────────

    def _poll_queue(self):
        """处理队列中的消息"""
        try:
            while True:
                msg = self._queue.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        if self._root and self._root.winfo_exists():
            self._root.after(80, self._poll_queue)

    def _update_elapsed(self):
        """更新计时器"""
        if self._root and self._root.winfo_exists():
            elapsed = time.time() - self._start_time
            m, s = divmod(int(elapsed), 60)
            self._elapsed_label.config(text=f"{m:02d}:{s:02d}")
            self._root.after(1000, self._update_elapsed)

    def _set_stage_active(self, idx: int):
        """将阶段卡片设为活跃状态"""
        card = self._stage_frames[idx]
        card.config(bg=_SURFACE1, highlightbackground=_BLUE)
        self._stage_icons[idx].config(text="●", fg=_BLUE, bg=_SURFACE1)
        for lbl in self._stage_labels[idx]:
            lbl.config(fg=_TEXT, bg=_SURFACE1)

    def _set_stage_done(self, idx: int):
        """将阶段卡片设为完成状态"""
        card = self._stage_frames[idx]
        card.config(bg=_SURFACE0, highlightbackground=_SURFACE0)
        self._stage_icons[idx].config(text="✓", fg=_GREEN, bg=_SURFACE0)
        title_lbl, desc_lbl = self._stage_labels[idx]
        title_lbl.config(fg=_TEXT, bg=_SURFACE0)
        desc_lbl.config(fg=_SUBTEXT, bg=_SURFACE0)

    def _handle(self, msg):
        """处理一条消息"""
        cmd = msg[0]

        if cmd == "update":
            _, stage, title, detail = msg
            idx = stage - 1

            # 确保进度条在跑
            if not self._progress_bar.winfo_ismapped():
                self._progress_bar.start(15)

            if 0 <= idx < len(self._stage_frames):
                # 之前的阶段标记完成
                for i in range(idx):
                    icon_text = self._stage_icons[i].cget("text")
                    if icon_text not in ("✓", "✗"):
                        self._set_stage_done(i)

                # 当前阶段激活
                self._set_stage_active(idx)

            if detail:
                self._detail_label.config(text=detail, fg=_YELLOW)

        elif cmd == "complete":
            _, stage, title, elapsed = msg
            idx = stage - 1

            if 0 <= idx < len(self._stage_frames):
                self._set_stage_done(idx)
                m, s = divmod(int(elapsed), 60)
                if m:
                    self._stage_timers[idx].config(
                        text=f"{m}m {s}s", fg=_GREEN, bg=_SURFACE0
                    )
                else:
                    self._stage_timers[idx].config(
                        text=f"{s}s", fg=_GREEN, bg=_SURFACE0
                    )

            self._detail_label.config(
                text=f"{title} 完成" + (f" ({m}m {s}s)" if m else f" ({s}s)"),
                fg=_GREEN,
            )

        elif cmd == "finish":
            _, epub_path, total_elapsed = msg

            # 停止进度条
            self._progress_bar.stop()

            # 标记所有完成
            for i in range(len(self._stage_frames)):
                self._set_stage_done(i)

            m, s = divmod(int(total_elapsed), 60)
            time_str = f"{m}m {s}s" if m else f"{s}s"

            self._detail_label.config(
                text=f"✅ 转换完成!\n\n📕  {epub_path}\n⏱   总耗时 {time_str}",
                fg=_GREEN,
            )
            self._elapsed_label.config(fg=_GREEN)

            # 取消置顶
            try:
                self._root.attributes("-topmost", False)
            except Exception:
                pass

        elif cmd == "close":
            if self._root and self._root.winfo_exists():
                self._progress_bar.stop()
                self._root.destroy()
