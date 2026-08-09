"""CABO all-in-one service — scoreboard PWA + Edge TTS -> RVC voice backend.

Everything is served from ONE origin, which removes the two failure modes that
kept phones on the built-in system voice:

  * mixed content — an HTTPS page may not call an HTTP voice endpoint,
  * CORS — cross-origin POST /tts was blocked by the browser.

Routes
  GET  /healthz        liveness + per-model presence
  GET  /models         list of RVC voice models (id + bilingual labels)
  POST /tts            {text, lang, model} -> audio/wav
  GET  /               the CABO web app (static)

Gradio is intentionally NOT used: it costs ~150MB RSS and we only need REST.
"""
import os
import subprocess
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
RVC_REPO = os.environ.get("RVC_REPO", os.path.join(HERE, "rvc"))
WEB_DIR = os.environ.get("CABO_WEB_DIR", os.path.join(HERE, "web"))


def ensure_rvc():
    """Clone the official RVC repo when the image did not bake it in."""
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
CFG.setdefault("rvc", {})["repo"] = RVC_REPO
CFG["rvc"]["infer_script"] = os.path.join(RVC_REPO, "infer_cli.py")

# Drop models whose weights are absent from the image so /models never offers a
# voice that would fail at synthesis time (index files are optional).
_rvc = CFG.get("rvc", {})
_present = {mid: m for mid, m in (_rvc.get("models") or {}).items()
            if os.path.exists(m.get("pth", ""))}
if _present:
    _rvc["models"] = _present
    if _rvc.get("default_model") not in _present:
        _rvc["default_model"] = next(iter(_present))
for _mid, _m in (_rvc.get("models") or {}).items():
    idx = _m.get("index") or ""
    if idx and not os.path.exists(idx):
        _m["index"] = ""   # index trimmed from the image to save RAM

from starlette.requests import Request
from starlette.responses import JSONResponse, FileResponse
from starlette.staticfiles import StaticFiles
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


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


app = FastAPI(title="CABO — scoreboard + voice (Edge TTS + RVC)")

# Same-origin is the normal case now, but keep CORS open so an externally hosted
# CABO (e.g. GitHub Pages) can still point at this service.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes are registered before the static mount so they win on conflicts.
app.add_route("/healthz", healthz, methods=["GET"])
app.add_route("/models", list_models, methods=["GET"])
app.add_route("/tts", tts_ep, methods=["POST"])

# The CABO web app, served from the same origin as /tts.
if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    print(f"[web] serving CABO from {WEB_DIR}", flush=True)
else:
    print(f"[web] no web dir at {WEB_DIR} — API only", flush=True)


@app.on_event("startup")
def _warmup():
    """Preload the engine off the request path so the first announcement in a
    game is not stuck behind a ~60s cold load. Runs in a thread so the health
    check can pass immediately. Disable with RVC_WARMUP=0."""
    if os.environ.get("RVC_WARMUP", "1") != "1":
        return
    import threading
    from rvc import warmup
    threading.Thread(target=warmup, args=(CFG.get("rvc", {}),), daemon=True).start()


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT")
               or os.environ.get("GRADIO_SERVER_PORT")
               or CFG.get("port", 7860))
    print(f"[boot] listening on 0.0.0.0:{port}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port)
