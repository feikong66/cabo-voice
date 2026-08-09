"""CABO voice service — Gradio SDK build for the free HF Spaces tier.

The CABO PWA talks to this service over a small REST API:
  GET  /healthz        liveness + per-model presence
  GET  /models         list of RVC voice models (id + bilingual labels)
  POST /tts            {text, lang, model} -> audio/wav

On the free (Gradio) tier there is no Dockerfile, so instead of a COPY layer we:
  * clone the official RVC repo at startup into ./rvc,
  * put imageio-ffmpeg's static binary on PATH (no system ffmpeg in the image),
  * launch a tiny Gradio UI and mount the REST routes on the same ASGI app.
"""
import os
import subprocess
import tempfile
import uuid

# ---- 0. runtime bootstrap (no Dockerfile on the Gradio tier) ----
HERE = os.path.dirname(os.path.abspath(__file__))
RVC_REPO = os.environ.get("RVC_REPO", os.path.join(HERE, "rvc"))


def ensure_rvc():
    if os.path.exists(os.path.join(RVC_REPO, "infer_cli.py")):
        return
    print("[bootstrap] cloning RVC repo ->", RVC_REPO, flush=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1",
             "https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git",
             RVC_REPO],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600,
        )
    except Exception as e:
        print("[bootstrap] RVC clone failed:", repr(e), flush=True)


ensure_rvc()

# Make a static ffmpeg available on PATH (imageio-ffmpeg ships one).
try:
    import imageio_ffmpeg
    _ff = imageio_ffmpeg.get_ffmpeg_exe()
    os.environ["PATH"] = os.path.dirname(_ff) + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

from config import load_config, apply_env
from tts import tts_to_wav
from rvc import convert

CFG = apply_env(load_config())
# Point RVC at the cloned repo (relative to the workspace).
CFG.setdefault("rvc", {})["repo"] = RVC_REPO
CFG["rvc"]["infer_script"] = os.path.join(RVC_REPO, "infer_cli.py")

# ---- 1. REST handlers (Starlette) ----
from starlette.requests import Request
from starlette.responses import JSONResponse, FileResponse


def healthz(request: Request):
    rvc = CFG.get("rvc", {})
    models = rvc.get("models", {})
    status = {mid: os.path.exists(m.get("pth", "")) for mid, m in models.items()}
    return JSONResponse({
        "ok": True,
        "rvc_enabled": rvc.get("enabled", True),
        "default_model": rvc.get("default_model"),
        "models": status,
    })


def list_models(request: Request):
    rvc = CFG.get("rvc", {})
    models = rvc.get("models", {})
    out = []
    for mid, m in models.items():
        out.append({
            "id": mid,
            "label_zh": m.get("label_zh", mid),
            "label_en": m.get("label_en", mid),
            "has_index": bool(m.get("index")),
            "present": os.path.exists(m.get("pth", "")),
        })
    return JSONResponse({"default": rvc.get("default_model"), "models": out})


async def tts_ep(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json body"}, status_code=400)
    text = (data.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    if len(text) > 500:
        return JSONResponse({"error": "text too long (max 500 chars)"}, status_code=400)
    lang = (data.get("lang") or "zh").lower()
    model = data.get("model")
    voice = CFG["edge_voice_en"] if lang.startswith("en") else CFG["edge_voice_zh"]
    td = tempfile.gettempdir()
    uid = uuid.uuid4().hex
    src = os.path.join(td, f"{uid}_src.wav")
    dst = os.path.join(td, f"{uid}_rvc.wav")
    try:
        tts_to_wav(text, voice, src)
        convert(src, dst, CFG.get("rvc", {}), model)
        if not os.path.exists(dst):
            return JSONResponse({"error": "conversion produced no audio"}, status_code=500)
        return FileResponse(dst, media_type="audio/wav", filename="tts.wav")
    except Exception as e:
        return JSONResponse({"error": f"tts failed: {e}"}, status_code=500)
    finally:
        try:
            os.remove(src)
        except OSError:
            pass


# ---- 2. Gradio UI + mount our routes on the same ASGI app ----
import gradio as gr
from fastapi import FastAPI

fastapi_app = FastAPI(title="CABO Voice Service (Edge TTS + RVC)")
demo = gr.Blocks(title="CABO Voice Service")
with demo:
    gr.Markdown(
        "# CABO Voice Service (Edge TTS \u2192 RVC)\n"
        "This Space powers CABO's announcer voices.\n\n"
        "**REST API**\n\n"
        "- `GET /healthz`\n"
        "- `GET /models`\n"
        "- `POST /tts`  body `{text, lang, model}` \u2192 `audio/wav`"
    )

# Register our routes BEFORE mounting Gradio so they take precedence.
fastapi_app.add_route("/healthz", healthz, methods=["GET"])
fastapi_app.add_route("/models", list_models, methods=["GET"])
fastapi_app.add_route("/tts", tts_ep, methods=["POST"])

gradio.mount_gradio_app(fastapi_app, demo, path="/")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT")
               or os.environ.get("GRADIO_SERVER_PORT")
               or CFG.get("port", 7860))
    uvicorn.run(fastapi_app, host="0.0.0.0", port=port)
