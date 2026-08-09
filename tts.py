"""Neural speech via Microsoft Edge TTS.

Two output paths:

  synthesize()  -> MP3 bytes, served straight to the browser. No ffmpeg, no
                   temp files, no torch. This is the default voice for CABO.
  tts_to_wav()  -> 16k mono WAV on disk, only needed when RVC is enabled,
                   since RVC consumes wav.

Why a server at all, when Edge TTS is "just" a WebSocket? Microsoft rejects the
handshake with 403 unless the User-Agent looks like desktop Edge, and browsers
cannot override User-Agent. Measured: desktop Edge UA passes; Chrome, Android
Chrome, iOS Safari and WeChat all get 403. So phones cannot call Microsoft
directly and need this thin proxy. Origin, notably, is *not* checked.
"""
import asyncio
import os
import subprocess
import tempfile

import edge_tts

# Curated Chinese voices, with the dialect ones kept — they are the fun part for
# a card-game scoreboard. Labels are what the UI shows.
VOICES = [
    {"id": "zh-CN-YunjianNeural",          "zh": "云健 · 激情解说", "en": "Yunjian - sports caster", "gender": "male"},
    {"id": "zh-CN-XiaoxiaoNeural",         "zh": "晓晓 · 温暖女声", "en": "Xiaoxiao - warm",         "gender": "female"},
    {"id": "zh-CN-XiaoyiNeural",           "zh": "晓伊 · 活泼少女", "en": "Xiaoyi - lively",         "gender": "female"},
    {"id": "zh-CN-YunxiNeural",            "zh": "云希 · 阳光少年", "en": "Yunxi - sunshine",        "gender": "male"},
    {"id": "zh-CN-YunxiaNeural",           "zh": "云夏 · 可爱童声", "en": "Yunxia - cute",           "gender": "male"},
    {"id": "zh-CN-YunyangNeural",          "zh": "云扬 · 专业播报", "en": "Yunyang - news",          "gender": "male"},
    {"id": "zh-CN-liaoning-XiaobeiNeural", "zh": "小北 · 东北话",   "en": "Xiaobei - NE dialect",    "gender": "female"},
    {"id": "zh-CN-shaanxi-XiaoniNeural",   "zh": "小妮 · 陕西话",   "en": "Xiaoni - Shaanxi",        "gender": "female"},
    {"id": "zh-HK-HiuGaaiNeural",          "zh": "曉佳 · 粤语",     "en": "HiuGaai - Cantonese",     "gender": "female"},
    {"id": "zh-HK-WanLungNeural",          "zh": "雲龍 · 粤语男声", "en": "WanLung - Cantonese",     "gender": "male"},
    {"id": "zh-TW-HsiaoChenNeural",        "zh": "曉臻 · 台湾腔",   "en": "HsiaoChen - TW",          "gender": "female"},
    {"id": "zh-TW-YunJheNeural",           "zh": "雲哲 · 台湾腔男", "en": "YunJhe - TW",             "gender": "male"},
    {"id": "en-US-AriaNeural",             "zh": "Aria · 英文女声", "en": "Aria - English",          "gender": "female"},
    {"id": "en-US-GuyNeural",              "zh": "Guy · 英文男声",  "en": "Guy - English",           "gender": "male"},
]

VOICE_IDS = {v["id"] for v in VOICES}
DEFAULT_ZH = "zh-CN-YunjianNeural"
DEFAULT_EN = "en-US-AriaNeural"


def resolve_voice(voice: str | None, lang: str = "zh") -> str:
    """Map a requested voice onto a real Edge voice id.

    Accepts a full id ("zh-CN-XiaoyiNeural") or a short alias ("xiaoyi").
    Unknown values fall back to the language default instead of failing, so a
    stale client setting can never mute the game.
    """
    if voice:
        if voice in VOICE_IDS:
            return voice
        low = voice.lower()
        for v in VOICES:
            if v["id"].lower() == low or v["id"].split("-")[-1].lower().replace("neural", "") == low:
                return v["id"]
    return DEFAULT_EN if str(lang).lower().startswith("en") else DEFAULT_ZH


def _pct(value, default: str = "+0%") -> str:
    """Normalise a rate/volume/pitch knob into the form Edge TTS expects."""
    if value is None or value == "":
        return default
    s = str(value).strip()
    if s.endswith("%") or s.endswith("Hz"):
        return s if s[0] in "+-" else "+" + s
    try:
        n = int(round(float(s)))
    except ValueError:
        return default
    return f"{n:+d}%"


async def _collect(text: str, voice: str, rate: str, volume: str, pitch: str) -> bytes:
    comm = edge_tts.Communicate(text, voice, rate=rate, volume=volume, pitch=pitch)
    buf = bytearray()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)


async def synthesize_async(text: str, voice: str | None = None, lang: str = "zh",
                           rate=None, volume=None, pitch=None) -> bytes:
    """Return MP3 bytes for `text`. Roughly 1s and ~18KB for a short line."""
    v = resolve_voice(voice, lang)
    data = await _collect(text, v, _pct(rate), _pct(volume), _pct(pitch, "+0Hz"))
    if not data:
        raise RuntimeError(f"Edge TTS returned no audio for voice {v}")
    return data


def synthesize(text: str, voice: str | None = None, lang: str = "zh",
               rate=None, volume=None, pitch=None) -> bytes:
    """Blocking wrapper around synthesize_async().

    asyncio.run() refuses to nest, so calling this from inside a running loop
    (any async request handler) would raise instead of speaking. When a loop is
    already running we hand the work to a private one on a worker thread.
    Async callers should await synthesize_async() directly.
    """
    args = (text, voice, lang, rate, volume, pitch)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(synthesize_async(*args))

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(synthesize_async(*args))).result()


def tts_to_wav(text: str, voice: str, out_wav: str, rate=None):
    """Write a 16k mono WAV — only used when RVC post-processing is enabled."""
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "src.mp3")
        with open(mp3, "wb") as f:
            f.write(synthesize(text, voice, rate=rate))
        _to_wav(mp3, out_wav)


def _ffmpeg_exe():
    import shutil
    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        return sys_ff
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _to_wav(mp3_path: str, out_wav: str):
    ff = _ffmpeg_exe()
    if ff:
        try:
            subprocess.run(
                [ff, "-y", "-i", mp3_path, "-ar", "16000", "-ac", "1", out_wav],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
    try:
        import miniaudio  # type: ignore
        import soundfile as sf  # type: ignore
        data = miniaudio.decode_file(mp3_path)
        sf.write(out_wav, data.samples, data.sample_rate)
        return
    except Exception:
        raise RuntimeError(
            "Could not convert MP3->WAV: install ffmpeg (recommended) or miniaudio+soundfile"
        )
