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
    if os.environ.get("EDGE_VOICE_ZH"):
        cfg["edge_voice_zh"] = os.environ["EDGE_VOICE_ZH"]
    if os.environ.get("EDGE_VOICE_EN"):
        cfg["edge_voice_en"] = os.environ["EDGE_VOICE_EN"]
    return cfg
