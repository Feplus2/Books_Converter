#!/usr/bin/env python3
"""
Books Converter GUI — 扫描书 PDF → EPUB（羊皮纸 · 鎏金）

面向非技术用户的图形前端：
- 拖放 PDF（可多本，自动去重）/ 点击选择文件
- 队列列表：书名、页数、状态、耗时，单行移除 / 清空
- 设置面板：MinerU Token、LLM 端点（OpenAI 协议，可在线获取模型列表）、
  强制 OCR、翻译目标语言、输出目录、界面语言（中/EN），持久化到 gui_settings.json
- 串行转换：stage1 MinerU → stage2 Hybrid（失败降级简单结构）→
  可选 stage4 翻译（失败只出原文版）→ stage3 EPUB
- 转换在 worker 线程执行，GUI 只通过 queue + after() 轮询更新

自检：python app.py --shot        中文界面截图 _app_shot.png
      python app.py --shot-en     English UI 截图 _app_shot_en.png
"""

import json
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

import fitz  # PyMuPDF，读取页数
from tkinterdnd2 import DND_FILES, TkinterDnD

import config  # 设置默认值来源（读 .env / 环境变量）
from progress_ui import (
    _PARCH, _PARCH_DEEP, _CARD, _LINE, _LINE_GOLD,
    _INK, _SUBINK, _FAINT, _GOLD, _GOLD_HI, _TROUGH,
    _F_LATIN, _F_KAI, _F_BODY, _F_SYMBOL,
    _STAGE_NUM, _fmt_elapsed,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("gui")

# PyInstaller 打包（frozen）时：设置文件放 exe 同目录（用户可写）；
# 源码运行时：放项目根目录
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent
SETTINGS_PATH = APP_DIR / "gui_settings.json"

_RED = "#A6453A"      # 失败状态
_ENTRY_BG = "#FFFDF6"  # 输入框底色（纸白）
_TRACK_OFF = "#D6C9AC"  # 开关关闭时的滑轨
_PAPER_FG = "#FDF9EE"  # 鎏金底上的纸白字

# 翻译目标语言（显示名即传给 translate_book(target_lang=...) 的语言名）
TRANSLATE_LANGS = ["简体中文", "English", "日本語", "Français", "Deutsch",
                   "Español", "한국어"]

# 解析引擎（设置下拉的显示名 ↔ ocr_provider 注册名）
ENGINE_OPTIONS = [("mineru", "MinerU"), ("paddleocr", "PaddleOCR-VL")]
_ENGINE_DISPLAY = dict(ENGINE_OPTIONS)
_ENGINE_BY_DISPLAY = {v: k for k, v in ENGINE_OPTIONS}

# ════════════════════════════════════════════════════════════
# 界面文案（zh / en 键必须一一对应）
# ════════════════════════════════════════════════════════════

STRINGS = {
    "zh": {
        "window_title": "Books Converter — 扫描书转 EPUB",
        "title": "扫 描 书 · 一 键 成 册",
        "subtitle": "S C A N N E D   B O O K   →   E P U B   ·   T R A N S L A T I O N",
        "drop_main": "✦  把 PDF 拖进来（可多本，自动去重）",
        "drop_sub": "或  点 击 选 择 文 件",
        "drop_hot": "松 手 即 收 入 队 列",
        "sec_queue": "转 换 队 列",
        "clear": "清 空",
        "queue_empty": "（队列为空）",
        "sec_settings": "⚙  设 置",
        "sec_progress": "当 前 进 度",
        "status_header": "当 前 状 态",
        "lbl_mineru": "MinerU Token",
        "lbl_engine": "解析引擎",
        "lbl_paddle": "PaddleOCR Token",
        "lbl_base": "LLM Base URL",
        "lbl_key": "LLM API Key",
        "lbl_model": "LLM Model",
        "btn_fetch": "获取模型列表",
        "fetching": "正在获取模型列表…",
        "models_ok": "✓ 已获取 {n} 个模型",
        "models_need_base": "请先填写 LLM Base URL。",
        "models_fail_auth": "获取模型列表失败：API Key 无效（401），请检查 LLM API Key 与 Base URL。",
        "models_fail_net": "获取模型列表失败：{err}",
        "lbl_ui_lang": "界面语言 / Language",
        "lbl_translate": "翻 译",
        "chk_translate": "翻译为",
        "chk_ocr": "强制 OCR（扫描书必开；文字版 PDF 关掉更快）",
        "ocr_hint": "扫描本 = 图片型 PDF，必须 OCR；出版社出的文字版 PDF 可关闭提速。",
        "lbl_outdir": "输出目录",
        "outdir_hint": "（留空 = 输出到 PDF 同目录）",
        "browse": "浏 览…",
        "note_mineru": "· MinerU Token 免费获取：",
        "note_paddle": "· PaddleOCR Token 免费申请：",
        "note_llm": "· LLM 配置兼容任何 OpenAI 协议端点",
        "btn_update": "检查更新",
        "update_checking": "正在检查更新…",
        "update_title": "检查更新",
        "update_latest": "已是最新版本（v{ver}）。",
        "update_new_t": "发现新版本",
        "update_new": "发现新版本 v{latest}（当前 v{ver}）。\n\n前往下载页面？",
        "update_fail": "检查更新失败：{err}",
        "save": "保存设置",
        "saved": "✓ 已存",
        "waiting": "等待中",
        "pages": "{n} 页",
        "pages_unknown": "? 页",
        "s1_name": "PDF 解析",
        "s1_desc": "MinerU / PaddleOCR · 图片提取",
        "s2_name": "Hybrid",
        "s2_desc": "结构重建 · 云端 LLM",
        "s3_name": "翻译",
        "s3_desc": "文学翻译 · 云端 LLM",
        "s4_name": "EPUB 生成",
        "s4_desc": "嵌套目录 · 图片 · 排版",
        "st_s1": "PDF 解析中",
        "st_s2": "结构分析中",
        "st_s3": "翻译中",
        "st_s4": "EPUB 生成中",
        "done": "✓ 完成",
        "done_degraded": "（粗排目录）",
        "cancelled": "已取消",
        "added": "已收录 {n} 卷新书，队列共 {total} 卷。",
        "no_new": "未发现可收录的 PDF（重复或非 PDF 已略过）。",
        "stop_req": "已请求停止 — 当前步骤结束后收笔。",
        "stopped": "已停止。余下的书仍候在队列中。",
        "all_done": "✦  全部告竣，共成书 {n} 卷  ✦",
        "hint_done": "成书 {n} 卷",
        "hint_stopped": "已停止",
        "hint_error": "出错",
        "copied": "路径已誊入剪贴板。",
        "open_fail": "打开文件夹失败：{err}",
        "copy": "复 制",
        "open_folder": "打开所在文件夹",
        "msg_no_books_t": "书架空空",
        "msg_no_books": "请先拖入或选择要转换的 PDF。",
        "msg_no_token_t": "缺少 MinerU Token",
        "msg_no_token": "MinerU Token 为空，无法解析 PDF。\n\n"
                        "请展开「设置」，填入 Token（可从\n"
                        "https://mineru.net/apiManage/token 免费获取）。",
        "msg_no_ptoken_t": "缺少 PaddleOCR Token",
        "msg_no_ptoken": "已选择 PaddleOCR 引擎，但 Token 为空，无法解析 PDF。\n\n"
                         "请展开「设置」填入 PaddleOCR Token（免费申请，\n"
                         "链接见设置页底部），或把解析引擎改回 MinerU。",
        "msg_no_key_t": "LLM API Key 为空",
        "msg_no_key": "LLM API Key 为空：结构分析将降级为按字号的简单目录"
                      "（仍可成书，但目录较粗）。\n\n继续转换？",
        "msg_quit_t": "转换进行中",
        "msg_quit": "仍有书卷在炉上，确定熄火退出？",
        "stage_done": "{title} 告竣（{elapsed}）",
        "book_open": "开卷：《{name}》",
        "fallback_note": "结构分析未竟，以字号粗排章节续行…",
        "translate_fail_note": "翻译未竟，仍奉上原文版。",
        "book_done_detail": "✦ 《{name}》告竣  ✦\n{path}",
        "fail_mineru": "PDF 解析失败：{err}",
        "fail_epub": "EPUB 生成失败：{err}",
        "fatal_load": "加载转换模块失败：{err}",
        "fatal_run": "转换流程异常：{err}",
        "dlg_choose_pdf": "选择 PDF 文件",
        "dlg_outdir": "选择输出目录",
        "idle_detail": "研墨备纸，静候书卷…",
        "start": "开 始 转 换",
        "stop": "停 止",
        "demo_detail": "片 2/3：第 201-400 页，云端解析中…",
    },
    "en": {
        "window_title": "Books Converter — Scanned PDF to EPUB",
        "title": "From Scan to EPUB",
        "subtitle": "S C A N N E D   B O O K   →   E P U B   ·   T R A N S L A T I O N",
        "drop_main": "✦  Drop PDFs here (multiple allowed, auto-dedup)",
        "drop_sub": "or click to choose files",
        "drop_hot": "R E L E A S E   T O   A D D",
        "sec_queue": "QUEUE",
        "clear": "Clear",
        "queue_empty": "(queue is empty)",
        "sec_settings": "⚙  SETTINGS",
        "sec_progress": "PROGRESS",
        "status_header": "STATUS",
        "lbl_mineru": "MinerU Token",
        "lbl_engine": "OCR engine",
        "lbl_paddle": "PaddleOCR Token",
        "lbl_base": "LLM Base URL",
        "lbl_key": "LLM API Key",
        "lbl_model": "LLM Model",
        "btn_fetch": "Fetch models",
        "fetching": "Fetching model list…",
        "models_ok": "✓ {n} models loaded",
        "models_need_base": "Please fill in the LLM Base URL first.",
        "models_fail_auth": "Failed to fetch models: invalid API key (401). "
                            "Check LLM API Key / Base URL.",
        "models_fail_net": "Failed to fetch models: {err}",
        "lbl_ui_lang": "Language / 界面语言",
        "lbl_translate": "Translate",
        "chk_translate": "into",
        "chk_ocr": "Force OCR (required for scans; turn off for text PDFs)",
        "ocr_hint": "Scanned (image-only) PDFs need OCR; digital text PDFs can skip it for speed.",
        "lbl_outdir": "Output folder",
        "outdir_hint": "(empty = same folder as the PDF)",
        "browse": "Browse…",
        "note_mineru": "· Free MinerU token: ",
        "note_paddle": "· Free PaddleOCR token: ",
        "note_llm": "· LLM settings accept any OpenAI-compatible endpoint",
        "btn_update": "Check updates",
        "update_checking": "Checking for updates…",
        "update_title": "Check updates",
        "update_latest": "You're on the latest version (v{ver}).",
        "update_new_t": "Update available",
        "update_new": "Version v{latest} is available (current v{ver}).\n\nOpen the download page?",
        "update_fail": "Update check failed: {err}",
        "save": "Save settings",
        "saved": "✓ Saved",
        "waiting": "Waiting",
        "pages": "{n} p.",
        "pages_unknown": "? p.",
        "s1_name": "Parse",
        "s1_desc": "MinerU / PaddleOCR · images",
        "s2_name": "Hybrid",
        "s2_desc": "Structure rebuild · Cloud LLM",
        "s3_name": "Translate",
        "s3_desc": "Literary translation · Cloud LLM",
        "s4_name": "EPUB",
        "s4_desc": "Nested TOC · images · layout",
        "st_s1": "Parsing PDF…",
        "st_s2": "Analyzing structure…",
        "st_s3": "Translating…",
        "st_s4": "Building EPUB…",
        "done": "✓ Done",
        "done_degraded": " (simple TOC)",
        "cancelled": "Cancelled",
        "added": "Added {n} book(s); {total} in queue.",
        "no_new": "No new PDFs added (duplicates / non-PDFs skipped).",
        "stop_req": "Stop requested — finishing the current step…",
        "stopped": "Stopped. Remaining books stay in queue.",
        "all_done": "✦  All done — {n} book(s) finished  ✦",
        "hint_done": "{n} done",
        "hint_stopped": "Stopped",
        "hint_error": "Error",
        "copied": "Path copied to clipboard.",
        "open_fail": "Failed to open folder: {err}",
        "copy": "Copy",
        "open_folder": "Open folder",
        "msg_no_books_t": "Queue empty",
        "msg_no_books": "Drop or choose some PDFs first.",
        "msg_no_token_t": "MinerU token missing",
        "msg_no_token": "MinerU Token is empty — PDF parsing is impossible.\n\n"
                        "Open Settings and paste your token\n"
                        "(free at https://mineru.net/apiManage/token).",
        "msg_no_ptoken_t": "PaddleOCR token missing",
        "msg_no_ptoken": "PaddleOCR is selected but its token is empty.\n\n"
                         "Open Settings and paste your PaddleOCR token (free,\n"
                         "link at the bottom of Settings), or switch back to MinerU.",
        "msg_no_key_t": "LLM API key empty",
        "msg_no_key": "Without an LLM API key, structure analysis falls back to a "
                      "rough font-size outline (a book is still produced).\n\n"
                      "Continue anyway?",
        "msg_quit_t": "Conversion running",
        "msg_quit": "Books are still being converted. Quit anyway?",
        "stage_done": "{title} finished ({elapsed})",
        "book_open": "Now processing: {name}",
        "fallback_note": "Structure analysis failed; continuing with a font-size outline…",
        "translate_fail_note": "Translation failed; publishing the original text.",
        "book_done_detail": "✦ {name} finished ✦\n{path}",
        "fail_mineru": "PDF parsing failed: {err}",
        "fail_epub": "EPUB failed: {err}",
        "fatal_load": "Failed to load converter modules: {err}",
        "fatal_run": "Conversion error: {err}",
        "dlg_choose_pdf": "Choose PDF files",
        "dlg_outdir": "Choose output folder",
        "idle_detail": "Ready and waiting…",
        "start": "START",
        "stop": "STOP",
        "demo_detail": "Chunk 2/3: pages 201-400, parsing in cloud…",
    },
}


# ════════════════════════════════════════════════════════════
# 设置：默认值 / 持久化 / 注入
# ════════════════════════════════════════════════════════════

def _default_settings() -> dict:
    # frozen（PyInstaller 打包）时：不读取打包者机器的环境变量，
    # 避免把构建机的密钥预填给最终用户（泄露事故）。
    if getattr(sys, "frozen", False):
        mineru_token, llm_key, paddle_token = "", "", ""
    else:
        mineru_token = config.MINERU_TOKEN or ""
        llm_key = config.DEEPSEEK_API_KEY or ""
        paddle_token = config.PADDLEOCR_TOKEN or ""
    return {
        "mineru_token": mineru_token,
        "ocr_provider": config.OCR_PROVIDER or "mineru",
        "paddleocr_token": paddle_token,
        "llm_base_url": config.DEEPSEEK_BASE_URL or "https://api.deepseek.com",
        "llm_key": llm_key,
        "llm_model": config.DEEPSEEK_MODEL or "deepseek-v4-flash",
        "ocr": True,
        "translate": False,
        "translate_lang": "简体中文",
        "ui_lang": "zh",
        "output_dir": "",          # 空 = PDF 同目录
    }


def _load_settings() -> dict:
    s = _default_settings()
    try:
        if SETTINGS_PATH.exists():
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k in s:
                if k in saved:
                    s[k] = saved[k]
    except Exception as e:
        logger.warning(f"读取 gui_settings.json 失败，使用默认值: {e}")
    if s.get("translate_lang") not in TRANSLATE_LANGS:
        s["translate_lang"] = "简体中文"
    return s


def _save_settings(s: dict) -> None:
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存 gui_settings.json 失败: {e}")


def _apply_settings(s: dict) -> None:
    """把 GUI 设置写入 os.environ，并同步给已加载的 stage 模块打补丁。

    stage 模块在 import 时通过 `from config import X` 绑定了配置值，
    因此只改 os.environ 对已经 import 过的模块无效——必须逐个 setattr。
    尚未加载的模块直接跳过：它们之后 import 时会从 config 读取，
    而 config 的属性已在此处先行更新（config 本身也由 os.environ 驱动）。
    """
    env_map = {
        "MINERU_TOKEN": s["mineru_token"].strip(),
        "OCR_PROVIDER": s.get("ocr_provider", "mineru"),
        "PADDLEOCR_TOKEN": s.get("paddleocr_token", "").strip(),
        "DEEPSEEK_API_KEY": s["llm_key"].strip(),
        "DEEPSEEK_BASE_URL": s["llm_base_url"].strip() or "https://api.deepseek.com",
        "DEEPSEEK_MODEL": s["llm_model"].strip() or "deepseek-v4-flash",
    }
    for key, value in env_map.items():
        os.environ[key] = value
        setattr(config, key, value)

    patch_map = {
        "stage1_mineru": ("MINERU_TOKEN",),
        "stage2_hybrid": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"),
        "stage2_common": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"),
        "stage4_translate": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"),
    }
    for mod_name, attrs in patch_map.items():
        mod = sys.modules.get(mod_name)
        if mod is None:                      # 未加载：os.environ + config 已足够
            continue
        for attr in attrs:
            if hasattr(mod, attr):
                setattr(mod, attr, os.environ[attr])


def _save_stage1_metadata(work_dir: str, engine: str, info: dict) -> None:
    """保存 Stage 1 元数据到 <work_dir>/<engine>/metadata.json（与 pipeline.py 一致）"""
    meta = {
        "provider": engine,
        "markdown_length": len(info.get("markdown", "")),
        "content_blocks": len(info.get("content_list", [])),
        "images_dir": info.get("images_dir"),
    }
    meta_path = Path(work_dir) / engine / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


# ════════════════════════════════════════════════════════════
# 降级结构（与 pipeline.py 的 _fallback_structure 一致）
# ════════════════════════════════════════════════════════════
def _fallback_structure(mineru_info: dict, book_name: str) -> dict:
    """当 DeepSeek 不可用时，基于 MinerU text_level 生成基础结构"""
    content_list = mineru_info.get("content_list", [])
    chapters = []
    current_chapter = None

    for block in content_list:
        level = block.get("text_level", 0)
        page = block.get("page_idx", 0) + 1

        if level >= 1:
            if current_chapter:
                chapters.append(current_chapter)
            current_chapter = {
                "type": "chapter",
                "title": block.get("text", f"章节 {len(chapters)+1}"),
                "level": level,
                "page_start": page,
                "page_end": page,
            }
        elif current_chapter:
            current_chapter["page_end"] = page

    if current_chapter:
        chapters.append(current_chapter)

    logger.info(f"  降级结构: 基于字号检测到 {len(chapters)} 个章节")
    return {
        "metadata": {"title": book_name, "authors": [], "translator": None, "publisher": None, "language": "zh"},
        "front_matter": [],
        "body": chapters,
        "back_matter": [],
        "noise_ranges": [],
    }


def _compute_spans(estimates: list) -> dict:
    """按预估耗时占比计算各阶段进度跨度（同 progress_ui 逻辑）"""
    total = sum(estimates) or 1.0
    spans, acc = {}, 0.0
    for i, est in enumerate(estimates, 1):
        nxt = acc + est / total * 100
        spans[i] = (acc, min(nxt, 100.0))
        acc = nxt
    return spans


def _play_done_sound():
    """清脆的上行琶音（C6-E6-G6-C7），同 pipeline.py 末尾"""
    try:
        import winsound
        for freq, dur in ((1046, 110), (1319, 110), (1568, 110), (2093, 260)):
            winsound.Beep(freq, dur)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════
# 鎏金胶囊开关（自绘 toggle，替代复古 Checkbutton）
# ════════════════════════════════════════════════════════════

class Toggle(tk.Canvas):
    """圆角小胶囊 + 滑动圆点：开=鎏金右点，关=灰陶左点"""

    W, H = 46, 24

    def __init__(self, parent, variable: tk.BooleanVar, bg=_CARD, command=None):
        super().__init__(parent, width=self.W, height=self.H, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self.cmd = command
        self.bind("<Button-1>", self._on_click)
        self._trace = self.var.trace_add("write", lambda *_: self._draw())
        self.bind("<Destroy>", self._on_destroy)
        self._draw()

    def _on_destroy(self, _e):
        try:
            self.var.trace_remove("write", self._trace)
        except tk.TclError:
            pass

    def _on_click(self, _e):
        self.var.set(not bool(self.var.get()))
        if self.cmd:
            self.cmd()

    def _draw(self):
        try:
            self.delete("all")
            w, h = self.W, self.H
            r = h // 2
            on = bool(self.var.get())
            track = _GOLD if on else _TRACK_OFF
            # 胶囊：两圆 + 中矩形
            self.create_oval(2, 2, h - 2, h - 2, fill=track, outline="")
            self.create_oval(w - h + 2, 2, w - 2, h - 2, fill=track, outline="")
            self.create_rectangle(r, 2, w - r, h - 2, fill=track, outline="")
            # 圆点
            kr = r - 5
            cx = w - r if on else r
            self.create_oval(cx - kr, r - kr, cx + kr, r + kr,
                             fill=_PAPER_FG, outline=_LINE_GOLD, width=1)
        except tk.TclError:
            pass


# ════════════════════════════════════════════════════════════
# 主窗口
# ════════════════════════════════════════════════════════════

class App:
    def __init__(self, root, lang: str | None = None):
        self.root = root
        self.settings = _load_settings()
        self.lang = lang or self.settings.get("ui_lang", "zh")
        if self.lang not in STRINGS:
            self.lang = "zh"
        self._shot_path = APP_DIR / "_app_shot.png"

        self.books: list[dict] = []      # {path,name,pages,status,color,done,epub,t0,extra,widgets}
        self._keys: set[str] = set()     # 去重 key（小写绝对路径）
        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = False

        # 进度条 / 阶段卡状态
        self._spans: dict[int, tuple] = {}
        self._bar_target = 0.0
        self._bar_value = 0.0
        self._active_stage = 0
        self._stage_start: dict[int, float] = {}
        self._stage_done: set[int] = set()
        self._current_idx: int | None = None
        self._cards: list[dict] = []
        self._settings_open = True

        # 设置变量（跨界面重建持久）
        self.var_token = tk.StringVar(value=self.settings["mineru_token"])
        self.var_engine = tk.StringVar(value=_ENGINE_DISPLAY.get(
            self.settings.get("ocr_provider", "mineru"), "MinerU"))
        self.var_paddle = tk.StringVar(
            value=self.settings.get("paddleocr_token", ""))
        self.var_base = tk.StringVar(value=self.settings["llm_base_url"])
        self.var_key = tk.StringVar(value=self.settings["llm_key"])
        self.var_model = tk.StringVar(value=self.settings["llm_model"])
        self.var_ocr = tk.BooleanVar(value=bool(self.settings["ocr"]))
        self.var_translate = tk.BooleanVar(value=bool(self.settings["translate"]))
        self.var_translate_lang = tk.StringVar(value=self.settings["translate_lang"])
        self.var_outdir = tk.StringVar(value=self.settings["output_dir"])
        self.var_ui_lang = tk.StringVar(
            value="English" if self.lang == "en" else "中文")

        self._build_fonts()
        self._build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_id = None
        self._tick_id = None
        self._poll_id = self.root.after(80, self._poll_queue)
        self._tick_id = self.root.after(200, self._tick)

    # ── 文案 ────────────────────────────────────────────────

    def _t(self, key: str, **kw) -> str:
        s = STRINGS[self.lang].get(key, key)
        return s.format(**kw) if kw else s

    # ── 字体 / 组件工厂 ──────────────────────────────────────

    def _build_fonts(self):
        def F(family, size, bold=False, slant="roman"):
            return tkfont.Font(family=family, size=size,
                               weight="bold" if bold else "normal", slant=slant)
        self.fonts = {
            "caps":     F(_F_LATIN, 13, True),
            "title":    F(_F_KAI, 21, True),
            "title_en": F(_F_LATIN, 20, True),
            "subtitle": F(_F_LATIN, 10),
            "section":  F(_F_BODY, 12, True),
            "body":     F(_F_BODY, 12),
            "small":    F(_F_BODY, 10),
            "num":      F(_F_KAI, 14, True),
            "card":     F(_F_BODY, 12, True),
            "desc":     F(_F_BODY, 10),
            "timer":    F(_F_LATIN, 12),
            "pct":      F(_F_LATIN, 12),
            "btn":      F(_F_BODY, 12, True),
            "btn_s":    F(_F_BODY, 10),
            "link":     F(_F_BODY, 10),
            "symbol":   F(_F_SYMBOL, 12),
        }

    def _btn(self, parent, text, cmd, primary=False, small=False, state="normal"):
        """按钮工厂：primary=鎏金实心；否则=细金描边。均带 hover 反馈"""
        normal_bg = _GOLD if primary else _CARD
        hover_bg = _GOLD_HI if primary else _PARCH_DEEP
        b = tk.Button(
            parent, text=text, command=cmd,
            bg=normal_bg, fg=_PAPER_FG if primary else _INK,
            activebackground=hover_bg,
            activeforeground=_PAPER_FG if primary else _INK,
            disabledforeground=_FAINT,
            relief="flat", bd=0, cursor="hand2",
            font=self.fonts["btn_s" if small else "btn"],
            padx=10 if small else 18, pady=3 if small else 8,
            highlightbackground=_LINE_GOLD, highlightthickness=1,
            state=state,
        )

        def on_enter(_e, b=b, h=hover_bg):
            if str(b.cget("state")) == "normal":
                b.config(bg=h)

        def on_leave(_e, b=b, n=normal_bg):
            b.config(bg=n)

        b.bind("<Enter>", on_enter)
        b.bind("<Leave>", on_leave)
        return b

    def _entry(self, parent, show=None, textvariable=None):
        return tk.Entry(
            parent, show=show, textvariable=textvariable,
            bg=_ENTRY_BG, fg=_INK, insertbackground=_INK,
            relief="flat", bd=0, font=self.fonts["body"],
            highlightbackground=_LINE, highlightcolor=_GOLD_HI,
            highlightthickness=1,
        )

    def _style_ttk(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Paper.TCombobox",
            fieldbackground=_ENTRY_BG, background=_CARD,
            foreground=_INK, arrowcolor=_GOLD,
            bordercolor=_LINE, lightcolor=_LINE, darkcolor=_LINE,
            padding=2)
        style.map("Paper.TCombobox",
                  fieldbackground=[("readonly", _ENTRY_BG), ("disabled", _PARCH_DEEP)],
                  foreground=[("readonly", _INK), ("disabled", _FAINT)],
                  arrowcolor=[("hover", _GOLD_HI)])
        self.root.option_add("*TCombobox*Listbox.background", _ENTRY_BG)
        self.root.option_add("*TCombobox*Listbox.foreground", _INK)
        self.root.option_add("*TCombobox*Listbox.selectBackground", _GOLD)
        self.root.option_add("*TCombobox*Listbox.selectForeground", _PAPER_FG)
        self.root.option_add("*TCombobox*Listbox.font", self.fonts["body"])

    def _section(self, parent, text, extra=None, pady=(14, 6)):
        """金色小标题 + 右侧延展细线；extra(row) 可在线条之前布置右侧组件"""
        row = tk.Frame(parent, bg=_PARCH)
        row.pack(fill="x", pady=pady)
        tk.Label(row, text=text, bg=_PARCH, fg=_GOLD,
                 font=self.fonts["section"]).pack(side="left")
        if extra:
            extra(row)
        line = tk.Canvas(row, height=2, bg=_PARCH, highlightthickness=0, bd=0)
        line.pack(side="left", fill="x", expand=True, padx=(10, 2), pady=(9, 0))

        def draw(_e=None, cv=line):
            try:
                cv.delete("all")
                cv.create_line(0, 1, cv.winfo_width(), 1, fill=_LINE_GOLD)
            except tk.TclError:
                pass
        line.bind("<Configure>", draw)
        return row

    # ── UI 构建（语言切换时整体重建） ─────────────────────────

    def _build_ui(self):
        root = self.root
        for child in root.winfo_children():
            child.destroy()
        from version import __version__
        root.title(f"{self._t('window_title')}  v{__version__}")
        root.configure(bg=_PARCH)
        self._style_ttk()

        # 双线鎏金边框（同 progress_ui）
        outer_line = tk.Frame(root, bg=_PARCH, highlightbackground=_LINE_GOLD,
                              highlightthickness=1, bd=0)
        outer_line.pack(fill="both", expand=True, padx=6, pady=6)
        inner_line = tk.Frame(outer_line, bg=_PARCH, highlightbackground=_LINE,
                              highlightthickness=1, bd=0)
        inner_line.pack(fill="both", expand=True, padx=4, pady=4)
        main = tk.Frame(inner_line, bg=_PARCH)
        main.pack(fill="both", expand=True, padx=24, pady=16)
        self._main = main

        # ━━ 眉题 + 主标题 + 副标题 ━━
        tk.Label(main, text="✦  B O O K S   C O N V E R T E R  ✦",
                 bg=_PARCH, fg=_GOLD, font=self.fonts["caps"]).pack(fill="x")
        tk.Label(main, text=self._t("title"),
                 bg=_PARCH, fg=_INK,
                 font=self.fonts["title" if self.lang == "zh" else "title_en"]
                 ).pack(fill="x", pady=(4, 0))
        tk.Label(main, text=self._t("subtitle"),
                 bg=_PARCH, fg=_SUBINK, font=self.fonts["subtitle"]
                 ).pack(fill="x", pady=(3, 0))
        self._draw_divider(main)

        # ━━ 拖放区 ━━
        self._drop = tk.Canvas(main, height=84, bg=_CARD, highlightthickness=0,
                               bd=0, cursor="hand2")
        self._drop.pack(fill="x")
        self._drop.bind("<Configure>", lambda e: self._draw_dropzone())
        self._drop.bind("<Button-1>", lambda e: self._choose_files())
        self._drop.drop_target_register(DND_FILES)
        self._drop.dnd_bind("<<Drop>>", self._on_drop)
        try:
            self._drop.dnd_bind("<<DragEnter>>", lambda e: self._draw_dropzone(hot=True))
            self._drop.dnd_bind("<<DragLeave>>", lambda e: self._draw_dropzone())
        except tk.TclError:
            pass

        # ━━ 队列 ━━
        def _queue_extra(row):
            self.btn_clear = self._btn(row, self._t("clear"), self._clear_books,
                                       small=True)
            self.btn_clear.pack(side="right", pady=(0, 4))
        self._section(main, self._t("sec_queue"), extra=_queue_extra)

        list_wrap = tk.Frame(main, bg=_PARCH)
        list_wrap.pack(fill="x")
        self._list_canvas = tk.Canvas(list_wrap, height=128, bg=_PARCH,
                                      highlightthickness=0, bd=0)
        sb = tk.Scrollbar(list_wrap, orient="vertical",
                          command=self._list_canvas.yview)
        self._list_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._list_canvas.pack(side="left", fill="x", expand=True)
        self._list_inner = tk.Frame(self._list_canvas, bg=_PARCH)
        self._list_win = self._list_canvas.create_window(
            (0, 0), window=self._list_inner, anchor="nw")
        self._list_inner.bind("<Configure>", self._on_list_configure)
        self._list_canvas.bind("<Configure>", self._on_list_canvas_configure)
        self._list_canvas.bind("<Enter>", lambda e: self._list_canvas.bind_all(
            "<MouseWheel>", self._on_wheel))
        self._list_canvas.bind("<Leave>", lambda e: self._list_canvas.unbind_all(
            "<MouseWheel>"))

        self._list_hint = tk.Label(
            self._list_inner, text=self._t("queue_empty"),
            bg=_PARCH, fg=_FAINT, font=self.fonts["small"])
        self._list_hint.pack(pady=10)

        # 重建已有队列行（语言切换时）
        for b in self.books:
            self._build_row(b)
        self._refresh_hint()

        # ━━ 设置（可折叠） ━━
        srow = tk.Frame(main, bg=_PARCH)
        srow.pack(fill="x", pady=(14, 6))
        arrow = "▾" if self._settings_open else "▸"
        self.btn_settings = tk.Button(
            srow, text=f"{self._t('sec_settings')}  {arrow}",
            command=self._toggle_settings,
            bg=_PARCH, fg=_GOLD, activebackground=_PARCH, activeforeground=_GOLD_HI,
            relief="flat", bd=0, cursor="hand2", font=self.fonts["section"],
            padx=0, pady=0)
        self.btn_settings.pack(side="left")
        sline = tk.Canvas(srow, height=2, bg=_PARCH, highlightthickness=0, bd=0)
        sline.pack(side="left", fill="x", expand=True, padx=(10, 2), pady=(9, 0))
        sline.bind("<Configure>", lambda e: (sline.delete("all"),
               sline.create_line(0, 1, sline.winfo_width(), 1, fill=_LINE_GOLD)))

        self._settings_panel = tk.Frame(
            main, bg=_CARD, highlightbackground=_LINE, highlightthickness=1, bd=0)
        if self._settings_open:
            self._settings_panel.pack(fill="x")
        sp = tk.Frame(self._settings_panel, bg=_CARD)
        sp.pack(fill="x", padx=16, pady=12)
        sp.columnconfigure(1, weight=1)

        def row_label(r, text):
            tk.Label(sp, text=text, bg=_CARD, fg=_SUBINK,
                     font=self.fonts["body"], anchor="e"
                     ).grid(row=r, column=0, sticky="e", padx=(0, 12), pady=4)

        row_label(0, self._t("lbl_engine"))
        self._engine_combo = ttk.Combobox(
            sp, values=[d for _, d in ENGINE_OPTIONS], state="readonly",
            textvariable=self.var_engine, style="Paper.TCombobox",
            font=self.fonts["body"], width=12)
        self._engine_combo.grid(row=0, column=1, sticky="w", pady=4)

        row_label(1, self._t("lbl_mineru"))
        self._entry(sp, show="●", textvariable=self.var_token
                    ).grid(row=1, column=1, sticky="ew", pady=4)

        row_label(2, self._t("lbl_paddle"))
        self._entry(sp, show="●", textvariable=self.var_paddle
                    ).grid(row=2, column=1, sticky="ew", pady=4)

        row_label(3, self._t("lbl_base"))
        self._entry(sp, textvariable=self.var_base
                    ).grid(row=3, column=1, sticky="ew", pady=4)

        row_label(4, self._t("lbl_key"))
        self._entry(sp, show="●", textvariable=self.var_key
                    ).grid(row=4, column=1, sticky="ew", pady=4)

        row_label(5, self._t("lbl_model"))
        model_row = tk.Frame(sp, bg=_CARD)
        model_row.grid(row=5, column=1, sticky="ew", pady=4)
        model_row.columnconfigure(0, weight=1)
        self._model_combo = ttk.Combobox(
            model_row, textvariable=self.var_model, style="Paper.TCombobox",
            font=self.fonts["body"], values=[])
        self._model_combo.grid(row=0, column=0, sticky="ew")
        self.btn_fetch = self._btn(model_row, self._t("btn_fetch"),
                                   self._on_fetch_models, small=True)
        self.btn_fetch.grid(row=0, column=1, padx=(8, 0))

        row_label(6, self._t("lbl_ui_lang"))
        self._lang_combo = ttk.Combobox(
            sp, values=["中文", "English"], state="readonly",
            textvariable=self.var_ui_lang, style="Paper.TCombobox",
            font=self.fonts["body"], width=12)
        self._lang_combo.grid(row=6, column=1, sticky="w", pady=4)
        self._lang_combo.bind("<<ComboboxSelected>>", self._on_lang_change)

        row_label(7, "OCR")
        ocr_row = tk.Frame(sp, bg=_CARD)
        ocr_row.grid(row=7, column=1, sticky="w", pady=(10, 0))
        Toggle(ocr_row, self.var_ocr, bg=_CARD).pack(side="left")
        tk.Label(ocr_row, text=self._t("chk_ocr"), bg=_CARD, fg=_INK,
                 font=self.fonts["body"]).pack(side="left", padx=(10, 0))
        tk.Label(sp, text=self._t("ocr_hint"), bg=_CARD, fg=_FAINT,
                 font=self.fonts["small"], anchor="w"
                 ).grid(row=8, column=1, sticky="w", pady=(2, 0))

        row_label(9, self._t("lbl_translate"))
        tl_row = tk.Frame(sp, bg=_CARD)
        tl_row.grid(row=9, column=1, sticky="w", pady=(10, 4))
        Toggle(tl_row, self.var_translate, bg=_CARD,
               command=self._on_translate_toggle).pack(side="left")
        tk.Label(tl_row, text=self._t("chk_translate"), bg=_CARD, fg=_INK,
                 font=self.fonts["body"]).pack(side="left", padx=(10, 8))
        self._tl_combo = ttk.Combobox(
            tl_row, values=TRANSLATE_LANGS, state="readonly",
            textvariable=self.var_translate_lang, style="Paper.TCombobox",
            font=self.fonts["body"], width=10)
        self._tl_combo.pack(side="left")
        self._tl_combo.config(
            state="readonly" if self.var_translate.get() else "disabled")

        row_label(10, self._t("lbl_outdir"))
        out_row = tk.Frame(sp, bg=_CARD)
        out_row.grid(row=10, column=1, sticky="ew", pady=4)
        out_row.columnconfigure(0, weight=1)
        self._entry(out_row, textvariable=self.var_outdir
                    ).grid(row=0, column=0, sticky="ew")
        self._btn(out_row, self._t("browse"), self._browse_outdir, small=True
                  ).grid(row=0, column=1, padx=(8, 0))
        tk.Label(sp, text=self._t("outdir_hint"), bg=_CARD, fg=_FAINT,
                 font=self.fonts["small"], anchor="w"
                 ).grid(row=11, column=1, sticky="w")

        note = tk.Frame(sp, bg=_CARD)
        note.grid(row=12, column=0, columnspan=2, sticky="w", pady=(10, 0))
        tk.Label(note, text=self._t("note_mineru"), bg=_CARD, fg=_SUBINK,
                 font=self.fonts["small"]).pack(side="left")
        link = tk.Label(note, text="https://mineru.net/apiManage/token",
                        bg=_CARD, fg=_GOLD, font=self.fonts["link"],
                        cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda e: self._open_url(
            "https://mineru.net/apiManage/token"))
        tk.Label(note, text="    " + self._t("note_llm"),
                 bg=_CARD, fg=_SUBINK, font=self.fonts["small"]).pack(side="left")

        note2 = tk.Frame(sp, bg=_CARD)
        note2.grid(row=13, column=0, columnspan=2, sticky="w")
        tk.Label(note2, text=self._t("note_paddle"), bg=_CARD, fg=_SUBINK,
                 font=self.fonts["small"]).pack(side="left")
        link2 = tk.Label(note2, text="https://ai.baidu.com/ai-doc/AISTUDIO/fml7mozw5",
                         bg=_CARD, fg=_GOLD, font=self.fonts["link"],
                         cursor="hand2")
        link2.pack(side="left")
        link2.bind("<Button-1>", lambda e: self._open_url(
            "https://ai.baidu.com/ai-doc/AISTUDIO/fml7mozw5"))

        save_row = tk.Frame(sp, bg=_CARD)
        save_row.grid(row=14, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.btn_update = self._btn(save_row, self._t("btn_update"),
                                    self._on_check_update, small=True)
        self.btn_update.pack(side="left")
        self._save_hint = tk.Label(save_row, text="", bg=_CARD, fg=_GOLD,
                                   font=self.fonts["small"])
        self._save_hint.pack(side="left", padx=(10, 0))
        self._btn(save_row, self._t("save"), self._on_save_settings, small=True
                  ).pack(side="right")

        # ━━ 当前进度 ━━
        def _progress_extra(row):
            self._cur_book_lbl = tk.Label(row, text="—", bg=_PARCH, fg=_SUBINK,
                                          font=self.fonts["body"], anchor="e")
            self._cur_book_lbl.pack(side="right", pady=(0, 4))
        self._section(main, self._t("sec_progress"), extra=_progress_extra)

        self._cards_frame = tk.Frame(main, bg=_PARCH)
        self._cards_frame.pack(fill="x")
        self._rebuild_cards(translate=bool(self.var_translate.get()))

        bar_row = tk.Frame(main, bg=_PARCH)
        bar_row.pack(fill="x", pady=(10, 8))
        self._bar_canvas = tk.Canvas(bar_row, height=10, bg=_PARCH,
                                     highlightthickness=0, bd=0)
        self._bar_canvas.pack(side="left", fill="x", expand=True)
        self._bar_canvas.bind("<Configure>", lambda e: self._redraw_bar())
        self._bar_pct = tk.Label(bar_row, text="0%", bg=_PARCH, fg=_SUBINK,
                                 font=self.fonts["pct"], anchor="e", width=6)
        self._bar_pct.pack(side="right", padx=(8, 0))

        detail_card = tk.Frame(main, bg=_PARCH_DEEP,
                               highlightbackground=_LINE, highlightthickness=1, bd=0)
        detail_card.pack(fill="x", pady=(2, 0))
        tk.Label(detail_card, text=self._t("status_header"),
                 bg=_PARCH_DEEP, fg=_GOLD,
                 font=self.fonts["desc"], anchor="w"
                 ).pack(fill="x", padx=14, pady=(8, 0))
        self._detail_lbl = tk.Label(
            detail_card, text=self._t("idle_detail"),
            bg=_PARCH_DEEP, fg=_INK, font=self.fonts["body"],
            anchor="nw", justify="left", wraplength=780, height=2)
        self._detail_lbl.pack(fill="x", padx=14, pady=(0, 10))

        # ━━ 底部按钮 ━━
        btn_row = tk.Frame(main, bg=_PARCH)
        btn_row.pack(fill="x", pady=(14, 2))
        self.btn_start = self._btn(btn_row, self._t("start"), self._on_start,
                                   primary=True)
        self.btn_start.pack(side="left")
        self.btn_stop = self._btn(btn_row, self._t("stop"), self._on_stop,
                                  state="disabled")
        self.btn_stop.pack(side="left", padx=(12, 0))
        self._run_hint = tk.Label(btn_row, text="", bg=_PARCH, fg=_SUBINK,
                                  font=self.fonts["small"], anchor="e")
        self._run_hint.pack(side="right")

    def _draw_divider(self, parent):
        cv = tk.Canvas(parent, height=16, bg=_PARCH, highlightthickness=0, bd=0)
        cv.pack(fill="x", pady=(4, 12))

        def draw(_e=None):
            try:
                cv.delete("all")
                w, cy = cv.winfo_width(), 8
                cv.create_line(60, cy, w // 2 - 14, cy, fill=_GOLD, width=1)
                cv.create_line(w // 2 + 14, cy, w - 60, cy, fill=_GOLD, width=1)
                cv.create_polygon(w // 2, cy - 5, w // 2 + 5, cy,
                                  w // 2, cy + 5, w // 2 - 5, cy,
                                  fill=_GOLD, outline="")
            except tk.TclError:
                pass
        cv.bind("<Configure>", draw)

    def _draw_dropzone(self, hot=False):
        cv = self._drop
        try:
            cv.delete("all")
            w, h = cv.winfo_width(), cv.winfo_height()
            color = _GOLD_HI if hot else _LINE_GOLD
            cv.create_rectangle(8, 6, w - 8, h - 6, outline=color,
                                width=3 if hot else 2, dash=(7, 5))
            cv.create_text(w / 2, h / 2 - 12,
                           text=self._t("drop_hot") if hot else self._t("drop_main"),
                           fill=_GOLD_HI if hot else _SUBINK,
                           font=self.fonts["body"])
            if not hot:
                cv.create_text(w / 2, h / 2 + 14, text=self._t("drop_sub"),
                               fill=_FAINT, font=self.fonts["small"])
        except tk.TclError:
            pass

    # ── 队列列表 ─────────────────────────────────────────────

    def _on_list_configure(self, _e=None):
        self._list_canvas.configure(
            scrollregion=self._list_canvas.bbox("all"))

    def _on_list_canvas_configure(self, e):
        self._list_canvas.itemconfigure(self._list_win, width=e.width)

    def _on_wheel(self, e):
        self._list_canvas.yview_scroll(int(-e.delta / 120), "units")

    def _refresh_hint(self):
        if self.books:
            self._list_hint.pack_forget()
        elif not self._list_hint.winfo_ismapped():
            self._list_hint.pack(pady=10)

    def _add_entry(self, path: str, pages: int | None) -> dict:
        key = os.path.normcase(os.path.abspath(path))
        book = {"path": path, "name": Path(path).stem, "pages": pages,
                "status": self._t("waiting"), "color": _FAINT,
                "done": False, "epub": None, "t0": None, "extra": False,
                "widgets": {}}
        self._keys.add(key)
        self.books.append(book)
        self._build_row(book)
        self._refresh_hint()
        return book

    def _build_row(self, book: dict):
        """构建（或语言切换后重建）一行的组件"""
        frame = tk.Frame(self._list_inner, bg=_CARD,
                         highlightbackground=_LINE, highlightthickness=1, bd=0)
        frame.pack(fill="x", pady=2, padx=2)
        frame.columnconfigure(0, weight=1)

        name_lbl = tk.Label(frame, text=book["name"], bg=_CARD, fg=_INK,
                            font=self.fonts["card"], anchor="w")
        name_lbl.grid(row=0, column=0, sticky="ew", padx=(10, 6), pady=(6, 6))
        pages = book["pages"]
        pages_lbl = tk.Label(
            frame,
            text=self._t("pages", n=pages) if pages else self._t("pages_unknown"),
            bg=_CARD, fg=_SUBINK, font=self.fonts["small"], anchor="e", width=7)
        pages_lbl.grid(row=0, column=1, padx=4)
        status_lbl = tk.Label(frame, text=book["status"], bg=_CARD,
                              fg=book["color"], font=self.fonts["body"],
                              anchor="w", width=22)
        status_lbl.grid(row=0, column=2, padx=4)
        time_lbl = tk.Label(frame, text="", bg=_CARD, fg=_SUBINK,
                            font=self.fonts["timer"], anchor="e", width=7)
        time_lbl.grid(row=0, column=3, padx=4)
        btn_rm = self._btn(frame, "✕", lambda b=book: self._remove_book(b),
                           small=True)
        btn_rm.grid(row=0, column=4, padx=(2, 8))
        if self.running:
            btn_rm.config(state="disabled")

        book["widgets"] = {"frame": frame, "status": status_lbl,
                           "time": time_lbl, "remove": btn_rm}
        if book["extra"] and book["epub"]:
            book["extra"] = False
            self._build_done_row(book)

    def _remove_book(self, book):
        if self.running:
            return
        w = book["widgets"].get("frame")
        if w:
            w.destroy()
        self._keys.discard(os.path.normcase(os.path.abspath(book["path"])))
        self.books.remove(book)
        self._refresh_hint()

    def _clear_books(self):
        if self.running:
            return
        for book in list(self.books):
            f = book["widgets"].get("frame")
            if f:
                f.destroy()
        self.books.clear()
        self._keys.clear()
        self._refresh_hint()

    def _add_files(self, paths):
        added = 0
        for p in paths:
            p = p.strip().strip("{}")
            if not p.lower().endswith(".pdf"):
                continue
            if not os.path.isfile(p):
                continue
            key = os.path.normcase(os.path.abspath(p))
            if key in self._keys:
                continue
            try:
                doc = fitz.open(p)
                pages = doc.page_count
                doc.close()
            except Exception:
                pages = None
            self._add_entry(p, pages)
            added += 1
        if added:
            # 数量一律以队列实际长度为准（修复：旧版只报本次新增数，易误读）
            self._set_detail(self._t("added", n=added, total=len(self.books)),
                             _SUBINK)
        else:
            self._set_detail(self._t("no_new"), _SUBINK)

    def _choose_files(self):
        paths = filedialog.askopenfilenames(
            title=self._t("dlg_choose_pdf"), filetypes=[("PDF 文件", "*.pdf")])
        if paths:
            self._add_files(paths)

    def _on_drop(self, event):
        self._draw_dropzone()
        try:
            paths = self.root.tk.splitlist(event.data)
        except tk.TclError:
            paths = [event.data]
        self._add_files(paths)

    # ── 设置面板 ─────────────────────────────────────────────

    def _toggle_settings(self):
        self._settings_open = not self._settings_open
        arrow = "▾" if self._settings_open else "▸"
        self.btn_settings.config(text=f"{self._t('sec_settings')}  {arrow}")
        if self._settings_open:
            self._settings_panel.pack(fill="x")
        else:
            self._settings_panel.pack_forget()

    def _on_translate_toggle(self):
        self._tl_combo.config(
            state="readonly" if self.var_translate.get() else "disabled")

    def _on_lang_change(self, _e=None):
        new = "en" if self.var_ui_lang.get() == "English" else "zh"
        if new == self.lang:
            return
        self.lang = new
        self.settings = self._collect_settings()
        _save_settings(self.settings)
        self._build_ui()

    def _collect_settings(self) -> dict:
        return {
            "mineru_token": self.var_token.get(),
            "ocr_provider": _ENGINE_BY_DISPLAY.get(
                self.var_engine.get(), "mineru"),
            "paddleocr_token": self.var_paddle.get(),
            "llm_base_url": self.var_base.get(),
            "llm_key": self.var_key.get(),
            "llm_model": self.var_model.get(),
            "ocr": bool(self.var_ocr.get()),
            "translate": bool(self.var_translate.get()),
            "translate_lang": self.var_translate_lang.get(),
            "ui_lang": self.lang,
            "output_dir": self.var_outdir.get().strip(),
        }

    def _on_save_settings(self):
        self.settings = self._collect_settings()
        _save_settings(self.settings)
        _apply_settings(self.settings)
        self._save_hint.config(text=self._t("saved"))
        self.root.after(2000, lambda: self._save_hint.config(text=""))

    def _browse_outdir(self):
        d = filedialog.askdirectory(title=self._t("dlg_outdir"))
        if d:
            self.var_outdir.set(d)

    def _open_url(self, url):
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass

    # ── 检查更新（工作线程，不卡 UI） ───────────────────────

    def _on_check_update(self):
        if getattr(self, "_update_checking", False):
            return
        self._update_checking = True
        self.btn_update.config(state="disabled")
        self._save_hint.config(text=self._t("update_checking"))

        def work():
            try:
                from updater import check_for_update
                r = check_for_update()
            except Exception as e:
                r = {"status": "error", "error": str(e), "current": "?"}
            self._put("update_result", r)

        threading.Thread(target=work, daemon=True).start()

    # ── 模型列表在线获取（工作线程，不卡 UI） ─────────────────

    def _on_fetch_models(self):
        base = self.var_base.get().strip()
        key = self.var_key.get().strip()
        if not base:
            self._set_detail(self._t("models_need_base"), _RED)
            return
        self.btn_fetch.config(state="disabled")
        self._save_hint.config(text=self._t("fetching"))

        def work():
            try:
                from openai import OpenAI
                client = OpenAI(api_key=key or "none", base_url=base, timeout=25)
                ids = sorted({m.id for m in client.models.list().data})
                self.q.put(("models_ok", ids))
            except Exception as e:
                self.q.put(("models_fail", str(e)))

        threading.Thread(target=work, daemon=True).start()

    # ── 阶段卡 / 进度条 ──────────────────────────────────────

    def _rebuild_cards(self, translate: bool):
        for child in self._cards_frame.winfo_children():
            child.destroy()
        self._cards = []
        self._active_stage = 0
        self._stage_start = {}
        self._stage_done = set()

        stages = [(self._t("s1_name"), self._t("s1_desc")),
                  (self._t("s2_name"), self._t("s2_desc"))]
        if translate:
            stages.append((self._t("s3_name"), self._t("s3_desc")))
        stages.append((self._t("s4_name"), self._t("s4_desc")))

        for i, (name, desc) in enumerate(stages):
            card = tk.Frame(self._cards_frame, bg=_CARD,
                            highlightbackground=_LINE, highlightthickness=1, bd=0)
            card.pack(fill="x", pady=3)
            accent = tk.Frame(card, bg=_CARD, width=4)
            accent.pack(side="left", fill="y")
            accent.pack_propagate(False)
            body = tk.Frame(card, bg=_CARD)
            body.pack(side="left", fill="x", expand=True, padx=(12, 10), pady=8)
            num = tk.Label(body, text=_STAGE_NUM[i], bg=_CARD, fg=_FAINT,
                           font=self.fonts["num"], width=2)
            num.pack(side="left")
            info = tk.Frame(body, bg=_CARD)
            info.pack(side="left", fill="x", expand=True)
            name_lbl = tk.Label(info, text=name, bg=_CARD, fg=_FAINT,
                                font=self.fonts["card"], anchor="w")
            name_lbl.pack(fill="x")
            desc_lbl = tk.Label(info, text=desc, bg=_CARD, fg=_FAINT,
                                font=self.fonts["desc"], anchor="w")
            desc_lbl.pack(fill="x")
            timer = tk.Label(body, text="", bg=_CARD, fg=_FAINT,
                             font=self.fonts["timer"], anchor="e", width=8)
            timer.pack(side="right")
            self._cards.append({"card": card, "accent": accent, "num": num,
                                "name": name_lbl, "desc": desc_lbl,
                                "timer": timer})

    def _set_card_active(self, idx):
        c = self._cards[idx]
        c["accent"].config(bg=_GOLD_HI)
        c["num"].config(fg=_GOLD_HI)
        c["name"].config(fg=_INK)
        c["desc"].config(fg=_SUBINK)
        c["timer"].config(fg=_GOLD_HI)

    def _set_card_done(self, idx):
        c = self._cards[idx]
        c["accent"].config(bg=_GOLD)
        c["num"].config(text="✓", fg=_GOLD)
        c["name"].config(fg=_INK)
        c["desc"].config(fg=_SUBINK)
        c["timer"].config(fg=_SUBINK)

    def _redraw_bar(self):
        cv = self._bar_canvas
        try:
            cv.delete("all")
            w, cy = cv.winfo_width(), 5
            cv.create_line(2, cy, w - 2, cy, fill=_TROUGH, width=5,
                           capstyle="round")
            fill_w = max((w - 4) * self._bar_value / 100.0, 0)
            if fill_w > 1:
                cv.create_line(2, cy, 2 + fill_w, cy, fill=_GOLD_HI, width=5,
                               capstyle="round")
            self._bar_pct.config(text=f"{self._bar_value:.0f}%")
        except tk.TclError:
            pass

    def _set_detail(self, text, fg=_INK):
        self._detail_lbl.config(text=text, fg=fg)

    # ── 开始 / 停止 ─────────────────────────────────────────

    def _on_start(self):
        if self.running:
            return
        pending = [b for b in self.books if not b["done"]]
        if not pending:
            messagebox.showinfo(self._t("msg_no_books_t"),
                                self._t("msg_no_books"))
            return

        self.settings = self._collect_settings()
        _save_settings(self.settings)
        _apply_settings(self.settings)

        engine = self.settings.get("ocr_provider", "mineru")
        if engine == "paddleocr":
            if not self.settings.get("paddleocr_token", "").strip():
                messagebox.showerror(self._t("msg_no_ptoken_t"),
                                     self._t("msg_no_ptoken"))
                if not self._settings_open:
                    self._toggle_settings()
                return
        elif not self.settings["mineru_token"].strip():
            messagebox.showerror(self._t("msg_no_token_t"),
                                 self._t("msg_no_token"))
            if not self._settings_open:
                self._toggle_settings()
            return
        if not self.settings["llm_key"].strip():
            ok = messagebox.askokcancel(self._t("msg_no_key_t"),
                                        self._t("msg_no_key"))
            if not ok:
                return

        # 重置待处理书的状态与界面
        for b in pending:
            self._set_row_status(b, self._t("waiting"), _FAINT)
            b["widgets"]["time"].config(text="")
        for b in self.books:
            b["widgets"]["remove"].config(state="disabled")
        self.btn_clear.config(state="disabled")
        self._lang_combo.config(state="disabled")   # 转换中禁止切换语言（防重建）

        self.cancel.clear()
        self.running = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self._run_hint.config(text="")
        self._bar_target = self._bar_value = 0.0
        self._redraw_bar()

        self.worker = threading.Thread(target=self._worker_main, daemon=True)
        self.worker.start()

    def _on_stop(self):
        if not self.running:
            return
        self.cancel.set()
        self.btn_stop.config(state="disabled")
        self._set_detail(self._t("stop_req"), _SUBINK)

    def _restore_idle_controls(self):
        self.running = False
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.btn_clear.config(state="normal")
        self._lang_combo.config(state="readonly")
        for b in self.books:
            b["widgets"]["remove"].config(state="normal")

    # ── worker 线程：串行转换（严禁触碰 tkinter） ────────────

    def _put(self, *msg):
        self.q.put(msg)

    def _worker_main(self):
        try:
            from ocr_provider import get_provider
            from stage2_hybrid import analyze_structure_hybrid, save_structure
            from stage3_epub import generate_epub
        except Exception as e:
            self._put("fatal", self._t("fatal_load", err=e))
            return

        engine = self.settings.get("ocr_provider", "mineru")
        n_done = 0
        try:
            for i, book in enumerate(self.books):
                if self.cancel.is_set():
                    break
                if book["done"]:
                    continue
                ok = self._process_book(
                    i, book, engine, get_provider,
                    analyze_structure_hybrid, save_structure, generate_epub)
                if ok:
                    n_done += 1
                if self.cancel.is_set():
                    self._put("book_status", i, self._t("cancelled"), _SUBINK)
                    break
        except Exception as e:
            logger.exception("worker 异常")
            self._put("fatal", self._t("fatal_run", err=e))
            return
        self._put("queue_done", self.cancel.is_set(), n_done)

    def _process_book(self, i, book, engine, get_provider,
                      analyze_structure_hybrid, save_structure,
                      generate_epub) -> bool:
        pdf = Path(book["path"])
        # Windows 不允许目录名以空格/点结尾（同 pipeline.py）
        name = pdf.stem.rstrip(" .") or pdf.stem
        work_dir = pdf.parent / name                   # 约定：PDF 同目录的同名文件夹
        work_dir.mkdir(parents=True, exist_ok=True)
        out_dir = (Path(self.settings["output_dir"])
                   if self.settings["output_dir"] else pdf.parent)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            out_dir = pdf.parent

        translate = bool(self.settings["translate"])
        pages = book["pages"] or 300
        # MinerU 实测 ≈ 0.80 s/页；PaddleOCR 更快（同 pipeline.py 的预估）
        est1 = max(pages * (0.80 if engine == "mineru" else 0.50), 30)
        est2 = max(pages * 0.14, 15)
        estimates = [est1, est2]
        if translate:
            estimates.append(max(pages * 2.0, 30))
        estimates.append(3.0)
        spans = _compute_spans(estimates)

        t_book = time.time()
        self._put("book_start", i, name, translate, spans)

        def prog(stage, title):
            def cb(detail, fraction=None):
                self._put("stage_update", i, stage, title, detail, fraction)
            return cb

        # ── 壹 · PDF 解析（MinerU / PaddleOCR） ──
        self._put("book_status", i, self._t("st_s1"), _GOLD_HI)
        t0 = time.time()
        try:
            provider = get_provider(engine)
            mineru_info = provider.parse(str(pdf), str(work_dir),
                                         ocr=bool(self.settings["ocr"]),
                                         progress=prog(1, self._t("s1_name")))
            _save_stage1_metadata(str(work_dir), engine, mineru_info)
        except Exception as e:
            self._put("book_error", i, self._t("fail_mineru", err=e))
            return False
        self._put("stage_complete", i, 1, self._t("s1_name"), time.time() - t0)
        if self.cancel.is_set():
            return False

        # ── 贰 · Hybrid 结构分析（失败降级） ──
        self._put("book_status", i, self._t("st_s2"), _GOLD_HI)
        t0 = time.time()
        degraded = False
        try:
            structure = analyze_structure_hybrid(
                mineru_info["content_list"], name, str(work_dir),
                progress=prog(2, self._t("s2_name")))
            save_structure(structure, str(work_dir))
        except Exception as e:
            logger.warning(f"Stage 2 失败，降级为简单结构: {e}")
            self._put("stage_note", self._t("fallback_note"))
            structure = _fallback_structure(mineru_info, name)
            degraded = True
        self._put("stage_complete", i, 2, self._t("s2_name"), time.time() - t0)
        if self.cancel.is_set():
            return False

        # ── 叁 · 翻译（可选；失败只出原文版） ──
        translations = None
        if translate:
            target = self.settings.get("translate_lang", "简体中文")
            self._put("book_status", i, self._t("st_s3"), _GOLD_HI)
            t0 = time.time()
            try:
                from stage4_translate import translate_book
                result = translate_book(
                    mineru_info["content_list"],
                    structure.get("metadata", {}),
                    str(work_dir), target_lang=target,
                    progress=prog(3, self._t("s3_name")))
                translations = result["translations"]
                if result.get("title_zh"):
                    structure["metadata"]["title"] = result["title_zh"]
                structure["metadata"]["language"] = target
            except Exception as e:
                logger.warning(f"Stage 4 翻译失败，输出原文版: {e}")
                self._put("stage_note", self._t("translate_fail_note"))
                translations = None
            self._put("stage_complete", i, 3, self._t("s3_name"),
                      time.time() - t0)
            if self.cancel.is_set():
                return False

        # ── 末 · EPUB 生成 ──
        s_epub = 4 if translate else 3
        self._put("book_status", i, self._t("st_s4"), _GOLD_HI)
        t0 = time.time()
        try:
            epub_path = generate_epub(name, mineru_info, structure,
                                      str(work_dir), pdf_path=str(pdf),
                                      translations=translations)
            final = out_dir / epub_path.name
            if epub_path.resolve() != final.resolve():
                import shutil
                shutil.copy2(epub_path, final)
            epub_path = final
        except Exception as e:
            self._put("book_error", i, self._t("fail_epub", err=e))
            return False
        self._put("stage_complete", i, s_epub, self._t("s4_name"),
                  time.time() - t0)

        self._put("book_done", i, str(epub_path), time.time() - t_book, degraded)
        return True

    # ── GUI 消息处理（主线程） ───────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                self._handle(self.q.get_nowait())
        except queue.Empty:
            pass
        try:
            if self.root.winfo_exists():
                self._poll_id = self.root.after(80, self._poll_queue)
        except tk.TclError:
            pass

    def _set_row_status(self, book, text, color):
        book["status"] = text
        book["color"] = color
        lbl = book["widgets"].get("status")
        if lbl:
            shown = text if len(text) <= 42 else text[:41] + "…"
            lbl.config(text=shown, fg=color)

    def _handle(self, msg):
        cmd = msg[0]

        if cmd == "book_start":
            _, i, name, translate, spans = msg
            self._current_idx = i
            book = self.books[i]
            book["t0"] = time.time()
            shown = name if len(name) <= 30 else name[:29] + "…"
            self._cur_book_lbl.config(text=shown, fg=_INK)
            self._spans = spans
            self._bar_target = self._bar_value = 0.0
            self._redraw_bar()
            need_translate = translate and len(self._cards) != 4
            need_plain = not translate and len(self._cards) != 3
            if need_translate or need_plain:
                self._rebuild_cards(translate)
            else:
                for idx in range(len(self._cards)):
                    c = self._cards[idx]
                    c["accent"].config(bg=_CARD)
                    c["num"].config(text=_STAGE_NUM[idx], fg=_FAINT)
                    c["name"].config(fg=_FAINT)
                    c["desc"].config(fg=_FAINT)
                    c["timer"].config(text="", fg=_FAINT)
                self._active_stage = 0
                self._stage_start = {}
                self._stage_done = set()
            self._set_detail(self._t("book_open", name=name), _INK)

        elif cmd == "book_status":
            _, i, text, color = msg
            self._set_row_status(self.books[i], text, color)

        elif cmd == "update_result":
            _, r = msg
            self._update_checking = False
            self.btn_update.config(state="normal")
            self._save_hint.config(text="")
            if r["status"] == "update_available":
                if messagebox.askyesno(
                        self._t("update_new_t"),
                        self._t("update_new", latest=r["latest"],
                                ver=r["current"])):
                    self._open_url(r["url"])
            elif r["status"] == "latest":
                messagebox.showinfo(
                    self._t("update_title"),
                    self._t("update_latest", ver=r["current"]))
            else:
                messagebox.showwarning(
                    self._t("update_title"),
                    self._t("update_fail", err=r["error"]))

        elif cmd == "stage_update":
            _, i, stage, title, detail, fraction = msg
            idx = stage - 1
            if 0 <= idx < len(self._cards):
                if stage not in self._stage_start:
                    self._stage_start[stage] = time.time()
                self._active_stage = stage
                for k in range(idx):
                    if k not in self._stage_done:
                        self._set_card_done(k)
                        self._stage_done.add(k)
                self._set_card_active(idx)
                if fraction is not None:
                    base, ceiling = self._spans.get(stage, (0, 100))
                    target = base + max(0.0, min(fraction, 1.0)) * (ceiling - base)
                    self._bar_target = max(self._bar_target, target)
            if detail:
                self._set_detail(detail)

        elif cmd == "stage_complete":
            _, i, stage, title, elapsed = msg
            idx = stage - 1
            if 0 <= idx < len(self._cards):
                self._set_card_done(idx)
                self._stage_done.add(stage)
                self._cards[idx]["timer"].config(text=_fmt_elapsed(elapsed))
                self._bar_target = max(self._bar_target,
                                       self._spans.get(stage, (0, 100))[1])
            if self._active_stage == stage:
                self._active_stage = 0
            self._set_detail(self._t("stage_done", title=title,
                                     elapsed=_fmt_elapsed(elapsed)), _SUBINK)

        elif cmd == "stage_note":
            self._set_detail(msg[1], _SUBINK)

        elif cmd == "book_done":
            _, i, epub_path, elapsed, degraded = msg
            book = self.books[i]
            book["done"] = True
            book["epub"] = epub_path
            suffix = self._t("done_degraded") if degraded else ""
            self._set_row_status(book, f"{self._t('done')}{suffix}", _GOLD)
            book["widgets"]["time"].config(text=_fmt_elapsed(elapsed))
            self._build_done_row(book)
            self._bar_target = self._bar_value = 100.0
            self._redraw_bar()
            for k in range(len(self._cards)):
                self._set_card_done(k)
            self._set_detail(self._t("book_done_detail", name=book["name"],
                                     path=epub_path), _GOLD)

        elif cmd == "book_error":
            _, i, reason = msg
            book = self.books[i]
            self._set_row_status(book, f"✗ {reason}", _RED)
            if book["t0"]:
                book["widgets"]["time"].config(
                    text=_fmt_elapsed(time.time() - book["t0"]))
            self._set_detail(f"《{book['name']}》{reason}", _RED)
            self._active_stage = 0

        elif cmd == "queue_done":
            _, cancelled, n_done = msg
            self._restore_idle_controls()
            self._cur_book_lbl.config(text="—", fg=_SUBINK)
            self._active_stage = 0
            if cancelled:
                self._set_detail(self._t("stopped"), _SUBINK)
                self._run_hint.config(text=self._t("hint_stopped"))
            else:
                self._set_detail(self._t("all_done", n=n_done), _GOLD)
                self._run_hint.config(text=self._t("hint_done", n=n_done))
                if n_done > 0:
                    threading.Thread(target=_play_done_sound, daemon=True).start()

        elif cmd == "fatal":
            self._restore_idle_controls()
            self._set_detail(msg[1], _RED)
            self._run_hint.config(text=self._t("hint_error"))

        elif cmd == "models_ok":
            _, ids = msg
            self.btn_fetch.config(state="normal")
            self._model_combo.config(values=ids)
            self._save_hint.config(text=self._t("models_ok", n=len(ids)))
            self.root.after(2500, lambda: self._save_hint.config(text=""))

        elif cmd == "models_fail":
            _, err = msg
            self.btn_fetch.config(state="normal")
            self._save_hint.config(text="")
            low = err.lower()
            if "401" in err or "authenticat" in low or "invalid api key" in low:
                self._set_detail(self._t("models_fail_auth"), _RED)
            else:
                self._set_detail(self._t("models_fail_net", err=err[:160]), _RED)

    def _build_done_row(self, book):
        """完成后在书名下追加 EPUB 路径行（可复制 + 打开所在文件夹）"""
        if book["extra"]:
            return
        book["extra"] = True
        frame = book["widgets"]["frame"]
        row = tk.Frame(frame, bg=_CARD)
        row.grid(row=1, column=0, columnspan=5, sticky="ew",
                 padx=(10, 8), pady=(0, 6))
        row.columnconfigure(0, weight=1)

        e = tk.Entry(row, bg=_CARD, fg=_SUBINK, relief="flat", bd=0,
                     font=self.fonts["small"], readonlybackground=_CARD)
        e.insert(0, book["epub"])
        e.config(state="readonly")
        e.grid(row=0, column=0, sticky="ew")

        def copy_path():
            self.root.clipboard_clear()
            self.root.clipboard_append(book["epub"])
            self._set_detail(self._t("copied"), _SUBINK)

        def open_folder():
            try:
                os.startfile(os.path.dirname(book["epub"]))
            except Exception as ex:
                self._set_detail(self._t("open_fail", err=ex), _RED)

        self._btn(row, self._t("copy"), copy_path, small=True
                  ).grid(row=0, column=1, padx=(6, 0))
        self._btn(row, self._t("open_folder"), open_folder, small=True
                  ).grid(row=0, column=2, padx=(6, 0))

    # ── 节拍：进度条缓动/爬行 + 卡片走时 ─────────────────────

    def _tick(self):
        try:
            if not self.root.winfo_exists():
                return
        except tk.TclError:
            return

        now = time.time()
        # 活跃阶段走时
        if self._active_stage and self._active_stage not in self._stage_done:
            start = self._stage_start.get(self._active_stage)
            if start and 0 < self._active_stage <= len(self._cards):
                self._cards[self._active_stage - 1]["timer"].config(
                    text=_fmt_elapsed(now - start))

        # 当前书耗时同步到队列行
        if self._current_idx is not None and self._current_idx < len(self.books):
            book = self.books[self._current_idx]
            if self.running and book["t0"] and not book["done"]:
                book["widgets"]["time"].config(
                    text=_fmt_elapsed(now - book["t0"]))

        # 无 fraction 时缓慢爬行（上限为该段 90%）
        if self.running and self._active_stage:
            base, ceiling = self._spans.get(self._active_stage, (0, 100))
            creep_cap = base + (ceiling - base) * 0.9
            if self._bar_target < creep_cap:
                self._bar_target = min(self._bar_target + 0.15, creep_cap)

        # 缓动逼近目标
        if self._bar_value < self._bar_target:
            self._bar_value = min(
                self._bar_value + max((self._bar_target - self._bar_value) * 0.25, 0.2),
                self._bar_target)
            self._redraw_bar()

        self._tick_id = self.root.after(200, self._tick)

    # ── 关闭 ─────────────────────────────────────────────────

    def _cancel_timers(self):
        """销毁前停掉 after() 轮询，避免退出时报 invalid command name"""
        for aid in (self._poll_id, self._tick_id):
            if aid:
                try:
                    self.root.after_cancel(aid)
                except Exception:
                    pass
        self._poll_id = self._tick_id = None

    def _on_close(self):
        if self.running:
            if not messagebox.askokcancel(self._t("msg_quit_t"),
                                          self._t("msg_quit")):
                return
            self.cancel.set()
        self.settings = self._collect_settings()
        _save_settings(self.settings)
        self._cancel_timers()
        self.root.destroy()

    # ── 自检截图 ─────────────────────────────────────────────

    def demo_fill(self):
        """--shot：预填两本假书并摆出进行中的姿态（不真跑转换）"""
        self._add_entry(r"F:\Books\城市与国家财富（经济生活的基本原则）.pdf", 549)
        self._add_entry(r"F:\Books\民法总论（第五版）.pdf", 638)

        def demo_state():
            spans = _compute_spans([max(549 * 0.80, 30),
                                    max(549 * 0.14, 15), 3.0])
            self._handle(("book_start", 0,
                          "城市与国家财富（经济生活的基本原则）", False, spans))
            self._handle(("book_status", 0, self._t("st_s1"), _GOLD_HI))
            self._handle(("stage_update", 0, 1, self._t("s1_name"),
                          self._t("demo_detail"), 0.62))
            self.books[0]["t0"] = time.time() - 83
            self._current_idx = 0
        self.root.after(500, demo_state)

    def grab_shot(self):
        try:
            from PIL import ImageGrab
            self.root.update_idletasks()
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(str(self._shot_path))
            print(f"shot saved: {self._shot_path} ({w}x{h})")
        except Exception as e:
            print(f"shot failed: {e}")
        finally:
            self._cancel_timers()
            self.root.after(150, self.root.destroy)


# ════════════════════════════════════════════════════════════

def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    shot_zh = "--shot" in sys.argv
    shot_en = "--shot-en" in sys.argv

    root = TkinterDnD.Tk()
    app = App(root, lang="en" if shot_en else None)
    if shot_en:
        app._shot_path = APP_DIR / "_app_shot_en.png"

    # 尺寸：按内容实际需要，钳制在屏幕内
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    w = max(960, root.winfo_reqwidth() + 8)
    h = min(root.winfo_reqheight() + 8, sh - 70)
    root.minsize(880, 700)
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{max((sh - h) // 2 - 20, 20)}")

    if shot_zh or shot_en:
        app.demo_fill()
        root.after(3000, app.grab_shot)

    root.mainloop()


if __name__ == "__main__":
    main()
