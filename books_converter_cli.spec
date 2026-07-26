# -*- mode: python ; coding: utf-8 -*-
# Books_Converter headless CLI — SageRead sidecar 打包配置
# 构建: .venv\Scripts\pyinstaller books_converter_cli.spec --noconfirm
a = Analysis(
    ['pipeline.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['mineru', 'fitz', 'openai', 'ebooklib', 'latex2mathml', 'stage4_translate'],
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
