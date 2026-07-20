---
name: books-converter
description: 把扫描书 PDF 转成结构化 EPUB（目录树/脚注/MathML 公式），可选全书翻译。当用户需要将书籍 PDF（尤其扫描本）转换为 EPUB、转换并翻译外文书、或批量处理书籍时使用本 skill。
---

# Books_Converter — Agent 使用指南

本文件面向调用本项目的 AI Agent。本项目为"扫描书 PDF → EPUB（可选翻译）"管线，
通过 CLI 使用；所有中间产物以文件形式落在磁盘上，便于检查与复用。

## 调用方式

```bash
cd F:/MyProjects/Books_Converter
.venv/Scripts/python.exe pipeline.py <pdf_path> [选项]
```

选项：

| 选项 | 说明 |
|---|---|
| `--translate [LANG]` | 转换后翻译全书（默认 zh 中文；可传 en/ja/fr 等） |
| `--no-ocr` | 文字版 PDF（跳过强制 OCR，更快） |
| `--max-pages N` | 只处理前 N 页（快速预览/技术验证，强烈推荐先切片） |
| `--skip-mineru` | 复用 work_dir 下已有 MinerU 结果（重跑结构/翻译/装订用） |
| `--skip-deepseek` | 复用已有 structure.json（只重跑装订） |
| `-o DIR` | 指定输出目录（默认 PDF 所在目录） |

前置条件：环境变量或项目根 `.env` 中配置 `MINERU_TOKEN`、
`DEEPSEEK_API_KEY`（及可选 `DEEPSEEK_BASE_URL`/`DEEPSEEK_MODEL`，
兼容任何 OpenAI 协议端点）。

## 产物与中间数据（work_dir = PDF 同目录 / PDF stem）

| 文件 | 内容 |
|---|---|
| `<书名>.epub` | 最终电子书（同时复制到 PDF 同目录） |
| `mineru/<书名>.md` | MinerU 的 Markdown 全文 |
| `mineru/<书名>_content_list.json` | MinerU block 数据（type/text/bbox/page_idx） |
| `mineru/images/` | 提取的图片 |
| `structure.json` | 结构分析结果：metadata/front_matter/back_matter/tree/toc_entries |
| `popo_blocks.json` | 标注后的 block 列表（含 contd/level/image/table_merge） |
| `translations.json` | 翻译产物：`{translations: {key: 译文}, glossary: 译名表}`，可断点续翻 |

## 进度与日志

- 控制台/日志输出各阶段耗时与统计（分块数、标题数、锚点数、翻译条数）。
- 长时间任务（MinerU 云端、翻译）建议后台运行并轮询日志；
  无独立进度文件——进度即日志行。
- 失败降级：结构分析失败会退化为 MinerU 原始结构继续出 EPUB；
  翻译失败只输出原文版。命令行退出码 0 即视为完成（含降级）。

## 典型用法

```bash
# 快速验证一本书（先切 60 页，别全量跑）
.venv/Scripts/python.exe pipeline.py book.pdf --max-pages 60

# 全书转换 + 中文翻译
.venv/Scripts/python.exe pipeline.py english_novel.pdf --translate

# 复用缓存重出（改代码/调样式后）
.venv/Scripts/python.exe pipeline.py book.pdf --skip-mineru --skip-deepseek

# 批量：循环调用；同名 work_dir 会自动复用 mineru 缓存
```

## 注意事项

- 长文档先 `--max-pages` 切片验证再全量跑（血泪教训）。
- 扫描本源 PDF 可能有重页（同页扫两次），管线会自动检测丢弃（日志"重页检测"）。
- 页脚注按编号匹配到正文锚点，锚不到的挂章尾，内容不丢。
- 公式转 MathML（latex2mathml），失败的保留 LaTeX 源码于 alttext。
