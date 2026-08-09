"""Source TTS via Microsoft Edge TTS (free, neural, natural).

Edge TTS streams an MP3; we re-export to WAV (16k/mono) so RVC (which expects
wav) can consume it. ffmpeg is used for the conversion when available.
"""
import asyncio
import os
import subprocess
import tempfile

import edge_tts


async def _synthesize(text: str, voice: str, mp3_path: str):
    comm = edge_tts.Communicate(text, voice)
    await comm.save(mp3_path)


def tts_to_wav(text: str, voice: str, out_wav: str):
    """Generate `text` with Edge TTS and write a 16k mono WAV to `out_wav`."""
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "src.mp3")
        asyncio.run(_synthesize(text, voice, mp3))
        _to_wav(mp3, out_wav)


def _ffmpeg_exe():
    """Return an ffmpeg executable path, preferring system ffmpeg, then the
    static binary shipped by imageio-ffmpeg (works on Gradio Spaces where no
    system ffmpeg is installed)."""
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
    # Prefer ffmpeg (system or static); fall back to a pure-python decoder.
    ff = _ffmpeg_exe()
    if ff:
        try:
            subprocess.run(
                [ff, "-y", "-i", mp3_path, "-ar", "16000", "-ac", "1", out_wav],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
    # Fallback: soundfile + miniaudio are lighter than ffmpeg but optional.
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
