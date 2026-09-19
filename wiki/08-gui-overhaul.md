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

进度事件契约（病例 050 诚实化）：start 事件的 `engine` 为真实引擎 id
（mineru/paddleocr/vlm，不再是硬编码 "hybrid"），并携带 `stage_bounds`
（按预估耗时加权的阶段边界累计百分比，进度条刻度点按它画；缺失不画）；
progress 事件的 `detail` 进队列卡片常显详情行（如「VLM 阅读 120/549 页」）；
无真实 fraction 的阶段按预估时长配速爬行（爬满跨度 90% ≈ est 秒），
真实 fraction 到达即让位、缓动逼近不跳变。

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
│    队列列表：每本 引擎徽章（MinerU/PaddleOCR/VLM）+ 实时进度条
│      （刻度点=start 事件 stage_bounds 真实阶段边界）+ 阶段名
│      + 常显详情行（最新 detail）+ 日志展开 + 取消（进行中/待开始）
│      + 重试（失败/已取消：入队选项快照原样重跑，走 startAll 预检）
│      + 打开文件夹（done：产物文件夹 <输出>/<书名>/，含 epub/ md/ tex/）
├─ 产物库 Library
│    卡片流：书名 / 引擎徽标 / 格式徽标 epub·md·tex / 完成时间 / 耗时 /
│    丢失徽标（文件被手动移走时）/ 打开 / 在文件夹显示
│    点卡片 → 产物详情（库内就地展开/子页，不占导航位）：
│      元数据 + 产物清单（每格式一行：大小、路径、打开文件/打开文件夹；
│      在文件夹显示仅文件行——目录行 reveal 与 open 等价且旧实现必翻车，
│      病例 057）
│      + "从列表移除"（只删登记行，不动产物文件）
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
- **动效**（用户点名做齐）：条目/按钮 hover 主色低透明外发光（零位移——
  hover translateY 会在边界自激抖动，病例 050 移除）、按钮 hover 亮度
  过渡、页面切换 150ms 透明度/位移、进度条细轨+主色填充+阶段步进点；
  拒绝弹跳。所有图标按钮带 Tooltip；操作反馈走 toast（成功/失败/警告三态，右上角）。
- **动效铁则（病例 051 由 050 单点教训通则化）**：hover/focus/active 等
  状态样式**只许动元素自身视觉，绝不允许动版面或命中区域**。
  - 允许：`color` / `background(-color)` / `border-color` / `box-shadow` /
    `filter` / `opacity` / `outline*` / `cursor` / `text-decoration` /
    `fill` / `stroke` / `caret-color` / `accent-color` / `visibility`；
    以及 hover 下 **scale(1~1.03) 微放大**（放大命中区域，不会自激）。
  - 禁止：① `transform: translate*`（位移让命中区域逃离光标 → 050 式
    自激抖动）；② `width/height/padding/margin/max-* /gap/flex-basis/
    字号/字重/字距/行高/边框宽度` 等布局属性（把兄弟元素顶开）；
    ③ hover 条件渲染新节点（含 `display` 切换、`hidden↔block`）撑版面。
  - 豁免：**覆盖层**（tooltip、下拉面板、toast 等 `absolute/fixed` 定位、
    不占文档流的浮层）与**一次性入场动画**（如 page-in 的 6px 淡入位移，
    不与 hover/焦点联动、无反馈回路）不受此限；点击驱动的状态动画
    （开关滑块、折叠侧栏、展开日志）亦不在此列——铁则只管 hover/focus
    这类指针驻留状态。
  - 守卫：`gui/src/lib/__tests__/styles-no-layout-thrash.test.ts`
    静态断言 styles.css 状态伪类块只含白名单属性、tsx 状态 variant
    不驱动布局工具类；E2E：`scripts/cdp_case051_hover_audit.mjs`
    真实鼠标悬停断言命中矩形逐像素不变（含 050 复现几何的边缘采样）。
- **图标**：Lucide 线性图标。空态：线条风插画位 + 一句引导文案。

## 4. 关键机制设计

- **产物登记处（管线侧唯一新耦合点，已实现）**：pipeline 每完成一本，向
  `<输出目录>/_registry.jsonl` 追加一行（v/ts/title/source_pdf/work_dir/
  engine/ocr/translate/formats/products{fmt:[paths]}/elapsed_s/app_version）。
  失败方向=不动作（登记失败只 warn，绝不影响转换）。**产物库语义 = 纯登记制
  （病例 053 用户拍板）**：登记文件是唯一数据来源——产物库只读
  outputDir + historyDirs 里的 `_registry.jsonl`（`registryDirsOf` 纯函数
  组装），**不做任何目录扫描**（052 的「未登记扫描」曾把 D:\temp_files 通用
  临时目录里的本机文档误判进产物库；扫描/忽略名单/重新定位全链已移除）。
  historyDirs 在设置载入时自动剔除仓库内暂存区（_regress/output/.tmp-*）
  与已不存在目录；addHistoryDir 拒绝仓库内路径。删除语义：已登记记录卡片/
  详情页均可「删除记录」（只删 registry 行，不动产物文件，remove_entry 带
  mtime 重读重试的并发加固；RegistryEntry 经 serde flatten extra 保未知
  字段）。旧设置文件里已持久化的 scanDirs/ignoredPaths 字段加载时静默
  丢弃（不炸、不报错，下次保存落盘即清）。
- **手动改动追踪（用户问：手动移走文件是不是就追丢了？）**：是，而且按
  053 裁定**不追踪**——registry 记的是绝对路径，用户在资源管理器里移动/
  改名/删除是用户自己的行为；产物库每次展示时校验存在性，丢失卡片标灰 +
  "丢失"徽标，剩下可做的只有「从列表移除」与「再次转换」。不提供重新定位，
  不做跨盘搜索（贵且不可靠）。
- **任务通道**：每本一个 sidecar 子进程（`pipeline.py --headless`），
  Rust 端按行读 JSON 事件 → 进度 UI；取消 = kill 进程树；日志落
  `_batch_logs/`（沿用）。多本并发上限可调（默认 1，VLM 引擎内部已有
  workers=4 并发，外层不建议再叠）。
- **设置落点**：Tauri 配置目录下 `settings.json`（v2 schema，与旧
  `gui_settings.json` 分离；旧 app.py 冻结后不再读新文件）。**多实例隔离
  （病例 052）**：debug 构建（tauri dev）落 `settings.dev.json`、release
  落 `settings.json`，dev 冒烟不再污染正式配置；dev 文件缺失时一次性继承
  正式配置（密钥免重填）。**聚焦重载**：窗口 focus 时经
  read_settings_state 比对 mtime/内容，磁盘比内存新才重载，解析/读取失败
  保持内存（铁律 0）。sidecar 拉起
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
  `assets/complete.wav` + `CONVERT_COMPLETE_SOUND`）；失败音同模式
  （FIXLOG 054，`assets/fail.wav` + `CONVERT_FAIL_SOUND`，接线在
  pipeline 失败路径与旧 tkinter app.py，用户取消不响）。
- **病例 050 显示层诚实化**（2026-09-19）：start 事件 engine 真实化 +
  `stage_bounds`；队列卡片引擎徽章 + 常显详情行；hover 位移自激修复；
  MinerU est 收窄 2.0 s/页、headless 爬行按预估配速；「再次转换」toast
  带引擎名。详见 FIXLOG 病例 050。
- **病例 051 动效铁则通则化**（2026-09-19）：050 单点教训升级为全 GUI
  悬停审计 + §3 动效铁则（状态样式只动自身视觉）+ vitest 静态守卫
  （styles-no-layout-thrash）+ CDP 悬停矩形逐像素断言。审计结论：
  050 后全库无新增违例。详见 FIXLOG 病例 051。
- **病例 052 多实例与产物库批次**（2026-09-20）：dev/release 配置文件分叉
  （settings.dev.json）+ 窗口聚焦重载，根治多实例内存分叉互踩；startAll
  起跑前 toast 引擎汇总（入队快照从此可见）；产物库去污（未登记扫描只吃
  显式 scanDirs，historyDirs 自动剔除仓库内/不存在目录）；卡片级「删除
  记录」（不删产物文件）与未登记「不再显示」（ignoredPaths 可恢复）；
  registry.rs 并发加固 + flatten extra 防丢字段。050「再次转换」归因
  证伪修正。详见 FIXLOG 病例 052。
- **病例 053 产物库纯登记制**（2026-09-19）：产品决策——「扫描旧产物
  目录」功能鸡肋且易误判（052 去污只是止血），全链移除：
  scan_unregistered/relocate_entry command、Settings.scanDirs/ignoredPaths
  及配套 UI（未登记区块/忽略 chips/扫描目录 chips/重新定位按钮）。
  产物库只剩登记记录 + 丢失徽标 + 删除记录 + 再次转换；登记文件是唯一
  数据来源（outputDir + historyDirs）。052 的扫描/忽略条目随之作废。
  详见 FIXLOG 病例 053。
- **病例 054 队列重试 + 失败音**（2026-09-20）：队列卡片对失败/已取消
  任务加「重试」icon-btn——queueStore.retry 就地重置回 queued（不新建
  卡片、选项快照原样保留），走 startAll 标准路径（052 预检照旧，预检
  不过重新置 error）；toast「已重新入队」反馈。失败提示音
  play_failure_sound（assets/fail.wav）接线 pipeline 三处失败路径
  （PDF 缺失早退/非零 SystemExit/未捕异常）。同病例修复 stage1_mineru
  run_tag 作用域崩溃（047 回归，切片大书 finally 必炸）与 Stage 1 误导性
  错误提示（环境类才走运维清单，意料外报「疑似程序 bug」+ traceback
  摘要），并连带修复 stage3_export 空壳引擎目录抢走缓存位的存量 bug。
  详见 FIXLOG 病例 054。
- **病例 055 done 按钮语义：打开 EPUB → 打开文件夹**（2026-09-20，用户
  裁定）：done 卡片按钮改为打开产物文件夹（<输出>/<书名>/，内含
  epub/ md/ tex/）；done 事件新增 product_dir（实际交付目录，撞名避让
  感知），前端缺字段时 epub_path 上溯两级兜底。连带修复 lib.rs
  open_file 对目录经 cmd start 不弹可见窗口的实弹坑（目录改 explorer.exe
  直开）。详见 FIXLOG 病例 055。
- **病例 057 详情页按钮直义化**（2026-09-20）：FileRow 按钮语义=
  打开文件/打开文件夹；目录行不再渲染「在文件夹显示」（与 open 等价
  且旧实现 explorer /select 目录必翻车回落「文档」）。连带修复更大的
  存量坑：`explorer /select,"<path>"` 带引号单参数形态对文件也翻车，
  改 /select, 与路径分两个 argv。详见 FIXLOG 病例 057。
