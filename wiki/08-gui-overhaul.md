# 08 GUI 重做方案（网页套壳 · 现代化）— 对齐稿 v2（已拍板，施工中）

> 目标（用户原话汇总）：一改 tkinter 旧风格，做"像样的、现代化的软件"——
> 网页套壳；统一设置页（token/API、检查更新、默认选项、明暗模式）；
> 详细转换选项页；已转换产物列表（指引路径/找回）；科技、简约、现代。
> 原则：**管线一行不动**（唯一例外：§4 产物登记处一行 JSONL），GUI 只是管线的一层皮。
>
> v2 修订（2026-09-16 用户拍板）：布局改为转换优先；产物详情并入产物库；
> 补输出地址选择；设置页模型管理分 OCR / LLM·VLM 两组并搬 SageRead 提供商页
> 模式与 vision-map；消灭豆包图片定位选项（代码侧已于同日移除）；动效与
> Tooltip/toast 做齐；registry 手动改动追踪对策。技术路线 A（Tauri）+ React/TS/Vite 确认。

## 1. 技术选型（已定 A）

**Tauri 2 + sidecar 管线**：Rust 壳 + WebView2 前端；
开发期 sidecar = `.venv/Scripts/python.exe pipeline.py --headless`，
发布期 = `books_converter_cli.exe`（同协议）。决定性理由：管线与 GUI 的边界
已是协议化的（`--headless` 每行一个 JSON 事件：start/progress/stage_done/
done/error——SageRead sidecar 消费的就是它），Tauri 壳只做任务拉起、事件
订阅、产物管理、设置下发。前端栈 **React + TypeScript + Vite + Tailwind +
lucide-react**（与 SageRead 同源，组件/样式可借鉴，不复制）。

## 2. 信息架构（v2 布局）

```
左侧竖排图标栏（可收起为纯图标）：
  ┌──────────┐
  │ 转换      │ ← 顶位，应用打开即此页
  │ 产物库    │
  │   …弹性空间… │
  │ 设置      │ ← 钉在左下角
  └──────────┘
主区域 = 当前栏内容：
├─ 转换 Convert（默认首页）
│    拖放区（多本排队）+ 选项分组（详细、带说明文字）：
│      引擎（MinerU / PaddleOCR / VLM）· OCR 开关 · 翻译（目标语言）
│      输出地址（目录选择器；默认读设置页的默认输出目录）
│      格式多选 epub/md/tex（md 单文件/分章、tex 完整/片段、语言 auto/orig/trans/both）
│      VLM 组：转写模型（仅列已激活且 vision-map 判定支持视觉的型号）、
│              思考档（off/low/medium/high）、并发 workers
│              ——图片定位无选项：恒为同模型粗框+光栅重裁（豆包已消灭）
│    队列列表：每本实时进度条 + 阶段名 + 日志展开 + 取消
├─ 产物库 Library
│    卡片流：书名 / 引擎徽标 / 格式徽标 epub·md·tex / 完成时间 / 耗时 /
│    丢失徽标（文件被手动移走时）/ 打开 / 在文件夹显示
│    点卡片 → 产物详情（库内就地展开/子页，不占导航位）：
│      元数据 + 产物清单（每格式一行：大小、路径、打开/在文件夹显示）
│      + 丢失项"重新定位"（文件选择器重指）与"从列表移除"
│    顶栏：搜索、按格式/引擎过滤、"再次转换"（带原选项一键重跑）
└─ 设置 Settings（左下角齿轮，分组页）
     ① 模型与密钥（分两组）：
        · OCR 引擎组：MinerU token、PaddleOCR token + API URL（测试连接）
        · LLM/VLM 提供商组（搬 SageRead 模式）：提供商列表
          （智谱 z.ai / 智谱 bigmodel / DeepSeek / 阿里 DashScope /
           Kimi Moonshot / 自定义 OpenAI 兼容）→ 选提供商 → 填 API key
          → 测试连接 → 激活模型（输入型号 id，vision-map 判定是否视觉，
          徽标显示）；**仅激活型号出现在转换页下拉**；VLM 转写模型
          只列视觉型号，Stage 2/4 文本 LLM 可列全部激活型号
     ② 默认选项（引擎/格式/语言/输出目录/workers，与 .env 同语义）
     ③ 外观（明/暗/跟随系统）
     ④ 更新与关于（检查更新 + 版本 + 开源协议）
```

## 3. 视觉系统（科技 · 简约 · iOS 式精致）

- **色板**（明暗双主题，CSS 变量一键切换）：
  亮：背景 #F6F7F9、卡片 #FFFFFF、主文字 #16181D、次文字 #6B7280、
  主色 科技蓝 #2563EB、QC 绿 #16A34A / 黄 #D97706 / 红 #DC2626；
  暗：背景 #0E1116、卡片 #171B22、主文字 #E7E9EC、主色 #3B82F6。
- **字体**：UI = Inter / 中文 PingFang/微软雅黑回退；数字/进度 = JetBrains Mono；
  圆角 10px，卡片 1px 边线无阴影（暗主题无边线用 +4% 亮面）。
- **动效**（用户点名做齐）：条目 hover 微浮起 + 科技光晕（主色低透明
  外发光）、按钮 hover 亮度过渡、页面切换 150ms 透明度/位移、进度条
  细轨+主色填充+阶段步进点；拒绝弹跳。所有图标按钮带 Tooltip；
  操作反馈走 toast（成功/失败/警告三态，右上角）。
- **图标**：Lucide 线性图标。空态：线条风插画位 + 一句引导文案。

## 4. 关键机制设计

- **产物登记处（管线侧唯一新耦合点，已实现）**：pipeline 每完成一本，向
  `<输出目录>/_registry.jsonl` 追加一行（v/ts/title/source_pdf/work_dir/
  engine/ocr/translate/formats/products{fmt:[paths]}/elapsed_s/app_version）。
  失败方向=不动作（登记失败只 warn，绝不影响转换）。产物库 = 读 registry
  + 按输出目录扫描兜底（旧产物无登记也能列）。
- **手动改动追踪（用户问：手动移走文件是不是就追丢了？）**：是，registry
  记的是绝对路径，用户在资源管理器里移动/改名/删除后原路径失效——
  对策：产物库每次展示时校验存在性，丢失卡片标灰 + "丢失"徽标；
  详情页提供"重新定位"（文件选择器指到新路径，回写 registry 该行）
  与"从列表移除"。不尝试跨盘自动搜索（贵且不可靠）。
- **任务通道**：每本一个 sidecar 子进程（`pipeline.py --headless`），
  Rust 端按行读 JSON 事件 → 进度 UI；取消 = kill 进程树；日志落
  `_batch_logs/`（沿用）。多本并发上限可调（默认 1，VLM 引擎内部已有
  workers=4 并发，外层不建议再叠）。
- **设置落点**：Tauri 配置目录下 `settings.json`（v2 schema，与旧
  `gui_settings.json` 分离；旧 app.py 冻结后不再读新文件）。sidecar 拉起
  时按设置注入环境变量：OCR_PROVIDER / MINERU_TOKEN / PADDLEOCR_TOKEN /
  PADDLEOCR_API_URL / VLM_API_KEY / VLM_BASE_URL / VLM_MODEL /
  VLM_REASONING / VLM_WORKERS / DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL /
  DEEPSEEK_MODEL（Stage 2/4 文本 LLM）。
- **vision-map**：从 SageRead `packages/app/src/ai/providers/vision-map.ts`
  逐行移植（精确型号表 + canonicalSlug 归一 + 未收录默认放行），落
  `gui/src/lib/vision-map.ts`；新型号核实后加行，与 SageRead 同步维护。
- **检查更新**：沿用 `updater.py`（GitHub release 轮询），设置页显化；
  GitHub 不可达时 toast 明确报错而非转圈。
- **i18n**：界面文案先全中文，集中一个文案文件，留 EN 扩展位。

## 5. 迁移与分期

| 期 | 内容 | 产出 |
|---|---|---|
| M1 骨架+产物库+转换 | Tauri 壳、v2 布局、sidecar 拉起、进度协议、产物库+registry+详情、设置存储 | 可跑通转换全程并看产物 |
| M2 设置页全量 | 提供商/密钥管理+测试连接+模型激活、默认选项、明暗主题、检查更新 | 功能对齐旧 GUI 并超越 |
| M3 打磨 | 动效/光效全量、Tooltip/toast 补齐、空态、"再次转换"、i18n 位 | 发布候选 |
| M4 打包发布 | MSI/zip、books_converter_cli.exe sidecar 随附、自动更新链 | v2.0.0 |

旧 `app.py`（tkinter）M1 起冻结维护，M4 后删除。管线/CLI/headless 协议
保持不变（SageRead sidecar 不受影响）。

## 6. 里程碑状态

- 2026-09-16 v2 对齐稿拍板，M1 开工。
- 前置已完成：豆包图片定位选项从管线移除（config/stage1_vlm/.env.example，
  全测试链 + 真实页段回归通过，FIXLOG 039）；SageRead vision-map.ts 已逐行
  移植进 `gui/src/lib/vision-map.ts`。
- **M1+M2 已落地**（同日）：Tauri 壳、v2 布局、sidecar 拉起+事件协议、
  产物库（registry 汇总/丢失标灰/重新定位/移除）、设置页（6 提供商卡片、
  测试连接、模型激活）、明暗主题、hover 光晕/Tooltip/toast。dev 端口 1520
  （SageRead 占 1420）。产物登记处已在管线侧实现（pipeline.py
  `_register_product` → `<输出>/_registry.jsonl`，FIXLOG 旁证，测试
  tests/test_pipeline_registry.py）。
- **v2 验收反馈迭代中**（同日，用户 6 条）：导航开合键置顶；转换页改
  规则/VLM 两模式（规则=解析模型+OCR 开关+后处理模型，VLM=视觉模型+
  思考档，后处理同源注入 DEEPSEEK_*）；设置页加子侧栏（解析模型/大模型
  提供商/选项三块，提供商两级页 SageRead 式）；UX 硬伤包（开始转换按钮、
  缺 key 预检+指引、取消修复、toast 治理+通知面板、报错人话化、日志落
  _batch_logs）；"stage2/4 文本模型"改名"后处理模型"；新增手册页。
- 提示音：`winsound.Beep` 根因确诊，换异步 WAV（FIXLOG 040，
  `assets/complete.wav` + `CONVERT_COMPLETE_SOUND`）。
