"""本地开发启动器：沙箱走 MITM 代理，给 aiohttp 关掉证书校验。仅本机调试用。"""
import ssl, aiohttp, os
_o = aiohttp.TCPConnector.__init__
def _p(self, *a, **kw):
    c = ssl.create_default_context(); c.check_hostname=False; c.verify_mode=ssl.CERT_NONE
    kw["ssl"] = c
    return _o(self, *a, **kw)
aiohttp.TCPConnector.__init__ = _p
os.environ.setdefault("PORT", "7899")
os.environ.setdefault("VOICE_MODE", "tts")
import uvicorn
from app import app
uvicorn.run(app, host="127.0.0.1", port=int(os.environ["PORT"]), log_level="warning")
