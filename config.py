import json
import os

CONFIG_PATH = os.environ.get("VOICE_CONFIG", os.path.join(os.path.dirname(__file__), "config.json"))


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# Allow overriding the RVC model paths via env (handy on HuggingFace Spaces / Railway)
def apply_env(cfg):
    rvc = cfg.setdefault("rvc", {})
    models = rvc.setdefault("models", {})
    if os.environ.get("RVC_DEFAULT_MODEL") and os.environ["RVC_DEFAULT_MODEL"] in models:
        rvc["default_model"] = os.environ["RVC_DEFAULT_MODEL"]
    default_id = rvc.get("default_model") or (next(iter(models)) if models else None)
    if default_id and default_id in models:
        dm = models[default_id]
        if os.environ.get("RVC_MODEL"):
            dm["pth"] = os.environ["RVC_MODEL"]
        if os.environ.get("RVC_INDEX"):
            dm["index"] = os.environ["RVC_INDEX"]
        if os.environ.get("RVC_PITCH"):
            dm["pitch"] = int(os.environ["RVC_PITCH"])
    if os.environ.get("RVC_DEVICE"):
        cfg["device"] = os.environ["RVC_DEVICE"]
        rvc["device"] = os.environ["RVC_DEVICE"]
    # `pm` skips the ~180MB rmvpe model — the lever to pull on small instances.
    if os.environ.get("RVC_F0_METHOD"):
        rvc["f0_method"] = os.environ["RVC_F0_METHOD"]
    if os.environ.get("RVC_INDEX_RATE"):
        rvc["index_rate"] = float(os.environ["RVC_INDEX_RATE"])
    if os.environ.get("EDGE_VOICE_ZH"):
        cfg["edge_voice_zh"] = os.environ["EDGE_VOICE_ZH"]
    if os.environ.get("EDGE_VOICE_EN"):
        cfg["edge_voice_en"] = os.environ["EDGE_VOICE_EN"]
    # Server-side default speaking rate, e.g. "+10%". The client may override
    # it per request; announcements sound better slightly faster than default.
    if os.environ.get("EDGE_RATE"):
        cfg["rate"] = os.environ["EDGE_RATE"]
    cfg.setdefault("rate", "+10%")
    return cfg
