# -*- mode: python ; coding: utf-8 -*-
# Books_Converter macOS 打包配置（GUI .app）
# 构建: pyinstaller Books_Converter_mac.spec --noconfirm
from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = []
binaries = []
hiddenimports = []
# tkinterdnd2 的 tkdnd 原生库（osx-arm64 等）必须随包分发，否则拖拽功能失效
tmp_ret = collect_all('tkinterdnd2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# ocr_provider 用 importlib 懒加载 provider，静态分析扫不到，必须显式列出
hiddenimports += ['ocr_provider', 'stage1_mineru_provider', 'stage1_paddleocr',
                  'stage1_layout', 'updater', 'version', 'requests',
                  'stage4_translate', 'llm_thinking', 'progress_ui',
                  'progress_headless', 'qc_book',
                  # latex2mathml 在 stage3 函数内懒导入，且带运行时数据文件
                  # unimathsymbols.txt（漏打则每条公式转 MathML 都失败退 <code> 裸源码，
                  # 见 FIXLOG 病例 013）
                  'latex2mathml']
datas += collect_data_files('latex2mathml')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'transformers', 'accelerate'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Books_Converter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Books_Converter',
)
app = BUNDLE(
    coll,
    name='Books_Converter.app',
    bundle_identifier='com.feplus2.books-converter',
)
