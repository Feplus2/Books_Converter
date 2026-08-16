# Books_Converter · 扫描书一键成册

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Release](https://img.shields.io/github/v/release/Feplus2/Books_Converter)](https://github.com/Feplus2/Books_Converter/releases)

扫描书 PDF 一键转成**真正的电子书**：多级目录树、可点击脚注、MathML 公式、
跨页断段拼接——还可以顺手**翻译成中文**。全部走云端 API，本地无需 GPU。

```
PDF → 解析引擎（MinerU / PaddleOCR-VL 云 API，OCR + 版面分析）
    → 结构重建（标题层级 / 跨页拼接 / 跨页表格 / 图注归属）
    → EPUB 装订（嵌套目录 / MathML / 尾注 / 封面）
    → 可选：全书翻译（DeepSeek 文学翻译，any to any）
```

> 双引擎统一契约：无论用哪个引擎，Stage 1 都产出同一套 MinerU 风格
> `content_list`（含 0-1000 千分位 bbox），下游管线零改动。
> 引擎特性见 [FAQ](#faq)。

## 实测成绩

| 书 | 页数 | 耗时 | 成本 | 结果 |
|---|---|---|---|---|
| 民法总论（7 层深结构） | 561 | **76 s** | ≈¥0.15 | 六编十五章全对，874 条脚注可点 |
| 高等数学·上册 | 442 | 6.5 min | ≈¥0.2 | 10148 个公式转 MathML，习题编号零误判 |
| The German Ideology（英文） | 592 | 10 min | ≈¥0.2 | Stirner 戏仿结构还原，731 条字母脚注 |
| Harold Fry（英文小说） | 355 | 15 min | ≈¥1 | 全本文学翻译，人名全书统一 |

> 耗时大头是 MinerU 云端 OCR 排队；结构重建本身 561 页只需 76 秒。
> 测试环境：RTX 5070Ti 笔记本 + 家庭宽带。

## 功能特性

- **双解析引擎**：MinerU（表格密集书首选）与 PaddleOCR-VL-1.6（速度快、
  图注绑定准、免费 3000 页/天）一键切换；PaddleOCR 的 Unicode 上下标
  （LiCoO₂、H₂O）自动转回 LaTeX
- **检查更新**：GUI 设置页一键检查 GitHub 新版本，CLI 用 `--check-update`
- **结构重建**：编/章/节多级嵌套目录；标题层级由 *TOC 锚点 + 形状栈*
  纯代码定音，深如《民法总论》的七层嵌套（编→章→节→一、→（一）→1.→(1)）全部归位
- **跨页修复**：断开的段落自动接回（含英文断词 dehyphen）、跨页表格语义合并
  （表头去重、rowspan 修正）、图注/表注自动归属
- **页脚注恢复**：正文①锚点 ↔ 页脚注配对，渲染为可点击尾注（附返回链接），
  配不上的挂章尾，内容零丢失
- **公式转 MathML**：`$…$`/`$$…$$` 批量转换（实测 99.98% 成功），
  失败的保留 LaTeX 源码兜底；EPUB 3 规范
- **全书翻译**：按阅读顺序分批（~6000 字符 + 前一条上下文 + 滚动译名表），
  人名地名全书统一；支持中/英/日/法/德/西/韩目标语言；断点续翻
- **扫描缺陷自愈**：重复扫描的页面自动检测丢弃、编分隔页自动救援、
  目录页密度检测防边界泄漏、章标题被 OCR 漏识时按目录页码回补
- **GUI 前端**：拖拽多本 PDF、密钥设置、队列进度、中英双语界面、真实进度条

## 快速开始

### 方式 A：免安装绿色版（推荐给非技术用户）

1. 到 [Releases](https://github.com/Feplus2/Books_Converter/releases) 下载最新版
   `Books_Converter-vX.Y.Z-win64.zip`（约 65MB）
2. 解压，双击 `Books_Converter.exe`
   （首次运行 Windows SmartScreen 会提示"未知发布者"→ 更多信息 → 仍要运行）
3. 在设置里填两个 Key（见下），把 PDF 拖进窗口，点"开始转换"

### 方式 B：源码运行

```bash
git clone https://github.com/Feplus2/Books_Converter.git
cd Books_Converter

# Python 3.10+，推荐 uv
uv venv && uv pip install -r requirements.txt
# 或: python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt

# 双击 启动.bat 打开 GUI，或走命令行：
python pipeline.py "D:\books\我的书.pdf"
```

### 需要的 Key（都有免费额度）

| Key | 获取 | 用途 |
|---|---|---|
| MinerU Token | https://mineru.net/apiManage/token | 默认解析引擎（免费 1000 页/日） |
| PaddleOCR Token | https://ai.baidu.com/ai-doc/AISTUDIO/fml7mozw5 | 备选解析引擎（免费 3000 页/天，选 PaddleOCR 时才需要） |
| LLM API Key | https://platform.deepseek.com/api_keys | 结构重建 + 翻译（约 ¥0.2/本） |

LLM 配置兼容**任何 OpenAI 协议端点**（DeepSeek/通义/智谱/OpenAI 官方/本地 vLLM），
GUI 里填 Base URL 和 Key 后可一键拉取模型列表。

## GUI 使用指南

1. **拖入 PDF**：可多本，自动去重，串行转换
2. **设置**：
   - 解析引擎：MinerU（默认）/ PaddleOCR-VL；对应 Token 填一个即可
   - LLM Base URL / LLM API Key / LLM Model
   - 强制 OCR：扫描书必开；出版社出的**文字版 PDF 关掉更快**
     （关掉后 MinerU 直接抽文字层，不做图像识别，速度翻倍）
   - 翻译为 [语言]：勾选后全书翻译为目标语言
   - 界面语言：中文 / English
   - 检查更新：一键对比 GitHub 最新 Release，有新版本可跳转下载页
3. **开始转换**：壹·PDF 解析 → 贰·结构重建 →（叁·翻译）→ 肆·EPUB，
   进度条按实测速率校准
4. **验收**：完成后显示 EPUB 路径，一键打开所在文件夹

## 命令行参考

```bash
python pipeline.py book.pdf                     # 标准转换（默认引擎 MinerU）
python pipeline.py book.pdf --engine paddleocr  # 用 PaddleOCR-VL 解析
python pipeline.py book.pdf --translate         # 转换 + 翻译成中文
python pipeline.py book.pdf --translate ja      # 翻译成日语
python pipeline.py book.pdf --no-ocr            # 文字版 PDF（跳过图像 OCR）
python pipeline.py book.pdf --max-pages 60      # 只转前 60 页（快速预览）
python pipeline.py book.pdf --skip-mineru       # 复用已有 Stage 1 结果（两种引擎均可）
python pipeline.py book.pdf --skip-deepseek     # 复用已有 structure.json
python pipeline.py --version                    # 打印版本号
python pipeline.py --check-update               # 检查 GitHub 新版本
```

Agent 调用请参阅 [SKILL.md](SKILL.md)；设计文档与踩坑记录见 [PROJECT.md](PROJECT.md)；
兜底策略病例（每本翻车书的根因与修复）见 [FIXLOG.md](FIXLOG.md)；
结构体检工具见 `qc_book.py`。

## 工作原理

### 为什么不需要本地大模型

MinerU 云 API 已经把每页看懂了（文字、坐标、类型、图、表、公式）。
剩下的工作是"把页与页之间的关系理顺"——这是四个**局部问题**：

| 任务 | 判断内容 |
|---|---|
| 跨页段落拼接 | 这两段是被页码切断的同一段吗？ |
| 标题检测 | 这个短文本是结构标题，还是题目/列表项？ |
| 图文关联 | 这句"图 2-1"说明归哪张图？ |
| 跨页表格合并 | 这两个半截表是同一张吗？ |

候选由纯规则筛出（vendor 自 MinerU-Popo 的启发式框架），
交给云端 LLM 逐批判断（每本约 ¥0.05）。

### 层级定音：TOC 锚点 + 形状栈

小模型/LLM 的分块层级判断会**漂移**（实测 MinerU-Popo 在深结构书上
漂到 L15）。我们的办法：

1. **TOC 锚点**：LLM 只读目录几页，拿到这本书自己的层级真值
   （编=L1、章=L2、节=L3），正文标题匹配上目录就锁死层级
2. **形状栈**：目录没收录的小标题（一、（一）/1. …）按编号形状的
   相对深度走经典大纲栈推理——同形同级、深形嵌套、浅形出栈

每个标题的级别 = 目录真值 + 邻近标题的相对关系，**误差零累计**。

### 翻译不是机翻

- 按阅读顺序组 ~6000 字符批次，附前一条上下文，4 批并发
- 每批注入书名/作者 + 滚动译名表（DeepSeek 每批回填新术语），
  人名地名全书一致
- 失败批次自动拆半重试；translations.json 断点续翻

## FAQ

**Q：两个解析引擎怎么选？**
默认 MinerU 即可，它在表格密集的书上最强（跨页表合并、rowspan 修正）。
PaddleOCR-VL-1.6 速度更快、每日免费额度更高（3000 页/天）、图注绑定更准，
适合图多表少的书；其跨页续表合并目前弱于 MinerU。两引擎产出同一套
content_list 契约，下游结构重建/翻译/装订完全共用，可随时换引擎重跑
（`--engine paddleocr`，Stage 1 结果各自缓存在 `mineru/`、`paddleocr/` 目录）。

**Q：文字版 PDF 怎么处理？**
GUI/CLI 关闭 OCR 即可（`--no-ocr`）。MinerU 会直接抽文字层，不做图像识别，
快得多；版面/表格/公式识别不受影响。

**Q：MathML 在哪些阅读器能看？**
calibre、Apple Books、Thorium 正常渲染；Kindle 不支持（显示 LaTeX 源码兜底文本）。
此为本项目的格式限制，不是 bug。

**Q：没有目录的书怎么办？**
照样转（如小说）。没有锚点时全靠形状栈的通用编号先验；
完全无编号标题体系会偏保守，但内容零丢失。

**Q：成本多少？**
MinerU 免费（1000 页/日高优先级）。DeepSeek：结构重建 ≈¥0.05/本，
翻译 ≈¥0.5-1/本（视字数）。

**Q：macOS / Linux 能用吗？**
管线本身跨平台（纯 Python），GUI 与打包目前在 Windows 上验证，
其他平台未测，欢迎反馈。

## 项目结构

```
app.py               # GUI 前端（拖拽多书/密钥设置/队列进度/中英双语/检查更新）
启动.bat             # 一键启动 GUI
pipeline.py          # 命令行主流程
ocr_provider.py      # Stage 1 引擎注册表（统一 content_list 契约）
stage1_mineru.py     # MinerU 云 API（自动分片）
stage1_paddleocr.py  # PaddleOCR-VL 云 API（自适应分片/bbox 归一化/转义清洗）
stage1_layout.py     # layout 系引擎共享转换层（标签映射/图注绑定/文本清洗）
stage2_hybrid.py     # 结构重建（云端 LLM 四项后处理）
stage2_common.py     # 轻量兜底 / TOC 锚点+形状栈 / 几何判别 / 全局一致性定级
stage3_epub.py       # EPUB 装订（嵌套目录/MathML/尾注/封面/译文渲染）
stage4_translate.py  # 全书翻译（分批+上下文+译名表）
qc_book.py           # 结构体检表（内容完整/结构正确 红黄绿判决）
run_batch.py         # 批量跑书器（串行转换 + 体检汇总）
tests/               # 单元回归测试（每例对应真实病例）
FIXLOG.md            # 兜底策略病例登记（每本翻车书的根因与修复）
updater.py           # 检查更新（GitHub Releases 对比）
version.py           # 版本号
popo/                # vendor 自 MinerU-Popo（MIT），候选筛选/建树/表格合并
progress_ui.py       # 进度窗口（羊皮纸鎏金，自适应缩放）
```

## 路线图

- [ ] 双语对照 EPUB 输出
- [ ] 纯数字脚注的锚点增强（LLM 语境判断兜底）
- [ ] macOS / Linux 验证与打包
- [ ] exe 代码签名（消除 SmartScreen 提示）

## 致谢

- [MinerU](https://github.com/opendatalab/MinerU) — 文档解析云 API
- [MinerU-Popo](https://github.com/opendatalab/MinerU-Popo)（MIT）—
  OCR 后处理框架，本项目的候选筛选/prompt/表格合并/建树框架来自它

## License

[MIT](LICENSE)。`popo/` 目录包含 MinerU-Popo 的 vendored 代码，
其许可证文本见 [popo/LICENSE](popo/LICENSE)。
