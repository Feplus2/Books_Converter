# -*- mode: python ; coding: utf-8 -*-
# Books_Converter headless CLI — SageRead sidecar 打包配置
# 构建: .venv\Scripts\pyinstaller books_converter_cli.spec --noconfirm
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ['pipeline.py'],
    pathex=[],
    binaries=[],
    # latex2mathml 的符号表 unimathsymbols.txt（symbols_parser 运行时 open 读取；
    # hiddenimports 只收模块不收数据文件——漏打后每条公式转 MathML 都抛
    # FileNotFoundError 被 _latex_to_mathml 吞掉，全书公式退化为 <code> 裸
    # LaTeX 源码。QFT 两本书实测事故，2026-08-14）
    datas=collect_data_files('latex2mathml'),
    hiddenimports=['mineru', 'fitz', 'openai', 'ebooklib', 'latex2mathml', 'stage4_translate',
                   # ocr_provider 用 importlib 懒加载，静态分析扫不到，必须显式列出
                   'ocr_provider', 'stage1_mineru_provider', 'stage1_paddleocr',
                   'stage1_layout', 'updater', 'version', 'requests'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'tkinterdnd2', 'torch', 'transformers', 'accelerate'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='books_converter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,   # headless CLI 需要 stdout 管道；Tauri sidecar 会隐藏窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
