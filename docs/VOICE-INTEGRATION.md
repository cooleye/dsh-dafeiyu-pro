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

## 怎么启用

### 1. 换成 fork 版本

当前 profile 装的是上游 npm 包 `dsh-dafeiyu`。要换成本仓库：

```powershell
cd D:\project\dsh-dafeiyu-pro

# 先卸掉上游版本（否则会有两个桌宠窗口）
dsh plugin --profile desktop remove dsh-dafeiyu

# 装本仓库（link 模式，改代码实时生效）
dsh plugin --profile desktop add link:D:\project\dsh-dafeiyu-pro
```

> ⚠️ 两条命令都**需要先完全退出 DSH 桌面端**，
> 因为正在运行时会占用插件文件。

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
