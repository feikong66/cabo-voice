# CABO 一体化服务（计分网页 + Edge TTS + RVC）

一个容器同时提供 **CABO 计分网页** 和 **RVC 变声后端**，共用一个 HTTPS 域名。

链路：`文本 → Edge TTS 念出来（中性源音色）→ RVC 转成目标角色音色 → 返回 WAV`

## 为什么要合成一个服务

分开部署时，手机上的 RVC 永远用不了，原因是两条浏览器安全规则：

- **混合内容**：网页是 `https://`，语音服务是 `http://` → 请求被直接拦截；
- **跨域**：即使语音服务上了 HTTPS，跨域 `POST /tts` 还要额外配置 CORS。

网页和接口同源之后，这两条同时消失，**手机打开网址就能用，无需填任何地址**——前端启动时会探测同源 `/healthz`，命中就自动启用 RVC。

## 接口

| 路由 | 说明 |
|------|------|
| `GET /` | CABO 计分网页 |
| `GET /healthz` | 存活探测 + 各音色是否就位 |
| `GET /models` | 音色列表 |
| `POST /tts` | `{text, lang, model}` → `audio/wav` |

## 内存要求（重要）

容器常驻 `torch + HuBERT + 一个音色模型`，实测稳态约 **600–800MB**：

| 平台档位 | 内存 | 结论 |
|----------|------|------|
| Render Free / Starter | 512MB | ❌ 会被 OOM 杀掉 |
| Render Standard | 2GB | ✅ 可用 |
| Google Cloud Run（2GB，缩容到零） | 2GB | ✅ 个人用量基本在免费额度内 |
| Hugging Face Space（免费 CPU） | 16GB | ✅ 内存最宽裕 |

省内存的开关：设 `RVC_F0_METHOD=pm` 可跳过约 180MB 的 rmvpe 模型（音高精度略降）；`INCLUDE_INDEX=0`（默认）不打包 `.index` 文件，镜像和内存都显著变小。

---

## 0. 已内置的模型（权重托管在 Hugging Face）

10 个音色，约 1.7GB 权重，托管在 **`feikong66/cabo-rvc-models`**（Hugging Face 公开 model repo）。**GitHub 仓库不含模型文件**——`Dockerfile` 在构建时会自动从这里下载到镜像里，部署无需手动上传大文件。要换/加模型，直接更新那个 HF repo 即可。

| id | 名称 | 文件 | 索引 |
|----|------|------|------|
| `nanami`  | 奈奈美（女声，带特征索引，音质最好） | `nanami_e500.pth` | `nanami_v2.index` |
| `lwjhh`   | LWJHH | `LWJHH-Final-2.pth` | 无 |
| `daboluo` | 大菠萝 | `daboluo.pth` | 无 |
| `jaychou` | 周杰伦 | `jaychou.pth` | `jaychou.index` |
| `venti`   | 原神·温迪 | `venti.pth` | `venti.index` |
| `march7`  | 三月七 | `march7.pth` | `march7.index` |
| `ayaka`   | 神里绫华 | `ayaka.pth` | `ayaka.index` |
| `luotianyi` | 洛天依 | `luotianyi.pth` | `luotianyi.index` |
| `yexiu`   | 叶修 | `yexiu.pth` | `yexiu.index` |
| `jackie`  | 成龙 | `jackie.pth` | `jackie.index` |

> 默认音色为 `nanami`。在 PWA **设置页 → RVC 音色**下拉里可切换（下拉会从服务动态拉取这份列表）；调用时也可在 `POST /tts` 的 `model` 字段指定。
> 想加更多音色：把 `.pth`（+可选 `.index`）丢进 `models/`，再在 `config.json` 的 `rvc.models` 加一项即可。

---

## 1. 自定义 / 换模型

把训练好的文件放进 `models/`，然后在 `config.json` 的 `rvc.models` 下加一项：

```json
"rvc": {
  "default_model": "nanami",
  "models": {
    "nanami":  { "label_zh":"奈奈美", "label_en":"Nanami", "pth":"models/nanami_e500.pth", "index":"models/nanami_v2.index", "pitch":0 },
    "myvoice": { "label_zh":"我的音色", "label_en":"My Voice", "pth":"models/my.pth", "index":"models/my.index", "pitch":0 }
  }
}
```

> 不想改文件，可用环境变量覆盖默认模型路径：`RVC_DEFAULT_MODEL` / `RVC_MODEL` / `RVC_INDEX` / `RVC_PITCH` / `RVC_DEVICE` / `EDGE_VOICE_ZH` / `EDGE_VOICE_EN`。

---

## 2. 本地跑（调试用）

```bash
cd voice-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# RVC 仓库会在 app.py 启动时自动克隆到 ./rvc（无需手动准备）
uvicorn app:fastapi_app --port 7860
# 或： python app.py
```

> 服务已开启 **CORS（`allow_origins=["*"]`）**，所以手机/浏览器从 GitHub Pages 等跨域页面也能直接调用 `/tts`，不会被浏览器拦截。
> 想让手机用上 RVC：在本机跑起服务后，把管理员面板/设置页的「RVC 语音服务地址」填成 `http://<本机局域网IP>:7860`（手机连同一 Wi-Fi），保存即自动同步到其他设备。

测试：

```bash
curl -X POST http://localhost:7860/tts \
  -H 'content-type: application/json' \
  -d '{"text":"恭喜小明卡波成功","lang":"zh","model":"nanami"}' --output out.wav

curl http://localhost:7860/models        # 列出可用模型
curl http://localhost:7860/healthz       # 健康检查
```

---

## 3. 部署到云端（推荐，手机/好友都能用）

GitHub Pages 只能托管静态页，**跑不了 Python**，所以语音服务必须单独部署。最省事的是**免费 Docker 主机**（不需要 HF PRO）。

### 方案 A：免费 Docker 主机（Railway / Render / Koyeb / Fly）——推荐

这些平台都有免费 CPU 额度，直接用仓库里的 `Dockerfile` 构建。**模型权重在构建时自动从 Hugging Face 拉取**，你不用自己传 1.7GB 大文件。

**通用步骤**（以 GitHub 仓库为来源）：

1. 把本目录（`voice-service/`）推到一个 GitHub 仓库。
2. 在平台新建服务，**连接该 GitHub 仓库**，**构建方式选 Docker / Dockerfile**（不要选 Node）。
3. 平台会自动读 `Dockerfile`：`git clone` RVC 官方仓库 → 装 torch(CPU) → 从 `feikong66/cabo-rvc-models` 下载模型 → 启动 `app.py`。
4. 端口：`app.py` 读 `$PORT`（Railway/Render 自动注入），默认 7860，一般无需手动设。
5. 拿到地址（如 `https://cabo-voice.up.railway.app`），在 CABO 云端版 **管理员面板 → RVC 语音服务地址** 填入即可。

**各平台要点：**

| 平台 | 免费档 | 注意 |
|------|--------|------|
| **Railway** | $5 额度/月（够玩很久） | New → Deploy from GitHub repo → 选 Dockerfile；默认能联网拉取 HF/RVC。 |
| **Render** | 512MB RAM，自动休眠 | New Web Service → 选仓库 → Runtime 选 `Docker`；免费实例休眠后首次请求慢。 |
| **Koyeb** | nano 实例（0.1 vCPU/512MB） | 连接 GitHub → 选 Dockerfile；有免费额度。 |
| **Fly.io** | 3 个共享 CPU VM | `fly launch` 用 Dockerfile；免费额度有限。 |

> ⚠️ RVC CPU 推理约需 1–2GB 内存，免费档最低配可能偏紧；若 OOM，升级实例档位或在 `config.json` 降低并发。首次构建要装 torch + RVC 并下载模型，**约 5–15 分钟**。`PORT` 由平台注入。PWA 已做「失败自动降级系统 TTS」，服务挂了也不影响计分。

### 方案 B：Hugging Face Spaces（Gradio SDK，**需 PRO 订阅**）

> HF 免费档**只支持 Gradio / Static SDK，且运行 Python（Gradio）也需要 PRO**，否则创建时返回 402。已开通 PRO 才走这条：

```bash
pip install -U huggingface_hub
export HF_TOKEN=hf_xxxxxxxxxxxx
export SPACE_ID=feikong66/cabo-voice
python _push_hf.py        # Space 用 Gradio SDK 创建，权重走 Hub LFS
```

> 部署完在 Settings 打开 **Public**（或设 Access Token），否则前端跨域拉不到。

---

## 4. 在前端（CABO PWA）里配置

云端版 CABO：
1. 进入 **管理员设置**（管理员面板），「**RVC 语音服务地址**」填部署得到的地址（如 `https://cabo-voice.up.railway.app` 或 HF Space 地址）。
2. 在 **设置页 → RVC 音色** 下拉里选择音色（`nanami / jaychou / venti / march7 / ayaka / luotianyi / yexiu / jackie / lwjhh / daboluo`，下拉会从服务动态拉取）。
3. 留空地址 → 自动用系统 TTS（Web Speech）。

---

## 5. 排错

- **RVC 推理报错 / 没输出**：多半是 `infer_cli.py` 参数名与 fork 不一致。改 `config.json` 的 `rvc.infer_args_template`（占位符：`{model} {index} {input} {output} {pitch} {sample_rate} {f0_method} {index_rate} {filter_radius} {resample_sr} {rms_mix_rate} {protect}`）。
  - 官方 RVC-Project 的 `infer_cli.py` 通常**还需要 `-c <config.json>`**（和 `.pth` 同目录的那个配置文件）。如果日志报 `the following arguments are required: -c/--config`，把默认模板加上 `-c {config}` 并在 config.json 里给每个模型补 `"config": "models/xxx.json"`。
  - 也可以改 `rvc.infer_script` / `rvc.repo` 指向你自己的 RVC fork 的 `infer_cli.py`。
- **声音太尖/太闷**：调对应模型的 `pitch`（半音）。
- **不像目标角色**：调高 `index_rate`，或确认 `sample_rate` 与训练时一致。
- **中文混英文**：Edge TTS 按 `lang` 选音色，建议混排文本统一语言传入。
