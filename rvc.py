"""RVC voice conversion wrapper (multi-model aware).

RVC is *voice conversion*, not TTS: it takes a source audio (here, the Edge TTS
output) and re-synthesizes it with the target voice model. We call the official
RVC `infer_cli.py` via subprocess so the service stays robust across RVC
forks/versions.

The CLI argument names below match RVC-Project / Mangio `infer_cli.py`. If your
fork uses different flags, override them via the `infer_args_template` key in
config.json — it is a Python str.format() template with these placeholders:
{model} {index} {input} {output} {pitch} {sample_rate} {f0_method} {index_rate}
{filter_radius} {resample_sr} {rms_mix_rate} {protect}.
"""
import os
import shutil
import subprocess
import sys

# Default template matches RVC-Project / Mangio `infer_cli.py`.
DEFAULT_ARGS = (
    "-m {model} -i {input} -o {output} -p {pitch} -s {sample_rate} "
    "-f0 {f0_method} -ix {index} -ir {index_rate} -fr {filter_radius} "
    "-rs {resample_sr} -rm {rms_mix_rate} -pro {protect}"
)


def _resolve_model(rvc_cfg: dict, model_id: str = None) -> dict:
    models = rvc_cfg.get("models", {})
    if not models:
        raise RuntimeError("No RVC models configured under rvc.models.")
    if model_id and model_id in models:
        m = dict(models[model_id])
    else:
        mid = rvc_cfg.get("default_model") or next(iter(models))
        m = dict(models.get(mid, {}))
    m.setdefault("index", "")
    m.setdefault("pitch", 0)
    return m


def convert(src_wav: str, dst_wav: str, rvc_cfg: dict, model_id: str = None):
    if not rvc_cfg.get("enabled", True):
        # Pass-through: just copy source to destination.
        if src_wav != dst_wav:
            shutil.copyfile(src_wav, dst_wav)
        return dst_wav

    m = _resolve_model(rvc_cfg, model_id)
    model = m["pth"]
    if not model or not os.path.exists(model):
        raise FileNotFoundError(
            f"RVC model not found: {model}. Place your .pth in the models/ folder "
            "or set rvc.models.<id>.pth in config.json."
        )

    script = rvc_cfg.get("infer_script")
    repo = rvc_cfg.get("repo") or (os.path.dirname(script) if script else os.getcwd())
    index = m.get("index") or ""

    tmpl = rvc_cfg.get("infer_args_template", DEFAULT_ARGS)
    args_str = tmpl.format(
        model=model,
        index=index,
        input=src_wav,
        output=dst_wav,
        pitch=m.get("pitch", 0),
        sample_rate=rvc_cfg.get("sample_rate", 40000),
        f0_method=rvc_cfg.get("f0_method", "rmvpe"),
        index_rate=rvc_cfg.get("index_rate", 0.75),
        filter_radius=rvc_cfg.get("filter_radius", 3),
        resample_sr=rvc_cfg.get("resample_sr", 0),
        rms_mix_rate=rvc_cfg.get("rms_mix_rate", 1),
        protect=rvc_cfg.get("protect", 0.33),
    )

    if script and os.path.exists(script):
        # Run inside the RVC repo dir so its local imports resolve.
        cmd = [sys.executable, script] + args_str.split()
        env = dict(os.environ)
        env["PYTHONPATH"] = repo
        env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        r = subprocess.run(
            cmd, cwd=repo, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if r.returncode != 0:
            err = (r.stderr or b"").decode(errors="replace")
            raise RuntimeError("RVC infer failed: " + err[:600])
        if not os.path.exists(dst_wav):
            raise RuntimeError("RVC produced no output file.")
        return dst_wav

    raise RuntimeError(
        "RVC infer script not found. Set rvc.infer_script (and rvc.repo) in "
        "config.json to point at your RVC fork's infer_cli.py."
    )
