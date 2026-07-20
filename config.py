"""Books_Converter 配置文件。

从 .env 文件或环境变量读取配置。
复制 .env.example 为 .env，填入你的 API Key 即可使用。
"""

import os
from pathlib import Path


def _load_dotenv():
    """从项目根目录的 .env 文件加载环境变量（不覆盖已有的）"""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool = True) -> bool:
    val = os.environ.get(key, str(default).lower())
    return val.lower() in ("true", "1", "yes")


# ============================================================
# API 密钥
# ============================================================
MINERU_TOKEN = _env("MINERU_TOKEN")
DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = _env("DEEPSEEK_MODEL", "deepseek-v4-flash")

# ============================================================
# 默认输出路径
# ============================================================
DEFAULT_OUTPUT_DIR = _env("DEFAULT_OUTPUT_DIR", str(Path(__file__).parent / "output"))

# ============================================================
# MinerU 参数
# ============================================================
MINERU_MODEL = _env("MINERU_MODEL", "vlm")
MINERU_LANGUAGE = _env("MINERU_LANGUAGE", "ch")
MINERU_TIMEOUT = int(_env("MINERU_TIMEOUT", "900"))
MINERU_ENABLE_FORMULA = _env_bool("MINERU_ENABLE_FORMULA", True)
MINERU_ENABLE_TABLE = _env_bool("MINERU_ENABLE_TABLE", True)

# ============================================================
# 结构分析参数（分块推理的动态分块目标页数）
# ============================================================
CHUNK_SIZE = int(_env("CHUNK_SIZE", "12"))
