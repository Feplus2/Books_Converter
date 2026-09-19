"""转换完成/失败提示音：异步播放 WAV（winsound.PlaySound，非阻塞）。

替代旧 winsound.Beep 琶音（软件模拟蜂鸣、同步阻塞，收尾高 CPU 时卡顿截断）。
环境变量 CONVERT_COMPLETE_SOUND / CONVERT_FAIL_SOUND：
  未设置 → 播放随附 assets/complete.wav / assets/fail.wav
            （均为 Kenney 素材，CC0；fail 由 minimize_008.ogg 转 16-bit PCM）
  off    → 静音
  其他   → 自定义 .wav 路径
失败方向 = 不动作：任何异常都静默跳过，绝不影响转换进程退出码。
"""
import os
from pathlib import Path

_DEFAULT_WAV = Path(__file__).parent / "assets" / "complete.wav"
_DEFAULT_FAIL_WAV = Path(__file__).parent / "assets" / "fail.wav"


def _play(env_key: str, default_wav: Path) -> None:
    try:
        import winsound
        conf = os.environ.get(env_key, "").strip()
        if conf.lower() == "off":
            return
        wav = Path(conf) if conf else default_wav
        if not wav.exists():
            return
        winsound.PlaySound(str(wav), winsound.SND_FILENAME
                           | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass


def play_completion_sound() -> None:
    _play("CONVERT_COMPLETE_SOUND", _DEFAULT_WAV)


def play_failure_sound() -> None:
    _play("CONVERT_FAIL_SOUND", _DEFAULT_FAIL_WAV)
