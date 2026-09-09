# 01 架构与数据契约

## 四级管线

```
PDF ──▶ Stage 1 解析引擎（云端 OCR/版面分析）
        ├─ mineru（MinerU 云，支持 强制/自动 OCR；自动模式对 born-digital 用文本层）
        └─ paddleocr（PaddleOCR-VL 云，永远整页识别；ocr 开关被忽略）
        产出（落盘 <work_dir>/<engine>/）：
          {stem}_content_list.json   MinerU 风格块列表（契约）
          {stem}.md                  引擎直出 markdown（留档）
          images/                    裁切好的图片
          ──▶ Stage 2 结构重建（stage2_hybrid + stage2_common）
                LLM 分块标注（contd 跨页拼接 / 标题层级 / 图文关联 / 跨页表格）
                + 锚点层级系统（见 02）+ 轻量兜底（metadata/前后页/目录条目）
                产出：popo_blocks.json + structure.json
          ──▶ Stage 3 EPUB 装订（stage3_epub）
                线性切章 → 嵌套 nav → MathML/尾注/封面
                产出：<书名>.epub（工作目录）+ 复制到 PDF 旁
          ──▶ Stage 4 翻译（可选，stage4_translate，--translate zh）
                DeepSeek 分批+上下文+译名表；translations.json 断点续翻
```

## LLM 调用约定

所有 chat.completions 调用必须经 `llm_thinking.chat_create` 包装（5 处：
stage2_hybrid ×1、stage2_common ×2、stage4_translate ×2）——思考参数进程级
协商：能关则关（thinking:disabled），端点拒绝（400/1210）则降
reasoning_effort=low（对齐 SageRead reasoning-map.ts 恒思考模型取最低档的
口径），仍拒则不下发思考参数。**不拦任何模型**（恒思考模型慢但可用）；
并发下旧模式请求的迟到拒绝只重发不再降档（病例 025）。

## 关键数据契约

### content_list（Stage 1 → 下游的块契约）

每块：`type`（text/title/image/table/header/footer/page_number/aside_text/
image_caption/table_caption/page_footnote…）、`text`、`text_level`
（引擎标题层级；MinerU 是真层级 1..N，PaddleOCR 只有 0/1 二值——
`stage1_layout.py` 一律打 1）、`bbox`（0–1000 千分位）、`page_idx`（0 起）。

### popo_blocks（Stage 2 的标注块，stage3 的唯一输入）

在 content_list 基础上由 `popo/convert.py` 归一化：`type`/`content`/`bbox`
（0–1 浮点）/`page`（**1 起**）/`source_id`（`{book}:{content_list索引}`，
译文查找靠它）/`source_label`/`title_level`（引擎层级，**写了无人读**）、
`id`（阅读顺序）。Stage 2 追加标注：`level`（LLM 首票→锚点校正）、
`level_raw`、`contd`/`image`/`table_merge`、`_anchored`、`rescued`、
`_pos_anchor`/`_pos_num`（泛名书签位置锚定标记）。

> ⚠️ `source_id` 必须与 `content` 同步：锚点富化/合成改写 content 时若保留
> 旧 source_id，译文会取回原文本的译文（病例 019 的幻影'抑郁'分区即
> 富化改写 + 旧 source_id 取错译文的合谋）。

### structure.json

`engine`、`metadata`、`front_matter`/`back_matter`（[{type,label,page_start,
page_end,keep}]）、`toc_entries`（[{text,level,page}]，**page 是印刷页码**；
outline 来源时是扫描页）、`toc_source`（'outline'|None——outline 页码才能
用于正文起点回收）、`tree`（仅 QC 用，stage3 不读）、`popo_blocks_file`。

## 工作目录与 CLI

`work_dir = 输出目录 / PDF词干`；`--skip-mineru` 复用 Stage 1 缓存、
`--skip-deepseek` 复用 structure.json、`--translate zh` 开 Stage 4、
`--headless` JSON 进度（sidecar 协议见 README/集成文档）。

## Stage 4 翻译要点

- 批响应**必须 1..N 全覆盖**，缺号/跳号抛错走 重试→拆半；失败方向=保留
  原文（病例 020：缺号静默收编导致整批错位并经续翻缓存扩散）。
- `translations.json` 断点续翻按 content_list 索引对齐——**引擎缓存不变
  才可续翻**；结构重跑若改变了块集合，先删 translations.json 再跑。
