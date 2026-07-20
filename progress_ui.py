"""
进度窗口 — 羊皮纸 · 鎏金 · 书籍质感设计。

用法:
    from progress_ui import ProgressWindow

    pw = ProgressWindow("民法总论", engine="popo")
    pw.start()
    pw.update_stage(1, "MinerU", "片 1/3: 第 1-200 页...", fraction=0.33)
    pw.complete_stage(1, "MinerU", elapsed=154.0)
    pw.finish(epub_path, total_elapsed)

设计说明：
- 羊皮纸底色 + 双线鎏金边框，楷体书名，壹/贰/叁阶段序号
- 进度条为 determinate 模式：阶段跨度 0-40 / 40-95 / 95-100，
  由 fraction 驱动，无 fraction 时缓慢爬行（绝不静止、绝不倒退）
- 计时：顶部总时钟（完成后停走），每阶段卡片实时走时（完成后定格）
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

# ── 配色：羊皮纸 + 鎏金 ─────────────────────────────────────
_PARCH       = "#F4EDDB"   # 羊皮纸主背景
_PARCH_DEEP  = "#EDE3CB"   # 详情区底色
_CARD        = "#FBF6E9"   # 卡片
_LINE        = "#DCCDA8"   # 细线
_LINE_GOLD   = "#B49B5E"   # 外框鎏金细线
_INK         = "#3B3226"   # 墨色（主文本）
_SUBINK      = "#8B7C61"   # 次文本
_FAINT       = "#BCAD8C"   # 等待状态
_GOLD        = "#A9853D"   # 古金（完成/装饰）
_GOLD_HI     = "#C9A227"   # 亮金（进行中）
_TROUGH      = "#E6DAB9"   # 进度条槽

# ── 字体 ────────────────────────────────────────────────────
_F_LATIN   = "Times New Roman"     # 拉丁/数字（新罗马，经典书籍衬线）
_F_KAI     = "KaiTi"               # 楷体（书名、序号）
_F_BODY    = "FangSong"            # 仿宋（正文/详情，书卷气且易读）
_F_SYMBOL  = "Segoe UI Symbol"     # 装饰符号

# 字号基准（窗口默认宽度 860px 下的大小，缩放时按比例自适应）
_BASE_W = 860

# 阶段进度跨度（百分比）
_STAGE_SPAN = {1: (0.0, 40.0), 2: (40.0, 95.0), 3: (95.0, 100.0)}
_STAGE_NUM = ("壹", "贰", "叁", "肆")


def _fmt_elapsed(seconds: float) -> str:
    """耗时格式化：<1h → '2m31s' / '12s'；>=1h → '1h02m'"""
    s = int(seconds)
    if s >= 3600:
        h, rem = divmod(s, 3600)
        return f"{h}h{rem // 60:02d}m"
    m, s = divmod(s, 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


class ProgressWindow:
    """线程安全的 tkinter 进度窗口（羊皮纸鎏金版）"""

    def __init__(self, book_name: str, engine: str = "popo",
                 stage_estimates: list | None = None,
                 translate: bool = False):
        self._queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._root = None
        self._book_name = book_name
        self._engine = engine
        self._translate = translate
        self._start_time = time.time()
        self._thread: threading.Thread | None = None

        # 阶段进度跨度：有预估耗时时按预估占比计算，否则用默认
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
            self._stage_span = dict(_STAGE_SPAN)

        # GUI 组件引用
        self._stage_cards: list[dict] = []   # 每阶段卡片的组件集合
        self._detail_label: tk.Label | None = None
        self._elapsed_label: tk.Label | None = None
        self._bar_canvas: tk.Canvas | None = None
        self._bar_pct_label: tk.Label | None = None

        # 进度/计时状态
        self._bar_target = 0.0      # 目标百分比
        self._bar_value = 0.0       # 当前显示百分比
        self._active_stage = 0      # 0=无, 1..3
        self._stage_start: dict[int, float] = {}
        self._stage_done_elapsed: dict[int, float] = {}
        self._finished = False

        # 字体注册表（缩放窗口时整体自适应；Garamond 字面小，拉丁类加大 1pt）
        self._font_defs = {
            "caps":     (_F_LATIN, 13, "bold"),
            "title":    (_F_KAI, 25, "bold"),
            "clock":    (_F_LATIN, 16, "normal"),
            "num":      (_F_KAI, 17, "bold"),
            "name":     (_F_BODY, 15, "bold"),
            "desc":     (_F_BODY, 12, "normal"),
            "timer":    (_F_LATIN, 14, "normal"),
            "pct":      (_F_LATIN, 14, "normal"),
            "detail_h": (_F_BODY, 12, "bold"),
            "detail":   (_F_BODY, 14, "normal"),
        }
        self._fonts: dict[str, tkfont.Font] = {}
        self._font_factor = 1.0

    # ── 公共 API（任意线程调用） ─────────────────────────────

    def start(self):
        """在后台线程中启动窗口"""
        self._thread = threading.Thread(target=self._run_gui, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def update_stage(self, stage: int, title: str, detail: str = "",
                     fraction: float | None = None):
        """更新当前阶段、详情与阶段内进度（fraction 0..1，可省略）"""
        self._queue.put(("update", stage, title, detail, fraction))

    def complete_stage(self, stage: int, title: str, elapsed: float):
        """标记阶段完成"""
        self._queue.put(("complete", stage, title, elapsed))

    def finish(self, epub_path: str, total_elapsed: float):
        """显示完成摘要"""
        self._queue.put(("finish", epub_path, total_elapsed))

    def close(self):
        """关闭窗口"""
        self._queue.put(("close",))

    # ── GUI 构建 ─────────────────────────────────────────────

    def _run_gui(self):
        root = tk.Tk()
        self._root = root
        root.title(f"Books Converter — {self._book_name}")
        root.configure(bg=_PARCH)

        # 高 DPI 支持
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        # 可调整大小 + 最小尺寸（修复: 原来锁死不可调）
        root.resizable(True, True)
        root.minsize(780, 700)
        w, h = 860, 760
        sx, sy = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{w}x{h}+{(sx - w) // 2}+{max((sy - h) // 3, 40)}")
        root.attributes("-topmost", True)

        # 共享字体（改字号即整体生效）
        self._fonts = {
            k: tkfont.Font(family=fam, size=size,
                           weight="bold" if weight == "bold" else "normal")
            for k, (fam, size, weight) in self._font_defs.items()
        }

        self._build_ui()
        self._ready.set()

        self._poll_queue()
        self._tick()
        root.mainloop()

    def _build_ui(self):
        root = self._root

        # 双线鎏金边框（书籍封面式）
        outer_line = tk.Frame(root, bg=_PARCH, highlightbackground=_LINE_GOLD,
                              highlightthickness=1, bd=0)
        outer_line.pack(fill="both", expand=True, padx=6, pady=6)
        inner_line = tk.Frame(outer_line, bg=_PARCH, highlightbackground=_LINE,
                              highlightthickness=1, bd=0)
        inner_line.pack(fill="both", expand=True, padx=4, pady=4)

        outer = tk.Frame(inner_line, bg=_PARCH)
        outer.pack(fill="both", expand=True, padx=26, pady=20)

        # ━━ 眉题： spaced caps 鎏金 ━━
        tk.Label(
            outer, text="✦  B O O K S   C O N V E R T E R  ✦",
            bg=_PARCH, fg=_GOLD,
            font=self._fonts["caps"], anchor="center",
        ).pack(fill="x")

        # ━━ 书名（楷体大字） ━━
        tk.Label(
            outer, text=self._book_name,
            bg=_PARCH, fg=_INK,
            font=self._fonts["title"], anchor="center",
        ).pack(fill="x", pady=(6, 0))

        # ━━ 总计时 ━━
        self._elapsed_label = tk.Label(
            outer, text="00:00",
            bg=_PARCH, fg=_SUBINK,
            font=self._fonts["clock"], anchor="center",
        )
        self._elapsed_label.pack(fill="x", pady=(2, 6))

        # ━━ 鎏金分隔线 + 中央菱形 ━━
        self._draw_divider(outer)

        # ━━ 阶段卡片（有翻译任务时插入第 叁 张） ━━
        if self._engine == "popo":
            stage2 = ("Popo 4B", "结构重建 · 本地 GPU")
        elif self._engine == "hybrid":
            stage2 = ("Hybrid", "结构重建 · 云端 LLM")
        else:
            stage2 = ("DeepSeek V4 Flash", "结构分析 · 云端 LLM")
        stages = [
            ("MinerU", "PDF 解析 · OCR · 图片提取"),
            stage2,
        ]
        if self._translate:
            stages.append(("翻译", "文学翻译 · 云端 LLM"))
        stages.append(("EPUB 生成", "嵌套目录 · 图片 · 排版"))

        for i, (name, desc) in enumerate(stages):
            card = tk.Frame(outer, bg=_CARD,
                            highlightbackground=_LINE,
                            highlightthickness=1, bd=0)
            card.pack(fill="x", pady=4)

            # 左侧鎏金指示条（进行中才点亮）
            accent = tk.Frame(card, bg=_CARD, width=4)
            accent.pack(side="left", fill="y")
            accent.pack_propagate(False)

            body = tk.Frame(card, bg=_CARD)
            body.pack(side="left", fill="x", expand=True,
                      padx=(12, 10), pady=11)

            num = tk.Label(body, text=_STAGE_NUM[i], bg=_CARD, fg=_FAINT,
                           font=self._fonts["num"], width=2)
            num.pack(side="left")

            info = tk.Frame(body, bg=_CARD)
            info.pack(side="left", fill="x", expand=True)
            name_lbl = tk.Label(info, text=name, bg=_CARD, fg=_FAINT,
                                font=self._fonts["name"], anchor="w")
            name_lbl.pack(fill="x")
            desc_lbl = tk.Label(info, text=desc, bg=_CARD, fg=_FAINT,
                                font=self._fonts["desc"], anchor="w")
            desc_lbl.pack(fill="x")

            timer = tk.Label(body, text="", bg=_CARD, fg=_FAINT,
                             font=self._fonts["timer"], anchor="e", width=8)
            timer.pack(side="right")

            self._stage_cards.append({
                "card": card, "accent": accent, "num": num,
                "name": name_lbl, "desc": desc_lbl, "timer": timer,
            })

        # ━━ 进度条（自绘鎏金长条） ━━
        bar_row = tk.Frame(outer, bg=_PARCH)
        bar_row.pack(fill="x", pady=(10, 8))

        self._bar_canvas = tk.Canvas(
            bar_row, height=10, bg=_PARCH, highlightthickness=0, bd=0,
        )
        self._bar_canvas.pack(side="left", fill="x", expand=True)
        self._bar_canvas.bind("<Configure>", lambda e: self._redraw_bar())

        self._bar_pct_label = tk.Label(
            bar_row, text="0%", bg=_PARCH, fg=_SUBINK,
            font=self._fonts["pct"], anchor="e", width=6,
        )
        self._bar_pct_label.pack(side="right", padx=(8, 0))

        # ━━ 详情区 ━━
        detail_card = tk.Frame(outer, bg=_PARCH_DEEP,
                               highlightbackground=_LINE,
                               highlightthickness=1, bd=0)
        detail_card.pack(fill="both", expand=True, pady=(4, 0))

        tk.Label(
            detail_card, text="当 前 状 态",
            bg=_PARCH_DEEP, fg=_GOLD,
            font=self._fonts["detail_h"], anchor="w",
        ).pack(fill="x", padx=14, pady=(10, 2))

        self._detail_label = tk.Label(
            detail_card, text="研墨备纸，即将开卷…",
            bg=_PARCH_DEEP, fg=_INK,
            font=self._fonts["detail"],
            anchor="nw", justify="left", wraplength=720,
        )
        self._detail_label.pack(fill="both", expand=True, padx=14, pady=(0, 12))

        # 窗口拉伸时：字体整体缩放 + 详情换行宽度跟随
        root.bind("<Configure>", self._on_resize)

    def _draw_divider(self, parent):
        """鎏金分隔线：细线 + 中央菱形饰件"""
        cv = tk.Canvas(parent, height=18, bg=_PARCH, highlightthickness=0, bd=0)
        cv.pack(fill="x", pady=(2, 10))

        def draw(_evt=None):
            try:
                cv.delete("all")
                w = cv.winfo_width()
                cy = 9
                cv.create_line(30, cy, w // 2 - 14, cy, fill=_GOLD, width=1)
                cv.create_line(w // 2 + 14, cy, w - 30, cy, fill=_GOLD, width=1)
                cv.create_polygon(w // 2, cy - 5, w // 2 + 5, cy,
                                  w // 2, cy + 5, w // 2 - 5, cy,
                                  fill=_GOLD, outline="")
            except tk.TclError:
                pass
        cv.bind("<Configure>", draw)

    # ── 消息循环 ─────────────────────────────────────────────

    def _alive(self) -> bool:
        """窗口是否存活（销毁竞态保护）"""
        try:
            return bool(self._root and self._root.winfo_exists())
        except tk.TclError:
            return False

    def _poll_queue(self):
        try:
            while True:
                self._handle(self._queue.get_nowait())
        except queue.Empty:
            pass
        if self._alive():
            self._root.after(80, self._poll_queue)

    def _tick(self):
        """1s 节拍：总时钟 + 活跃阶段走时 + 进度条缓动/爬行"""
        if not self._alive():
            return

        now = time.time()
        # 总时钟（完成后停走——修复: 原来完成後仍在走）
        if not self._finished:
            elapsed = now - self._start_time
            m, s = divmod(int(elapsed), 60)
            h, m = divmod(m, 60)
            text = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
            self._elapsed_label.config(text=text)

        # 活跃阶段实时走时
        if self._active_stage and self._active_stage not in self._stage_done_elapsed:
            start = self._stage_start.get(self._active_stage)
            if start:
                t = self._stage_cards[self._active_stage - 1]["timer"]
                t.config(text=_fmt_elapsed(now - start))

        # 无 fraction 输入时缓慢爬行（修复: 进度条静止）
        if not self._finished and self._active_stage:
            base, ceiling = self._stage_span.get(self._active_stage, (0, 100))
            creep_cap = base + (ceiling - base) * 0.9
            if self._bar_target < creep_cap:
                self._bar_target = min(self._bar_target + 0.15, creep_cap)

        # 缓动逼近目标
        if self._bar_value < self._bar_target:
            self._bar_value = min(self._bar_value
                                  + max((self._bar_target - self._bar_value) * 0.25, 0.2),
                                  self._bar_target)
            self._redraw_bar()

        self._root.after(200, self._tick)

    def _redraw_bar(self):
        cv = self._bar_canvas
        if not cv:
            return
        try:
            cv.delete("all")
            w, h = cv.winfo_width(), 10
            cy = h / 2
            # 槽
            cv.create_line(2, cy, w - 2, cy, fill=_TROUGH, width=5,
                           capstyle="round")
            # 鎏金填充
            fill_w = max((w - 4) * self._bar_value / 100.0, 0)
            if fill_w > 1:
                cv.create_line(2, cy, 2 + fill_w, cy, fill=_GOLD_HI, width=5,
                               capstyle="round")
            self._bar_pct_label.config(text=f"{self._bar_value:.0f}%")
        except tk.TclError:
            pass

    def _on_resize(self, evt):
        # 只响应根窗口（子组件的 Configure 也会触发此绑定）
        if evt.widget is not self._root:
            return
        w = self._root.winfo_width()
        # 字体整体自适应：以 _BASE_W 为基准宽度，限幅 0.85~1.6 倍
        factor = max(0.85, min(w / _BASE_W, 1.6))
        if abs(factor - self._font_factor) >= 0.05:
            self._font_factor = factor
            for k, f in self._fonts.items():
                f.configure(size=round(self._font_defs[k][1] * factor))
        if self._detail_label:
            self._detail_label.config(wraplength=max(w - 140, 300))

    # ── 阶段状态切换 ─────────────────────────────────────────

    def _set_stage_active(self, idx: int):
        c = self._stage_cards[idx]
        c["accent"].config(bg=_GOLD_HI)
        c["num"].config(fg=_GOLD_HI)
        c["name"].config(fg=_INK)
        c["desc"].config(fg=_SUBINK)
        c["timer"].config(fg=_GOLD_HI)

    def _set_stage_done(self, idx: int):
        c = self._stage_cards[idx]
        c["accent"].config(bg=_GOLD)
        c["num"].config(text="✓", fg=_GOLD)
        c["name"].config(fg=_INK)
        c["desc"].config(fg=_SUBINK)
        c["timer"].config(fg=_SUBINK)

    # ── 消息处理 ─────────────────────────────────────────────

    def _handle(self, msg):
        cmd = msg[0]

        if cmd == "update":
            _, stage, title, detail, fraction = msg
            idx = stage - 1

            if 0 <= idx < len(self._stage_cards):
                # 首次激活某阶段时记录开始时间
                if stage not in self._stage_start:
                    self._stage_start[stage] = time.time()
                self._active_stage = stage

                # 之前的阶段补记完成
                for i in range(idx):
                    if self._stage_cards[i]["num"].cget("text") != "✓":
                        self._set_stage_done(i)
                        if (i + 1) not in self._stage_done_elapsed:
                            start = self._stage_start.get(i + 1)
                            self._stage_done_elapsed[i + 1] = (
                                time.time() - start if start else 0
                            )
                self._set_stage_active(idx)

                # fraction → 目标百分比（单调不减）
                if fraction is not None:
                    base, ceiling = self._stage_span.get(stage, (0, 100))
                    target = base + max(0.0, min(fraction, 1.0)) * (ceiling - base)
                    self._bar_target = max(self._bar_target, target)

            if detail:
                self._detail_label.config(text=detail, fg=_INK)

        elif cmd == "complete":
            _, stage, title, elapsed = msg
            idx = stage - 1

            if 0 <= idx < len(self._stage_cards):
                self._set_stage_done(idx)
                self._stage_done_elapsed[stage] = elapsed
                self._stage_cards[idx]["timer"].config(
                    text=_fmt_elapsed(elapsed)
                )
                # 完成即顶到阶段跨度上限
                self._bar_target = max(self._bar_target,
                                       self._stage_span.get(stage, (0, 100))[1])

            if self._active_stage == stage:
                self._active_stage = 0

            self._detail_label.config(
                text=f"{title} 告竣（{_fmt_elapsed(elapsed)}）",
                fg=_SUBINK,
            )

        elif cmd == "finish":
            _, epub_path, total_elapsed = msg
            self._finished = True
            self._bar_target = 100.0
            self._bar_value = 100.0
            self._redraw_bar()

            for i in range(len(self._stage_cards)):
                self._set_stage_done(i)

            self._detail_label.config(
                text=f"✦  全书告竣  ✦\n\n{epub_path}\n\n总耗时 {_fmt_elapsed(total_elapsed)}",
                fg=_GOLD,
            )
            self._elapsed_label.config(fg=_GOLD)

            try:
                self._root.attributes("-topmost", False)
            except Exception:
                pass

        elif cmd == "close":
            if self._alive():
                self._root.destroy()


# ── 演示/自检 ────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    pw = ProgressWindow("城市与国家财富：经济生活的基本原则", engine="popo")
    pw.start()

    def simulate():
        time.sleep(0.6)
        pw.update_stage(1, "MinerU", "片 1/3： 第 1-200 页，上传解析中…", fraction=0.3)
        time.sleep(1.2)
        pw.update_stage(1, "MinerU", "片 3/3： 第 401-549 页，解析完成", fraction=1.0)
        time.sleep(0.8)
        pw.complete_stage(1, "MinerU", 154.0)
        pw.update_stage(2, "Popo", "加载 4B 模型到 GPU…")
        time.sleep(1.0)
        for i in range(1, 13):
            pw.update_stage(2, "Popo",
                            f"标题层级 分块 {i}/12（第 {i * 30}-{i * 30 + 29} 页）",
                            fraction=i / 12)
            time.sleep(0.35)
        pw.complete_stage(2, "Popo", 601.0)
        pw.update_stage(3, "EPUB 生成", "渲染章节 HTML、构建嵌套目录…", fraction=0.6)
        time.sleep(0.8)
        pw.complete_stage(3, "EPUB 生成", 1.2)
        pw.finish(r"D:\My_Library\城市与国家财富\城市与国家财富.epub", 758.0)

    threading.Thread(target=simulate, daemon=True).start()

    if "--shot" in sys.argv:
        # 自检截图：7 秒后抓取窗口区域
        def grab():
            try:
                from PIL import ImageGrab
                x = pw._root.winfo_rootx()
                y = pw._root.winfo_rooty()
                w = pw._root.winfo_width()
                h = pw._root.winfo_height()
                ImageGrab.grab(bbox=(x, y, x + w, y + h)).save("_ui_shot.png")
            finally:
                pw.close()

        pw._root.after(7000, grab)

    while True:
        time.sleep(1)
