# 05 构建与发布

## 版本号

`version.py` 的 `__version__` 是唯一版本源（GUI 与 CLI 共用）。
改动发版级别修复时递增。

发布稿文案：用户向 changelog 写在仓库根 `RELEASE_NOTES.md`（顶部加
新一节；草稿节标「未发布」），README.md 的功能/截图随版本同步更新
（配图在 `docs/images/`，相对路径引用）。

## PyInstaller 打包

```bash
cd F:\MyProjects\Books_Converter
.venv/Scripts/pyinstaller books_converter_cli.spec --noconfirm
# 产物：dist\books_converter.exe（onefile，约 61MB；无 torch/tkinter）
```

spec 要点：`datas` 只收 `latex2mathml` 包数据——**不打包工作目录/测试
PDF/缓存/产物**（`_regress/`、`output/` 等也不会进包，另有 gitignore
兜底不入库）。`hiddenimports` 含 `stage4_translate` 等，新增模块若打包后
报隐式导入错，补进 `hiddenimports`。

## SageRead sidecar 部署

SageRead 以 sidecar 形式捆绑本 exe（`tauri.conf.json` 的 externalBin）：

```bash
cp dist/books_converter.exe \
  F:\MyProjects\SageRead\packages\app\src-tauri\binaries\books_converter-x86_64-pc-windows-msvc.exe
# dev 模式运行时实际解析 target/debug 下的副本，同步替换（不依赖 cargo 重编）：
cp dist/books_converter.exe \
  F:\MyProjects\SageRead\packages\app\src-tauri\target\debug\books_converter.exe
```

替换后重启 SageRead dev 实例生效；发布安装包随 `pnpm tauri build` 自动打。
sidecar 协议（stdout 逐行 JSON：start/progress/stage_done/done/error，
`ensure_ascii=True` 防 GBK 管道乱码）详见 SageRead 的
`docs/archive/books-converter-integration.md`。

## 卫生检查（push/打包前）

```bash
git status --short        # 应只剩源码/文档/tests 改动
git check-ignore -v _regress output _batch_logs .env gui_settings.json
```

`.gitignore` 必须含：`.env`、`gui_settings.json`、`output/`、`_batch_logs/`、
`_regress/`、`.tmp-*/`、`*.epub`、`build/`、`dist/`、`.venv/`、`models/`。
