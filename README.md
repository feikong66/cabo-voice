# CABO 一体化服务（计分网页 + AI 配音）

一个容器同时提供 **CABO 计分网页** 和 **AI 配音接口**，共用一个 HTTPS 域名。

服务有两种模式，由环境变量 `VOICE_MODE` 决定：

| 模式 | 链路 | 常驻内存 | 镜像 | 适用 |
|------|------|----------|------|------|
| **`tts`（默认）** | 文本 → Edge TTS 神经语音 → 返回 MP3 | **约 70MB** | ~200MB | ✅ 免费档就能跑，14 种中英音色，秒回 |
| `rvc` | 文本 → Edge TTS → RVC 换成角色音色 → 返回 WAV | 600–800MB | ~4GB | 需要「周杰伦/温迪」这类特定角色音色时 |

**没有特殊需求就用默认的 `tts`。** 它不装 torch、不下权重、没有冷启动加载模型的几十秒，随便找个免费平台都能跑起来。

## 为什么需要这个后端——不能网页直连微软吗？

不能，实测过了。Edge TTS 本质是个公开 WebSocket，但微软**对 User-Agent 做严格校验**：

| 调用方 UA | 结果 |
|-----------|------|
| 桌面 Edge（`Edg/...`） | ✅ 200 |
| Chrome / 安卓 Chrome / iOS Safari / 微信内置浏览器 | ❌ 403 |

而浏览器**不允许 JS 覆写 User-Agent**，所以手机端纯前端直连必然 403。签名参数 `Sec-MS-GEC` 倒是能在浏览器里算出来（SHA256 over 向下取整到 5 分钟的 Windows file time + 固定 token，放 URL 而非 header），`Origin` 微软也不校验——**唯独 UA 这道坎绕不过去**。因此需要这个薄代理：它替手机发起握手，手机只跟自己的服务说话。

顺带解决另外两条浏览器安全规则——网页和接口**同源**之后：

- **混合内容**：`https://` 页面请求 `http://` 接口会被直接拦截 → 消失；
- **跨域**：`POST /tts` 不再需要配 CORS → 消失。

前端启动时探测同源 `/healthz`，命中就自动启用，**手机打开网址即用，无需填任何地址**。

## 接口

| 路由 | 说明 |
|------|------|
| `GET /` | CABO 计分网页 |
| `GET /healthz` | 存活探测；返回当前 `mode`、默认音色、音色数量 |
| `GET /voices` | AI 配音音色列表（`tts` 模式） |
| `GET /models` | RVC 模型列表（`rvc` 模式；`tts` 模式返回空） |
| `POST /tts` | `{text, lang, voice, rate}` → `audio/mpeg`（`tts` 模式）或 `audio/wav`（`rvc` 模式） |

响应头 `X-Voice` 会带回实际使用的音色 id，方便排错。

---

## 1. 内置音色（`tts` 模式，共 14 种）

| id | 名称 | 特点 |
|----|------|------|
| `zh-CN-YunjianNeural` | 云健 · 激情解说 | **默认**，体育解说腔，最适合报分 |
| `zh-CN-XiaoxiaoNeural` | 晓晓 · 温暖女声 | 最自然的通用女声 |
| `zh-CN-XiaoyiNeural` | 晓伊 · 活泼少女 | 年轻、上扬 |
| `zh-CN-YunxiNeural` | 云希 · 阳光少年 | 清亮男声 |
| `zh-CN-YunxiaNeural` | 云夏 · 可爱童声 | 童声 |
| `zh-CN-YunyangNeural` | 云扬 · 专业播报 | 新闻腔 |
| `zh-CN-liaoning-XiaobeiNeural` | 小北 · 东北话 | 方言，牌局气氛担当 |
| `zh-CN-shaanxi-XiaoniNeural` | 小妮 · 陕西话 | 方言 |
| `zh-HK-HiuGaaiNeural` / `zh-HK-WanLungNeural` | 曉佳 / 雲龍 · 粤语 | 女 / 男 |
| `zh-TW-HsiaoChenNeural` / `zh-TW-YunJheNeural` | 曉臻 / 雲哲 · 台湾腔 | 女 / 男 |
| `en-US-AriaNeural` / `en-US-GuyNeural` | Aria / Guy · 英文 | 女 / 男 |

在 PWA **设置页 → 语音音色**下拉里切换（列表由 `/voices` 动态拉取）；也可在 `POST /tts` 的 `voice` 字段直接指定。传了不存在的 id **不会报错**，会自动降级到该语言的默认音色——这样旧客户端存的过期设置不会让游戏突然哑掉。

语速用 `rate` 控制（`"+10%"` / `"-20%"` 或纯数字），默认 `+10%`，可用环境变量 `EDGE_RATE` 改全局默认。

---

## 2. 本地跑（调试用）

```bash
cd voice-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # 只有 4 个包，十几秒装完
python app.py                            # 默认 tts 模式，端口 7860
```

改了前端网页后**务必先同步再构建**，否则镜像里还是旧页面：

```bash
bash sync-web.sh          # 把上层最新的 CABO_v*.1.html 同步进 web/
```

测试：

```bash
curl http://localhost:7860/healthz     # {"ok":true,"mode":"tts","voice_count":14,...}
curl http://localhost:7860/voices      # 音色清单

curl -X POST http://localhost:7860/tts \
  -H 'content-type: application/json' \
  -d '{"text":"恭喜小明卡波成功","lang":"zh","voice":"zh-CN-liaoning-XiaobeiNeural"}' \
  --output out.mp3
```

> Windows 下用 curl 发中文 body 容易被终端编码搞乱导致 400，改用 Python 的 `urllib.request` 发（默认 UTF-8）更稳。

**实测数据**（本机，`tts` 模式）：14 音色全部合成成功，单句 1.3–11 秒（平均约 2 秒，首次握手偏慢），约 18KB/句；进程常驻 **70MB**，6 路并发后升到 73MB。

---

## 3. 部署到云端

GitHub Pages 只能托管静态页、跑不了 Python。本镜像把网页和语音接口装在一起，部署完拿到的那**一个 HTTPS 域名就是全部**。

### `tts` 模式（默认，推荐）

内存 70MB，**免费档随便挑**：

| 平台 | 免费档内存 | 结论 |
|------|-----------|------|
| **Render Free** | 512MB | ✅ 够用且免费。缺点是 15 分钟无请求会休眠，冷启动约 30 秒（只是拉起进程，不加载模型） |
| **Google Cloud Run** | 512MiB 起 | ✅ 缩容到零、冷启动更快，个人用量在免费额度内 |
| Railway / Koyeb / Fly | 256MB–1GB | ✅ 同样能跑 |

Render：Dashboard → New → **Blueprint** → 选本仓库，读 `render.yaml` 即可。

Cloud Run：

```bash
cd voice-service
gcloud config set project 你的项目ID
bash deploy-cloudrun.sh
```

构建只需 **2–3 分钟**（不装 torch、不下权重）。

### `rvc` 模式（需要角色音色时）

镜像会额外装 `requirements-rvc.txt`（torch 等）并从 Hugging Face `feikong66/cabo-rvc-models` 下载权重，构建 15–25 分钟：

```bash
docker build --build-arg VOICE_MODE=rvc -t cabo-rvc .
# 运行时也要设 VOICE_MODE=rvc
```

内存要求 600–800MB，**Render Free / Starter（都是 512MB）必被 OOM**（Starter 花了钱也不加内存，别白花）；请用 Cloud Run 2GiB 或 Render Standard（2GB，约 $25/月）。

可选音色 10 个：`nanami`（奈奈美，默认）、`lwjhh`、`daboluo`、`jaychou`、`venti`、`march7`、`ayaka`、`luotianyi`、`yexiu`、`jackie`。加音色就把 `.pth`（+可选 `.index`）丢进 `models/`，在 `config.json` 的 `rvc.models` 加一项。

省内存开关：`RVC_F0_METHOD=pm` 可省约 180MB 的 rmvpe 模型（音高精度略降）；`INCLUDE_INDEX=0`（默认）不打包 `.index`，镜像和内存都显著变小。

---

## 4. 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `VOICE_MODE` | `tts` | `tts` 或 `rvc` |
| `PORT` | `7860` | 平台通常自动注入 |
| `EDGE_VOICE_ZH` / `EDGE_VOICE_EN` | 云健 / Aria | 默认音色 |
| `EDGE_RATE` | `+10%` | 默认语速 |
| `RVC_DEFAULT_MODEL` / `RVC_MODEL` / `RVC_INDEX` / `RVC_PITCH` / `RVC_DEVICE` / `RVC_F0_METHOD` | — | 仅 `rvc` 模式 |

---

## 5. 在前端（CABO PWA）里配置

**一体化部署下：什么都不用配。** 打开部署地址，网页探测同源 `/healthz`，命中即自动启用 AI 配音。

只有网页和语音服务**分开部署**时（例如网页放 GitHub Pages）才需要手填：

1. **设置页 → 语音服务地址**，填部署得到的地址，点「测试连接」。
2. 地址**必须是 `https://`**：HTTPS 页面调 HTTP 接口会被按混合内容拦截，`http://192.168.x.x` 和 `http://localhost` 在手机上都不可能成功。
3. 留空 → 回落到系统自带 TTS。

---

## 6. 排错

**先看健康检查**：

```bash
curl -s https://你的地址/healthz
```

- `mode` 是不是你想要的（部署上去发现是 `tts` 但你想要 `rvc`，就是环境变量没设）；
- `rvc` 模式下关注 `engine_loaded` / `engine_error`——**引擎坏掉时服务照样开网页、健康检查照样通过**，唯一症状是手机默默用回自带嗓音，不查这里很难发现。

其它常见问题：

- **返回 502 且日志出现 `coroutine ... was never awaited`**：`asyncio.run()` 不能在已运行的事件循环里嵌套调用。异步处理器请 `await synthesize_async()`，同步场景才用 `synthesize()`（它内部会检测循环并回落到线程池）。
- **手机没声音但电脑正常**：多半是没走这个代理，直连微软被 UA 校验挡了 403（见开头）。确认前端请求的是同源 `/tts`。
- **合成偶发失败**：微软侧限流或网络抖动，重试即可；前端已有回落到系统 TTS 的兜底。
- **中文混英文**：Edge TTS 按 `lang` 选音色，建议混排文本统一语言传入。
- **`rvc` 模式构建卡在 fairseq**：它没有 Python 3.9+ 预编译包，`Dockerfile` 已做三级回退（固定版本源码 → 社区 fork → 裸装）。
- **`rvc` 模式声音太尖/太闷**：调对应模型的 `pitch`（半音）。
