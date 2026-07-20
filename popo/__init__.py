"""
popo — MinerU-Popo OCR 后处理框架的本地 vendor 包。

出处: https://github.com/opendatalab/MinerU-Popo (MIT License)
许可证文本见同目录 LICENSE。

功能（供结构分析引擎调用）:
  - content_list_to_pages: MinerU 云 API content_list → 统一 pages dict
  - inference 模块: 候选筛选 / prompt 构建 / 输出解析 / 动态分块（共享工具）
  - build_tree: 标注后的扁平 block 列表 → 层级文档树

注：原 MinerU-Popo 的本地 VLM 推理后端已移除——本项目统一走
hybrid 引擎（DeepSeek 等 OpenAI 兼容 API 的云端文本推理）。
"""

from .convert import content_list_to_pages
from .tree import build_tree

__all__ = ["build_tree", "content_list_to_pages"]
