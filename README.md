# Books_Converter

PDF 转 EPUB 智能转换工具。扫描本或文字版 PDF 均支持。

## 原理

```
PDF → MinerU API(VLM·OCR+版面分析) → DeepSeek V4 Flash(章节语义识别) → EPUB
```

- **MinerU VLM 模型**：精度 95+，自动版面分析、OCR、表格/公式提取
- **DeepSeek V4 Flash**：1M 上下文，识别章节层级、前页（封面/版权/目录）、后页（附录/索引）
- **ebooklib**：生成标准 EPUB，目录完整、CSS 美观

## 安装

```bash
git clone <本仓库地址>
cd Books_Converter

# Python 3.10+，推荐用 uv
uv venv
source .venv/Scripts/activate   # Windows
# source .venv/bin/activate     # macOS/Linux

uv pip install -r requirements.txt
```

## 配置

```bash
cp .env.example .env
# 编辑 .env，填入 MinerU 和 DeepSeek 的 API Key
```

MinerU Token 免费获取：https://mineru.net/apiManage/token
DeepSeek API Key：https://platform.deepseek.com/api_keys
（充值 $2 即可使用，每本书成本 < 1 美分）

## 使用

```bash
# 扫描本（默认启用 OCR）
python pipeline.py "D:\books\我的书.pdf"

# 文字版 PDF（禁用 OCR，更快）
python pipeline.py "D:\books\我的书.pdf" --no-ocr

# 指定输出目录
python pipeline.py book.pdf -o "F:\epubs"
```

EPUB 文件保存在 `output/<书名>/` 目录下。

## 项目结构

```
Books_Converter/
├── pipeline.py          # 主流程（三阶段串联）
├── stage1_mineru.py     # MinerU API 调用（含自动分片）
├── stage2_deepseek.py   # DeepSeek V4 Flash 结构分析
├── stage3_epub.py       # EPUB 生成（ebooklib + 段落合并）
├── config.py            # 配置读取
└── requirements.txt     # Python 依赖
```
