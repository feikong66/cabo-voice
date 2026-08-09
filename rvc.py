"""RVC voice conversion — in-process engine with a subprocess fallback.

Why in-process: the previous implementation spawned a fresh Python interpreter
per request, which re-imported torch and re-loaded HuBERT every time (~20-40s
and a second copy of every weight in RAM). `rvc-python` lets us keep one warm
engine, so only the first request pays the load cost and peak memory stays at
roughly `torch + hubert + one voice model`.

Only ONE voice model is kept resident at a time. Switching voices swaps it,
which trades a short reload for a much smaller memory envelope — the deciding
factor on small cloud instances.

If `rvc-python` is unavailable the module transparently falls back to calling a
local RVC checkout's `infer_cli.py` via subprocess.
"""
import os
import shutil
import subprocess
import sys
import threading

_LOCK = threading.Lock()
_ENGINE = None          # RVCInference instance
_CURRENT = None         # id of the model currently resident
_ENGINE_ERR = None      # import/init failure, remembered so we stop retrying

# Models live outside the package dir so the image layer stays cacheable.
MODELS_DIR = os.environ.get("RVC_MODELS_DIR", "")


def _resolve_model(rvc_cfg: dict, model_id: str = None) -> tuple:
    models = rvc_cfg.get("models", {})
    if not models:
        raise RuntimeError("No RVC models configured under rvc.models.")
    if model_id and model_id in models:
        mid = model_id
    else:
        mid = rvc_cfg.get("default_model") or next(iter(models))
    m = dict(models.get(mid, {}))
    m.setdefault("index", "")
    m.setdefault("pitch", 0)
    return mid, m


def _get_engine(rvc_cfg: dict):
    """Create the shared RVCInference lazily; base models self-download."""
    global _ENGINE, _ENGINE_ERR
    if _ENGINE is not None:
        return _ENGINE
    if _ENGINE_ERR is not None:
        raise _ENGINE_ERR
    try:
        from rvc_python.infer import RVCInference
        device = rvc_cfg.get("device") or os.environ.get("RVC_DEVICE") or "cpu:0"
        kwargs = {"device": device}
        if MODELS_DIR:
            kwargs["models_dir"] = MODELS_DIR
        print("[rvc] initialising engine on", device, flush=True)
        _ENGINE = RVCInference(**kwargs)
        print("[rvc] engine ready", flush=True)
        return _ENGINE
    except Exception as e:                      # noqa: BLE001
        _ENGINE_ERR = e
        print("[rvc] engine unavailable:", repr(e), flush=True)
        raise


def _infer_inprocess(src_wav, dst_wav, rvc_cfg, mid, m):
    global _CURRENT
    eng = _get_engine(rvc_cfg)
    if _CURRENT != mid:
        index = m.get("index") or ""
        if index and not os.path.exists(index):
            index = ""
        print(f"[rvc] loading voice '{mid}'", flush=True)
        eng.load_model(m["pth"], version=m.get("version", "v2"), index_path=index)
        _CURRENT = mid
    eng.set_params(
        f0up_key=int(m.get("pitch", 0) or 0),
        f0method=rvc_cfg.get("f0_method", "rmvpe"),
        index_rate=float(rvc_cfg.get("index_rate", 0.75)),
        filter_radius=int(rvc_cfg.get("filter_radius", 3)),
        resample_sr=int(rvc_cfg.get("resample_sr", 0)),
        rms_mix_rate=float(rvc_cfg.get("rms_mix_rate", 1)),
        protect=float(rvc_cfg.get("protect", 0.33)),
    )
    eng.infer_file(src_wav, dst_wav)
    return dst_wav


# --- legacy subprocess path (kept as a fallback) ---
DEFAULT_ARGS = (
    "-m {model} -i {input} -o {output} -p {pitch} -s {sample_rate} "
    "-f0 {f0_method} -ix {index} -ir {index_rate} -fr {filter_radius} "
    "-rs {resample_sr} -rm {rms_mix_rate} -pro {protect}"
)


def _infer_subprocess(src_wav, dst_wav, rvc_cfg, m):
    script = rvc_cfg.get("infer_script")
    if not (script and os.path.exists(script)):
        raise RuntimeError(
            "RVC inference unavailable: install `rvc-python`, or set "
            "rvc.infer_script to a valid infer_cli.py."
        )
    repo = rvc_cfg.get("repo") or os.path.dirname(script)
    tmpl = rvc_cfg.get("infer_args_template", DEFAULT_ARGS)
    args_str = tmpl.format(
        model=m["pth"], index=m.get("index") or "", input=src_wav, output=dst_wav,
        pitch=m.get("pitch", 0), sample_rate=rvc_cfg.get("sample_rate", 40000),
        f0_method=rvc_cfg.get("f0_method", "rmvpe"),
        index_rate=rvc_cfg.get("index_rate", 0.75),
        filter_radius=rvc_cfg.get("filter_radius", 3),
        resample_sr=rvc_cfg.get("resample_sr", 0),
        rms_mix_rate=rvc_cfg.get("rms_mix_rate", 1),
        protect=rvc_cfg.get("protect", 0.33),
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = repo
    r = subprocess.run([sys.executable, script] + args_str.split(),
                       cwd=repo, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError("RVC infer failed: " +
                           (r.stderr or b"").decode(errors="replace")[:600])
    if not os.path.exists(dst_wav):
        raise RuntimeError("RVC produced no output file.")
    return dst_wav


def convert(src_wav: str, dst_wav: str, rvc_cfg: dict, model_id: str = None):
    if not rvc_cfg.get("enabled", True):
        if src_wav != dst_wav:
            shutil.copyfile(src_wav, dst_wav)
        return dst_wav

    mid, m = _resolve_model(rvc_cfg, model_id)
    model = m.get("pth")
    if not model or not os.path.exists(model):
        raise FileNotFoundError(
            f"RVC model not found: {model}. Place the .pth under models/ or fix "
            "rvc.models.<id>.pth in config.json."
        )

    # Serialise inference: concurrent CPU conversions would multiply RAM use and
    # the engine itself is not re-entrant.
    with _LOCK:
        try:
            return _infer_inprocess(src_wav, dst_wav, rvc_cfg, mid, m)
        except Exception as e:                  # noqa: BLE001
            if _ENGINE is not None:
                raise                            # engine works; this is a real error
            print("[rvc] falling back to subprocess:", repr(e), flush=True)
            return _infer_subprocess(src_wav, dst_wav, rvc_cfg, m)


def warmup(rvc_cfg: dict):
    """Preload engine + default voice so the first user request isn't slow."""
    try:
        with _LOCK:
            mid, m = _resolve_model(rvc_cfg, None)
            if not os.path.exists(m.get("pth", "")):
                return
            global _CURRENT
            eng = _get_engine(rvc_cfg)
            index = m.get("index") or ""
            if index and not os.path.exists(index):
                index = ""
            eng.load_model(m["pth"], version=m.get("version", "v2"), index_path=index)
            _CURRENT = mid
            print(f"[rvc] warmed up with '{mid}'", flush=True)
    except Exception as e:                      # noqa: BLE001
        print("[rvc] warmup skipped:", repr(e), flush=True)
