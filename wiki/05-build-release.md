# 05 构建与发布

## 版本号

`version.py` 的 `__version__` 是 CLI/管线唯一版本源。**GUI 发版时同步
`gui/src-tauri/tauri.conf.json` 与 `gui/package.json` 的 `version`**
（安装包文件名与关于页版本取自 tauri.conf.json，漏改则产物名沿用旧版）。
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

## Tauri GUI 发布链（病例 060 落地）

管线随 GUI 安装包走 **externalBin sidecar**：`tauri.conf.json` 的
`bundle.externalBin = ["binaries/books_converter"]`，实体文件
`gui/src-tauri/binaries/books_converter-x86_64-pc-windows-msvc.exe`
（即 PyInstaller 产物的改名副本，构建前必须刷新；`gui/src-tauri/binaries/`
已入 .gitignore，二进制不入库）。

```bash
# ① 先打管线（上节）→ ② 同步 sidecar 实体 → ③ 打 GUI
.venv/Scripts/pyinstaller books_converter_cli.spec --noconfirm
cp dist/books_converter.exe \
  gui/src-tauri/binaries/books_converter-x86_64-pc-windows-msvc.exe
cd gui && pnpm tauri build
# 产物：gui/src-tauri/target/release/bundle/{nsis,msi}/…（sidecar 随包落主 exe 旁）
```

绿色 zip 组装（dist/ 命名约定）：

```bash
mkdir -p Books_Converter
cp gui/src-tauri/target/release/books-converter-gui.exe "Books_Converter/Books Converter.exe"
cp gui/src-tauri/target/release/books_converter.exe    "Books_Converter/books_converter.exe"
zip -r dist/Books_Converter-vX.Y.Z-win64.zip Books_Converter
```

**命名注意**：主 exe 不能命名 `Books_Converter.exe`——Windows 文件系统
大小写不敏感，与 `books_converter.exe` 同名互覆（实测踩过）。用带空格的
`Books Converter.exe`（对齐 productName）。

运行时模式解析（`gui/src-tauri/src/sidecar.rs` `resolve_pipeline`）：
主 exe 旁有 `books_converter.exe` → 发布模式直拉（事件协议不变）；
否则回退开发模式（仓库 `.venv` + `pipeline.py`）；两者都无 → 明确报错
不动作。`BC_FORCE_DEV_PIPELINE=1` 强制开发模式——**dev 验收实例启动
必须带它**（tauri dev 会把 externalBin 拷进 target/debug，不强制则 dev
静默走发布模式跑旧 exe，「你跑的是旧实例」的变体）。日志落点：发布
模式 `%APPDATA%\com.booksconverter.app\logs\`，开发模式照旧
`_batch_logs/`。

验收：沙盒实测（`_regress/case060-sandbox/` + scripts/cdp_case060_sandbox
系）——zip 结构解到仓库外，GUI 走完整转换，日志落 APPDATA 即发布模式
铁证。WebView2 调试端口注意：同一 user-data-folder 共享浏览器进程，
沙盒实例要设 `WEBVIEW2_USER_DATA_FOLDER` 隔开（见 launch-cdp.bat）。

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
