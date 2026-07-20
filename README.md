# Books_Converter

扫描书 PDF 一键转成 EPUB 电子书，可选全书翻译。

```
PDF → MinerU 云 API（OCR + 版面分析） → 结构重建（标题层级/跨页拼接/表格合并） → EPUB
                                              ↘ 可选：全书翻译（DeepSeek 文学翻译）
```

- **图形界面**：拖入 PDF 就能用，无需命令行（`启动.bat`）
- **结构重建**：目录树（编/章/节多级嵌套）、跨页段落拼接、跨页表格合并、图注归属、页脚注恢复
- **公式转 MathML**：万级公式批量转换，阅读器里是真数学排版
- **全书翻译**：英文书顺手译成中文 EPUB，文学级译文、人名全书统一
- **轻量**：不用本地 GPU，全部走云端 API（MinerU 免费 + DeepSeek 约 ¥0.1/本）

## 快速开始

1. **安装**（Python 3.10+，推荐 uv）：

   ```bash
   git clone <本仓库地址>
   cd Books_Converter
   uv venv && uv pip install -r requirements.txt
   ```

2. **拿两个 API Key**（都有免费额度）：

   - MinerU Token：https://mineru.net/apiManage/token （免费）
   - DeepSeek API Key：https://platform.deepseek.com/api_keys
   （LLM 配置兼容任何 OpenAI 协议端点，Base URL 可换）

3. **双击 `启动.bat`**，在设置里填入两个 Key，把 PDF 拖进窗口，点"开始转换"。

命令行用户：

```bash
python pipeline.py "D:\books\我的书.pdf"              # 转换
python pipeline.py "D:\books\english.pdf" --translate # 转换+翻译成中文
python pipeline.py book.pdf --no-ocr                  # 文字版 PDF（跳过 OCR）
python pipeline.py book.pdf --max-pages 60            # 只转前 60 页（快速预览）
python pipeline.py book.pdf --skip-mineru             # 复用已有 MinerU 结果
```

## 它怎么做到不重跑本地大模型

- **MinerU 云 API** 只负责"看懂每页"（文字、坐标、类型），免费且快
- **结构重建**不依赖"再识别一遍页面"：候选筛选 + DeepSeek 云端局部判断
  （这是标题吗/这两段该接吗/这注归哪张图），全局结构由
  **TOC 锚点 + 形状栈**（纯代码）定音——LLM 回答局部问题，代码拼装全局结构
- **翻译**按阅读顺序分批（每批 ~6000 字符 + 前一条上下文 + 滚动译名表），
  并发 4 批，translations.json 断点续翻

实测（RTX 5070Ti 笔记本）：561 页法学教科书 76 秒出 EPUB；592 页英文书
含翻译约 15 分钟。

## 项目结构

```
app.py               # GUI 前端（拖拽多书/设置密钥/队列进度）
启动.bat             # 一键启动 GUI
pipeline.py          # 命令行主流程
stage1_mineru.py     # MinerU 云 API（自动分片）
stage2_hybrid.py     # 结构重建（DeepSeek 云端四项后处理）
stage2_common.py     # 轻量兜底 / TOC 锚点+形状栈 / 重页丢弃
stage3_epub.py       # EPUB 装订（嵌套目录 / MathML / 尾注 / 封面 / 译文渲染）
stage4_translate.py  # 全书翻译（分批+上下文+译名表）
popo/                # vendor 自 MinerU-Popo（MIT），候选筛选/建树/表格合并
progress_ui.py       # 进度窗口（羊皮纸鎏金，自适应缩放）
```

## 致谢

- [MinerU](https://github.com/opendatalab/MinerU) — 文档解析云 API
- [MinerU-Popo](https://github.com/opendatalab/MinerU-Popo)（MIT）—
  OCR 后处理框架，本项目的候选筛选/prompt/表格合并/建树框架来自它

## License

MIT（见 LICENSE）。`popo/` 目录包含 MinerU-Popo 的 vendored 代码，
其许可证文本见 `popo/LICENSE`。
