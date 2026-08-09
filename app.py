"""CABO all-in-one service — scoreboard PWA + neural voice, one origin.

Serving the web app and the voice API from the same origin removes the two
failure modes that kept phones on the built-in system voice:

  * mixed content — an HTTPS page may not call an HTTP voice endpoint,
  * CORS — cross-origin POST /tts was blocked by the browser.

Two voice modes, chosen with VOICE_MODE:

  tts (default)  Edge TTS neural voices, returned as MP3. ~80MB RSS, boots in
                 seconds, runs on a 512MB free tier.
  rvc            Edge TTS piped through an RVC model for a custom character
                 voice. Needs torch and ~2GB RAM; see README.

The RVC imports are deliberately lazy: in tts mode torch need not be installed
at all, and a broken RVC install must not stop the scoreboard from loading.

Routes
  GET  /healthz    liveness, active mode, engine detail
  GET  /voices     Edge TTS voices for the picker
  GET  /models     RVC models (empty list in tts mode)
  POST /tts        {text, lang, voice|model, rate} -> audio/mpeg or audio/wav
  GET  /           the CABO web app
"""
import os
import subprocess
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
RVC_REPO = os.environ.get("RVC_REPO", os.path.join(HERE, "rvc"))
WEB_DIR = os.environ.get("CABO_WEB_DIR", os.path.join(HERE, "web"))
VOICE_MODE = os.environ.get("VOICE_MODE", "tts").strip().lower()
RVC_MODE = VOICE_MODE == "rvc"


def ensure_rvc():
    """Clone the official RVC checkout used by the subprocess fallback.

    NOT called at import time. Inference normally runs through `rvc-python`
    in-process, and upstream has since restructured the repo so `infer_cli.py`
    no longer exists at the root — cloning on every boot would cost tens of
    seconds and hundreds of MB for a file we would not find anyway. Opt in with
    RVC_ALLOW_CLONE=1 if you really want the legacy path.
    """
    if os.environ.get("RVC_ALLOW_CLONE", "0") != "1":
        return
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


if RVC_MODE:
    ensure_rvc()
    # A static ffmpeg (imageio-ffmpeg) — only the RVC path needs wav conversion.
    try:
        import imageio_ffmpeg
        _ff = imageio_ffmpeg.get_ffmpeg_exe()
        os.environ["PATH"] = os.path.dirname(_ff) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass

from config import load_config, apply_env
import tts as tts_mod

CFG = apply_env(load_config())
CFG.setdefault("rvc", {})["repo"] = RVC_REPO
CFG["rvc"]["infer_script"] = os.path.join(RVC_REPO, "infer_cli.py")

if RVC_MODE:
    # Hide models whose weights are absent so /models never offers a voice that
    # would fail at synthesis time (index files are optional).
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
else:
    CFG.setdefault("rvc", {})["models"] = {}

from starlette.requests import Request
from starlette.responses import JSONResponse, FileResponse, Response
from starlette.staticfiles import StaticFiles
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def _rvc_status():
    if not RVC_MODE:
        return {"mode": "tts", "note": "RVC disabled; Edge TTS served directly"}
    try:
        from rvc import status as rvc_status
        return rvc_status()
    except Exception as e:
        return {"engine_loaded": False, "engine_error": f"import failed: {e}"}


def healthz(request: Request):
    rvc = CFG.get("rvc", {})
    models = rvc.get("models", {})
    return JSONResponse({
        "ok": True,
        "mode": VOICE_MODE,
        "default_voice": tts_mod.DEFAULT_ZH,
        "voice_count": len(tts_mod.VOICES),
        "rvc_enabled": RVC_MODE,
        "default_model": rvc.get("default_model") if RVC_MODE else None,
        "models": {mid: os.path.exists(m.get("pth", "")) for mid, m in models.items()},
        "engine": _rvc_status(),
    })


def list_voices(request: Request):
    """Edge TTS voices for the settings picker."""
    return JSONResponse({
        "default": tts_mod.DEFAULT_ZH,
        "voices": [
            {"id": v["id"], "label_zh": v["zh"], "label_en": v["en"], "gender": v["gender"]}
            for v in tts_mod.VOICES
        ],
    })


def list_models(request: Request):
    """RVC models. Empty in tts mode — the client then falls back to /voices."""
    rvc = CFG.get("rvc", {})
    out = [{
        "id": mid,
        "label_zh": m.get("label_zh", mid),
        "label_en": m.get("label_en", mid),
        "has_index": bool(m.get("index")),
        "present": os.path.exists(m.get("pth", "")),
    } for mid, m in (rvc.get("models") or {}).items()]
    return JSONResponse({
        "mode": VOICE_MODE,
        "default": rvc.get("default_model") if RVC_MODE else None,
        "models": out,
    })


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
    rate = data.get("rate", CFG.get("rate"))
    requested = data.get("voice") or data.get("model")

    if not RVC_MODE:
        try:
            # Awaited, not asyncio.run(): we are already inside the loop.
            audio = await tts_mod.synthesize_async(text, requested, lang=lang, rate=rate)
        except Exception as e:
            return JSONResponse({"error": f"tts failed: {e}"}, status_code=502)
        return Response(content=audio, media_type="audio/mpeg", headers={
            "Cache-Control": "no-store",
            "X-Voice": tts_mod.resolve_voice(requested, lang),
        })

    # RVC path: Edge TTS as the source timbre, then voice conversion.
    from starlette.concurrency import run_in_threadpool
    from rvc import convert
    voice = CFG["edge_voice_en"] if lang.startswith("en") else CFG["edge_voice_zh"]
    td = tempfile.gettempdir()
    uid = uuid.uuid4().hex
    src = os.path.join(td, f"{uid}_src.wav")
    dst = os.path.join(td, f"{uid}_rvc.wav")
    try:
        # Both steps are blocking and CPU-bound (torch); off the loop they go,
        # otherwise one synthesis would stall every other request.
        await run_in_threadpool(tts_mod.tts_to_wav, text, voice, src, rate)
        await run_in_threadpool(convert, src, dst, CFG.get("rvc", {}), data.get("model"))
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


app = FastAPI(title="CABO — scoreboard + neural voice")

# Same-origin is the normal case now, but keep CORS open so an externally hosted
# CABO (e.g. GitHub Pages) can still point at this service.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Voice"],
)

# API routes are registered before the static mount so they win on conflicts.
app.add_route("/healthz", healthz, methods=["GET"])
app.add_route("/voices", list_voices, methods=["GET"])
app.add_route("/models", list_models, methods=["GET"])
app.add_route("/tts", tts_ep, methods=["POST"])

if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    print(f"[web] serving CABO from {WEB_DIR}", flush=True)
else:
    print(f"[web] no web dir at {WEB_DIR} — API only", flush=True)


@app.on_event("startup")
def _warmup():
    """Only RVC needs warming (a cold model load costs ~60s). Edge TTS has no
    local model, so tts mode starts serving immediately."""
    if not RVC_MODE or os.environ.get("RVC_WARMUP", "1") != "1":
        return
    import threading
    from rvc import warmup
    threading.Thread(target=warmup, args=(CFG.get("rvc", {}),), daemon=True).start()


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT")
               or os.environ.get("GRADIO_SERVER_PORT")
               or CFG.get("port", 7860))
    print(f"[boot] mode={VOICE_MODE} listening on 0.0.0.0:{port}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port)
