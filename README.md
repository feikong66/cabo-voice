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
| **Google Cloud Run**（2GiB，缩容到零） | 2GiB | ✅ **推荐**，个人用量基本在免费额度内 |
| Render Free / Starter | 512MB | ❌ 必被 OOM；Starter 付了钱也还是 512MB |
| Render Standard | 2GB | ✅ 可用，约 $25/月 |
| Hugging Face Space（免费 CPU） | 16GB | ✅ 内存最宽裕，但跑 Python 需 PRO |

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
python app.py            # 或： uvicorn app:app --port 7860
```

> 推理走 `rvc-python` **进程内常驻**：首次请求加载 HuBERT（约 30–60 秒），之后每次合成只有几秒。基础模型 `hubert_base.pt` / `rmvpe.pt` 由 `rvc-python` 首次初始化时自动下载（镜像已在构建阶段预下载）。

**改了前端网页后，务必先同步再构建**，否则镜像里还是旧页面：

```bash
bash sync-web.sh          # 把上层最新的 CABO_v*.1.html 同步进 web/
```

测试：

```bash
curl http://localhost:7860/healthz       # 健康检查（含引擎状态）
curl http://localhost:7860/models        # 列出可用模型

curl -X POST http://localhost:7860/tts \
  -H 'content-type: application/json' \
  -d '{"text":"恭喜小明卡波成功","lang":"zh","model":"nanami"}' --output out.wav
```

---

## 3. 部署到云端

GitHub Pages 只能托管静态页、**跑不了 Python**。本镜像把网页和语音接口装在一起，部署完拿到的那一个 HTTPS 域名就是全部——**手机打开即用，不用填地址**。

### 方案 A：Google Cloud Run —— 推荐

选它的理由很实在：**2GiB 内存够跑**，而且**没人用时自动缩容到零**，个人偶尔打牌的用量基本落在免费额度内。

```bash
cd voice-service
gcloud config set project 你的项目ID     # 项目需已开通结算（免费额度内也要求开通）
bash deploy-cloudrun.sh
```

脚本会自动启用所需 API、建好 Artifact Registry 仓库、构建并部署，最后打印访问地址。**首次构建约 15–25 分钟**（装 CPU 版 torch + 下载约 550MB 权重）。

想换区域或只打包一个音色（镜像更小、构建更快）：

```bash
REGION=asia-northeast1 MODEL_ALLOW='nanami*' bash deploy-cloudrun.sh
```

关于费用和冷启动，有两点要有心理预期：

- **冷启动 30–60 秒**。缩容到零之后，第一次开口要等实例拉起并加载模型。不想等就保持常驻，但**会离开免费额度、开始按小时计费**：
  ```bash
  gcloud run services update cabo --region asia-east1 \
    --min-instances=1 --no-cpu-throttling
  ```
- **免费额度按用量计**（每月 180,000 vCPU-秒 / 360,000 GiB-秒）。按本服务 2 vCPU + 2GiB 换算，约合每月 **25 小时**的实际处理时间；合成一句话只占几秒，正常打牌远远用不完。额度以官方页面为准。

### 方案 B：Render

能跑，但**必须上 Standard（2GB，约 $25/月）**。免费档和 Starter 都是 512MB，**一定会 OOM**——注意 Starter 花了钱也不加内存，别白花。

Dashboard → New → **Blueprint** → 选本仓库，它会读 `render.yaml`（其中 `plan: standard` 已经写好）。

### 方案 C：Hugging Face Spaces（需 PRO）

HF 免费档只给 Static SDK，跑 Python（Gradio/Docker）会在创建时返回 **402**。已开通 PRO 才走这条，好处是免费 CPU 档有 16GB 内存。

### 其它平台

Railway / Koyeb / Fly 同样支持 Docker 构建，选一个**内存 ≥ 1GB** 的档位即可，端口由 `$PORT` 自动注入，无需改代码。

---

## 4. 在前端（CABO PWA）里配置

**一体化部署下：什么都不用配。** 打开 Cloud Run 给的地址，网页会探测同源 `/healthz`，命中就自动启用 RVC。

只有当网页和语音服务**分开部署**时（例如网页仍放在 GitHub Pages）才需要手动填地址：

1. **设置页 → RVC 语音服务地址**，填部署得到的地址，点「测试连接」。
2. 地址**必须是 `https://`**：HTTPS 页面调用 HTTP 接口会被浏览器按混合内容直接拦截，`http://192.168.x.x` 和 `http://localhost` 在手机上都不可能成功。
3. 留空 → 自动使用系统 TTS。

音色在 **设置页 → RVC 音色** 下拉里选，列表由服务动态提供。

---

## 5. 排错

**先看健康检查**，它会直接告诉你引擎有没有起来：

```bash
curl -s https://你的地址/healthz
```

关注返回里的 `engine` 字段：

- `engine_loaded: true` → RVC 正常；
- `engine_error` 是一段非空字符串 → RVC 挂了，内容就是原因（最常见是 `fairseq` 没装上）。

这一步很关键：**引擎坏掉时服务照样能开网页、健康检查照样通过**，唯一症状就是手机默默用回自带嗓音，不查这里很难发现。

其它常见问题：

- **第一次说话很慢**：冷启动加载 HuBERT，属正常；之后就快了。缩容到零的平台每次休眠后都会重来一次。
- **构建卡在 fairseq**：它没有 Python 3.9+ 的预编译包，`Dockerfile` 已做三级回退（固定版本源码 → 社区 fork → 裸装）。三条都失败才会中断，日志里能看到具体是哪条。
- **声音太尖/太闷**：调对应模型的 `pitch`（半音）。
- **不像目标角色**：调高 `index_rate`；若镜像未打包 `.index`（默认不打包），相似度本身会略降，可用 `INCLUDE_INDEX=1` 重建。
- **内存被 OOM 杀掉**：设 `RVC_F0_METHOD=pm` 省掉约 180MB 的 rmvpe，或换更大内存档位。
- **中文混英文**：Edge TTS 按 `lang` 选音色，建议混排文本统一语言传入。
