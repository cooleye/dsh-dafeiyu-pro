# 接进桌宠（集成说明）

本文档说明怎么把语音能力装到桌宠插件里，以及它跟终端版的关系。

---

## 架构

```
DSH 主进程 (Node)
    │
    ├── helper-process.js ──► helper.py（Qt 桌宠窗口）
    │                          透明置顶、动画、气泡
    │
    └── voice-bridge.js ────► voice_service.py（语音服务）
                               唤醒 / 识别 / 合成 / 分流 / 派活
```

**为什么不合并成一个进程**：

| 理由 | 说明 |
|---|---|
| 免重打包 | helper 是 PyInstaller 打的 Qt exe（45MB），加音频和网络依赖要重打 |
| 故障隔离 | 语音挂了桌宠还能动，桌宠挂了语音还能用 |
| 复用协议 | 两者都是「JSONL over stdio」，同一套调试工具 |

---

## 状态怎么映射

语音服务推 `state` 事件，插件转成桌宠状态：

| 语音状态 | 桌宠状态 | 动画 |
|---|---|---|
| `idle` | IDLE | 待机 |
| `listening` | WAITING | 等待（竖耳朵） |
| `hearing` | WAITING | 正在听你说话 |
| `thinking` | THINKING | 思考 |
| `speaking` | WORKING | 说话 |
| `busy` | WORKING | 跑 agent 任务 |

**文字进气泡**：

| 事件 | 气泡显示 |
|---|---|
| `user_text` | 你说：…… |
| `reply_end` | 大肥鱼：…… |
| `task_start` | 派活：…… |
| `permission_ask` | 需要授权：要写文件，等你答复 |

---

## 语音指令一览

唤醒后可以对这些话说：

### 退下（回到待命）

说这些会**闭嘴、停任务、停止监听**，直到下次叫「大肥鱼」：

```
退下吧 / 退下 / 退出 / 你走吧 / 走开 / 去休息 / 睡吧
没事了 / 不聊了 / 先这样 / 拜拜 / 再见 / 关了吧
```

回一句「好，那我退下了」（**本地 SAPI 念**，1 毫秒出声，不依赖网络）。

> 为什么用本地合成：退下的时候可能会断网/关程序，
> 云端 TTS 要 1 秒起还可能失败。这种短句本地念最稳。

**识别规则比较严**：必须以退下词开头，后面最多跟 4 个字。
所以「你不是说退下这个词什么意思」不会被误判成退下。

### 停止任务

任务跑着的时候说：

```
停止任务 / 取消 / 别干了 / 停下 / 算了别做了 / 不做了
```

会立刻杀掉 DSH agent 的进程树，无孤儿残留。

> 说「退下」也会顺带取消任务——都退下了，跑完也没人听。

### 问进度

任务跑着的时候说任何别的，它会告诉你：

```
「还在忙，已经跑了 12 秒。说停止可以取消」
```

气泡里还会持续更新：

```
干活中：45s · 调用工具：write
```

---

Windows 有四种音频后端，**独占性完全不同**：

| 后端 | 模式 | 能否和别的程序共存 | 延迟 |
|---|---|---|---|
| **WASAPI** | 共享 | ✅ | 中 |
| DirectSound | 共享 | ✅ | 中 |
| MME | 共享 | ✅ | 高 |
| **WDM-KS** | **独占** | ❌ | 低 |

**默认走 `shared`**（WASAPI 优先），因为：

> 一开始为了低延迟选了 WDM-KS，结果 DSH 自己的语音输入报
> `Device in use` —— 桌宠把麦克风独占了，DSH 就抢不到。
> 整个语音输入功能都用不了。

用 `shared` 之后实测：

```
语音服务用 WASAPI 开着麦
  → 第二个进程同时开 WASAPI  ✅
  → 第二个进程同时开 MME     ✅
```

所以桌宠和 DSH 的语音输入可以**同时**用。

### 什么时候用 exclusive

- 你只用桌宠、不用 DSH 的语音输入
- 而且特别在意延迟（比如唤醒反应速度）

```powershell
python phase4_assistant.py --audio-mode exclusive
```

或者在设置页把「音频后端模式」改成「独占」。

### 自动回退

选定的后端打不开时，会自动换下一个（顺序见上方优先级表），
整轮失败还会等 1.5 秒重试一次（设备刚被释放时会有延迟）。

> ⚠️ 别用「预试开」来判断设备可用性 —— 实测反复开关设备会把
> 音频子系统搞挂，连本来能用的 WDM-KS 都变成 `Invalid device`。
> 正确做法是：按优先级挑一个，真开流失败了再换。

---

## 换音色

### 能换哪些

`qwen3-tts-flash` 实测可用 12 个音色（按音高从高到低，女声为主）：

| 音色 | 风格 | 说明 | 实测基频 |
|---|---|---|---|
| **Bella** | **萝莉音** | **默认**，最可爱 | 364 Hz |
| Momo | 萝莉音 | 软糯 | 343 Hz |
| Mia | 少女音 | 清亮 | 289 Hz |
| Kiki | 少女音 | 俏皮 | 276 Hz |
| Cherry | 少女音 | 甜美 | 242 Hz |
| Serena | 少女音 | 温柔 | 238 Hz |
| Chelsie | 少女音 | 活泼 | 242 Hz |
| Sunny | 元气女声 | 明亮 | 286 Hz |
| Jada | 高音女声 | 很尖 | 400 Hz |
| Katerina | 御姐音 | 低沉有气场 | 225 Hz |
| Ethan | 男声 | 低沉 | 137 Hz |
| Peter | 男声 | 沉稳 | 197 Hz |

> 基频是本鱼用自相关算法实测的中位数，越高越接近童声。
> Bella 364Hz 是目前最「萝莉」的一个。

### 怎么换（三个办法）

**办法一：桌宠右键菜单（最快）**

在桌宠身上点右键 → **音色** → 选一个。

选完立即生效，不用重启。

**办法二：设置页下拉框**

```
设置 → 插件 → dsh-dafeiyu-pro → TTS 音色  ▾
```

**办法三：命令行热改**

```powershell
$u = 'http://127.0.0.1:1752/plugins/dsh-dafeiyu/config'
Invoke-WebRequest -Uri $u -Method Patch -UseBasicParsing `
  -Body (@{voiceTts='Momo'} | ConvertTo-Json) -ContentType 'application/json'
```

### 先试听再选

```powershell
cd D:\project\dsh-dafeiyu-pro
$env:PYTHONIOENCODING='utf-8'

python preview_voices.py                 # 全部念一遍（约 30 秒）
python preview_voices.py --voice Bella   # 只听一个
python preview_voices.py --save          # 存成 wav 不播放
```

### 为什么能热改

音色只是 `Speaker` 上的一个字段，换掉之后**下一句合成**就用新的。
不动正在念的那句（半句换声音会很怪）。

其它配置（音频设备、对话模型、agent 开关）还是要重启语音服务才生效。

### 右键菜单是怎么接起来的

```
helper.py 右键菜单选中音色
    ↓ emit_reply("settings", voice="Momo")
helper-process.js 转成 onSettingsChange
    ↓
plugin.js 写成 settings.voiceTts
    ↓ settings.watch 触发
voice.updateConfig({voice:"Momo"})
    ↓
voice_service.py → Assistant.set_voice()
    ↓
下一句就用新音色
```

helper 自己不出声，只发一条消息，所以菜单响应很快。

---

## 固定短句也用云端音色（+ 磁盘缓存）

唤醒回应的「我在」和退下的「好，那我退下了」这两句，
**也走云端 TTS**，所以和你选的音色完全一致。

但为了不慢、不花钱，做了磁盘缓存：

```
第一次唤醒 → 云端合成一次（约 0.5 秒）→ 存成 WAV
之后每次   → 直接读磁盘（约 1 毫秒）→ 播放
```

缓存在：

```
%LOCALAPPDATA%\dsh-dafeiyu\voice-cache\<音色+文本的哈希>.wav
```

**换音色会自动失效**（哈希里带音色名），下次唤醒重新合成一次。

### 成本

基本为零。这两句一共 11 个字，合成一次就够。
即使每次换音色，也只是重新合成 11 个字。

### 为什么不用本地 SAPI

一开始为了「秒回」用了 Windows 自带的 SAPI（微软 Huihui），
问题是**音色和你选的 Bella 完全是两个人**，唤醒那一下声音突变，很割裂。

云端 + 缓存之后：音色一致 ✅ 延迟接近 0 ✅ 成本一次性 ✅

### 兜底

云端彻底拿不到（没网 / 没 key / 超时）时才退回本地 SAPI ——
声音会变，但至少还能应一声。

### 踩到的坑：Qwen TTS 的 WAV 头在撒谎

它返回的 WAV 里 data 块长度是个占位值（约 10 亿帧），
按 `readframes(getnframes())` 去读会申请 2GB 缓冲，
**每次卡 500ms 以上**（比重新合成还慢）。

修复：先读全部字节，按真实文件大小夹一个上限，
用 `readframes(-1)` 读到 data 块实际结束。改完从 700ms 降到 1ms。

---

## 字幕和声波

### 头顶气泡显示字幕

说话时桌宠头顶的气泡会实时显示文字：

```
你说：今天天气怎么样呀            ← 你的话（ASR 识别结果）
大肥鱼：今天晴，二十度，适合出门     ← 它的回复（边生成边显示）
```

派活、进度、授权请求也会显示在这里：

```
派活：写一个 Markdown 文件
干活中 · 调用工具：write
要写文件，等你答复
```

退下或回到待命时自动清空。

### 脚下声波

桌宠脚下有一排声波条，一眼看出当前状态：

| 状态 | 颜色 | 行为 |
|---|---|---|
| 听你说话 | 蓝色 | 跟麦克风实时音量跳动 |
| 它在说话 | 橙色 | 跟说话节奏跳动 |
| 待命 / 思考 | 不显示 | — |

### 为什么单独开一种消息类型

字幕没有复用现有的 `STATE` / `TASK`，原因很实际：

- **`STATE`**：每次状态变化（听→想→说）都会用空 message 覆盖气泡，
  字幕会「一闪就没」。所以语音期间遇到空 message 要沿用当前字幕。
- **`TASK`**：几秒后过期，而字幕要一直留到下一句。

所以加了独立的 `voice` 消息：只更新字幕和音量，不动宠物动画状态。

### 消息链路

```
voice_service（音量 10Hz）
    ↓ {kind:'level', level:0.62, activity:'listening'}
    ↓ {kind:'user_text' / 'delta', text:'...'}
voice-bridge.js
    ↓
plugin.js → VOICE 消息（字幕做 100ms 节流）
    ↓
helper.py → _apply_voice() → 更新气泡 + 声波 → update()
```

### 调试：怎么看某一刻的画面

helper 支持拍一张诊断快照：

```powershell
$env:DSH_DAFEIYU_SNAPSHOT_DELAY_MS='3500'   # 默认 180ms
runtime\bin\win32-x64\dsh-dafeiyu-helper.exe --snapshot out.png
```

然后从 stdin 喂几条 `voice` 消息，就能截到「正在说话」的那一帧。
本鱼就是靠这个确认声波画对了的（顺便发现衰减逻辑写错，只剩最后一根条在动）。

---

## 怎么启用

### 1. 换成 fork 版本

当前 profile 装的是上游 npm 包 `dsh-dafeiyu`。要换成本仓库：

**方式 A：插件管理页（推荐，不用命令行）**

```
设置 → 插件 → 添加插件 → 填 link:D:\project\dsh-dafeiyu-pro
然后卸掉旧的 dsh-dafeiyu
```

**方式 B：命令行**

`dsh` 命令不在 PATH 里，要么用完整路径，要么用 `npx`：

```powershell
# 完整路径（DeepSeek Harness 桌面端自带）
& 'D:\Program Files (x86)\deepseek\resources\runtime\cli\bin\dsh.cmd' `
    plugin --profile desktop remove dsh-dafeiyu

& 'D:\Program Files (x86)\deepseek\resources\runtime\cli\bin\dsh.cmd' `
    plugin --profile desktop add link:D:\project\dsh-dafeiyu-pro
```

> ⚠️ 两条命令都**需要先完全退出 DSH 桌面端**，
> 因为正在运行时会占用插件文件。

### 1.5 ⚠️ 补齐预编译 helper（link 安装必看）

`runtime/bin/` 在上游 `.gitignore` 里（各平台构建产物，不进版本库），
所以从源码 link 安装时**桌宠窗口会因为找不到 helper 而启动失败**。

从已安装的副本捞回来：

```powershell
# 1. 找到之前安装留下的 helper
Get-ChildItem "$env:LOCALAPPDATA\dsh-dafeiyu" -Recurse -Filter 'dsh-dafeiyu-helper-*.exe'

# 2. 复制到仓库里它期望的位置
New-Item -ItemType Directory -Path 'D:\project\dsh-dafeiyu-pro\runtime\bin\win32-x64' -Force
Copy-Item "$env:LOCALAPPDATA\dsh-dafeiyu\<版本号>\dsh-dafeiyu-helper-<哈希>.exe" `
          'D:\project\dsh-dafeiyu-pro\runtime\bin\win32-x64\dsh-dafeiyu-helper.exe'
```

或者自己构建：

```powershell
cd D:\project\dsh-dafeiyu-pro
npm run build:helper:windows
```

> 💡 没有 helper 时的退化路径：用 `python runtime/helper.py` 跑，
> 但要求系统 Python 装了 `PySide6`（`requirements.txt` 里那个）。

### 2. 确认依赖

```powershell
# 语音服务要能 import 这些
python -c "import sherpa_onnx, sounddevice, numpy; print('ok')"
```

缺的话：

```powershell
python -m pip install sherpa_onnx sounddevice numpy -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 准备模型

KWS 模型不能进版本库（含个人语音数据），要放在：

```
D:\project\dsh-dafeiyu-pro\kws-model\
├── kws_encoder.onnx
├── kws_decoder.onnx
├── kws_joiner.onnx
├── tokens.txt
└── keywords.txt     ← 内容是 d à f éi y ú @大肥鱼
```

从 desk-ai 复制过来即可：
```powershell
Copy-Item D:\project\desk-ai\src-tauri\kws\* D:\project\dsh-dafeiyu-pro\kws-model\ -Exclude keywords.txt
```

### 4. API Key

语音服务从 `apikey.txt` 读（和终端版一样）：

```
qwen:sk-xxxxxxxx       ← ASR / TTS / 对话（必需）
bochaai:sk-xxxxxxxx    ← 搜索（可选）
deepseek:sk-xxxxxxxx   ← DSH agent（跑长任务必需）
```

位置：`D:\project\dsh-dafeiyu-pro\apikey.txt`
或 `D:\project\desk-ai\apikey.txt`（会自动回退去找）

### 5. 在设置页打开语音

重启 DSH 桌面端 → 左侧 **插件** → **dsh-dafeiyu-pro** → 打开 **启用语音对话**。

配置项：

| 项 | 默认 | 说明 |
|---|---|---|
| 启用语音对话 | 关 | 总开关 |
| 唤醒词 | 大肥鱼 | 要和 keywords.txt 一致 |
| TTS 音色 | Cherry | Cherry / Serena / Chelsie / Ethan |
| 对话模型 | qwen-flash | qwen-turbo 在这个 key 上常 429 |
| 长任务交给 agent | 开 | 关掉就只聊天+搜索 |
| 写文件前语音确认 | 开 | 关掉则永不升权 |
| Python 解释器 | python | 跑语音服务的那个 |

---

## 验证

开语音后，说「大肥鱼」：

- **桌宠窗口的鱼动起来**（不是终端打印）
- 气泡显示「你说：……」
- 回答时气泡显示「大肥鱼：……」

如果只看到终端输出、桌宠没反应，检查：

1. `voiceEnabled` 是不是 true
2. DSH 日志里有没有 `dsh-dafeiyu voice bridge started`
3. `python voice_service.py --selftest` 能不能过

---

## 手动调试语音服务

不经过插件，直接跑协议：

```powershell
# 协议自检（不开麦）
python voice_service.py --selftest

# 手动喂命令
"{\"kind\":\"start\"}" | python voice_service.py --stdio
```

> ⚠️ stdout 是协议流，日志都在 stderr。
> 如果你看到 stdout 里有「💬 你说」这种，说明重定向没生效 —— 那是 bug。

---

## 终端版和桌宠版的关系

| | 终端版（phase4_assistant.py） | 桌宠版（voice_service.py） |
|---|---|---|
| 入口 | 直接跑 | 插件 spawn |
| 输出 | 终端打印 | JSONL 给插件 |
| 桌宠动画 | 无 | 有 |
| 气泡 | 无 | 有 |
| 适合 | 调试、测参数 | 日常使用 |

**同一套语音逻辑**（`phase4_assistant.Assistant`），
只是换了个「事件出口」（`voice_events.py` 里的 `ConsoleEvents` / `JsonlEvents`）。

所以你调参数时可以在终端里试，改完不用改两遍。
