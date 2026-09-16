"""转换完成提示音：异步播放 WAV（winsound.PlaySound，非阻塞）。

替代旧 winsound.Beep 琶音（软件模拟蜂鸣、同步阻塞，收尾高 CPU 时卡顿截断）。
环境变量 CONVERT_COMPLETE_SOUND：
  未设置 → 播放随附 assets/complete.wav（Kenney confirmation_002，CC0）
  off    → 静音
  其他   → 自定义 .wav 路径
失败方向 = 不动作：任何异常都静默跳过，绝不影响转换。
"""
import os
from pathlib import Path

_DEFAULT_WAV = Path(__file__).parent / "assets" / "complete.wav"


def play_completion_sound() -> None:
    try:
        import winsound
        conf = os.environ.get("CONVERT_COMPLETE_SOUND", "").strip()
        if conf.lower() == "off":
            return
        wav = Path(conf) if conf else _DEFAULT_WAV
        if not wav.exists():
            return
        winsound.PlaySound(str(wav), winsound.SND_FILENAME
                           | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass
