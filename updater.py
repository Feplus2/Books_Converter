"""检查更新：对比 GitHub Releases 最新版本。

免安装工具的定位决定了只做"检查 + 提示 + 浏览器打开下载页"，不做自动
替换 exe。所有网络/解析异常静默降级为 error 状态，绝不影响主功能。
"""

import logging

import requests

from version import __version__

logger = logging.getLogger(__name__)

GITHUB_REPO = "Feplus2/Books_Converter"
_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"


def _parse_ver(tag: str) -> tuple:
    """"v1.2" / "1.2.3" → (1, 2) / (1, 2, 3)；解析失败返回 (0,)。"""
    nums = []
    for part in tag.lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        if not digits:
            break
        nums.append(int(digits))
    return tuple(nums) if nums else (0,)


def _newer(latest: tuple, current: tuple) -> bool:
    n = max(len(latest), len(current))
    return latest + (0,) * (n - len(latest)) > current + (0,) * (n - len(current))


def check_for_update(timeout: int = 8) -> dict:
    """检查 GitHub 最新 Release。

    返回 {"status": "update_available" | "latest" | "error",
          "current": str, "latest": str | None, "url": str | None,
          "error": str | None}
    """
    result = {"current": __version__, "latest": None, "url": None, "error": None}
    try:
        resp = requests.get(_API_URL, timeout=timeout,
                            headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data = resp.json()
        tag = data.get("tag_name") or ""
        result["latest"] = tag.lstrip("vV")
        result["url"] = data.get("html_url") or RELEASES_URL
        if not tag:
            raise ValueError("release 缺少 tag_name")
        result["status"] = ("update_available"
                            if _newer(_parse_ver(tag), _parse_ver(__version__))
                            else "latest")
    except Exception as e:
        logger.info(f"检查更新失败: {e}")
        result["status"] = "error"
        result["error"] = str(e)
    return result
