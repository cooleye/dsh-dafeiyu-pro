# dsh-dafeiyu 语音交互扩展 · 设计文档

> 状态：设计稿 v2（已吸收 desk-ai 实战数据，未开工）
> 日期：2026-11-09
> 基线：`dsh-dafeiyu` 0.1.23（Python helper 1271 行 + Node 侧插件 611 行）
> 目标：给桌宠加上「语音唤醒 → 流式对话 → 分级路由」能力

---

## 目录

1. [目标与范围](#1-目标与范围)
2. [现状基线](#2-现状基线)
3. [参考实现：desk-ai](#3-参考实现desk-ai)
4. [核心设计：三层路由](#4-核心设计三层路由)
5. [模块设计](#5-模块设计)
6. [协议扩展](#6-协议扩展)
7. [配置与密钥](#7-配置与密钥)
8. [踩坑清单（实战验证）](#8-踩坑清单实战验证)
9. [技术风险](#9-技术风险)
10. [路线图](#10-路线图)
11. [待确认决策](#11-待确认决策)
12. [附录](#12-附录)

---

## 1. 目标与范围

### 1.1 要做的事

把现在这个「只会显示状态」的桌宠，变成能**语音对话**的伙伴：

- 叫它「大肥鱼」就能唤醒
- 唤醒后能听你说话（流式识别）
- 能张口回应（流式合成）
- 闲聊走快路径，干活走 DSH agent
- 有可配置的角色性格
- 有记忆，能记住聊过的重要事情

### 1.2 明确不做的事

| 不做 | 原因 |
|---|---|
| 本地跑大模型做识别/合成 | 本地只负责唤醒，ASR/TTS 全走云端 |
| 替换 DSH 的 agent loop | 长任务仍然由 DSH 承担 |
| 摄像头视觉 | 独立议题，本文档不覆盖 |
| 自研语音模型 | 用厂商现成 API |

### 1.3 设计原则

1. **本地只做唤醒和垫场** —— 低延迟、低功耗、高确定性的事放本地
2. **重活全部上云** —— 识别、合成、理解都走网络
3. **分流优先于等待** —— 与其优化延迟，不如让不该慢的路不要慢
4. **两条路，两套记忆** —— 闲聊和任务是不同物种，别强行统一

---

## 2. 现状基线

### 2.1 进程结构

```
DSH 主进程 (Node / Electron)
    │  订阅 session/event 根总线
    │  reducer 算出 7 种状态
    │
    │  ┌── newline-delimited JSON over stdio ──┐
    └──┤                                         │
       ▼                                         │
独立 Python 进程 (PySide6 / Qt6)  ◄──────────────┘
    └─ FramelessWindowHint | WindowStaysOnTopHint | Tool
       + WA_TranslucentBackground
```

### 2.2 关键文件

| 路径 | 行数 | 作用 |
|---|---|---|
| `src/plugin.js` | 611 | Node 侧：配置 Schema、session 事件订阅、HTTP 端点 |
| `src/companion-reducer.js` | 20.9 KB | 事件 → 宠物状态的状态机 |
| `src/helper-process.js` | 31.4 KB | 子进程生命周期、协议读写、背压 |
| `src/protocol.js` | 66 行 | 14 种消息类型定义 + 校验 |
| `src/balance.js` | 11.4 KB | 余额查询（可复用的凭据访问模式） |
| `runtime/helper.py` | 1271 行 | Qt 窗口、动画、交互、光标 |
| `assets/pet/` | 3444 帧 | 16 组动作的 WebP 序列 |

### 2.3 现有能力可复用的部分

- ✅ **`ping`/`pong` 心跳** —— 扩展消息类型照抄这个模式
- ✅ **快照合并**（`coalescibleMessageKinds`）—— 语音的高频事件也要合并
- ✅ **背压处理**（`stdin.write` 返回 false 时等 `drain`）
- ✅ **热更新配置**（`CONFIG` 消息，不重启 helper）
- ✅ **凭据访问模式**（`balance.js` 读 API Key 的方式，可抄）
- ✅ **SSE 广播**（`/plugins/dsh-dafeiyu/events`，给网页版用）

### 2.4 Qt 打包现状（关键约束）

`_MEI` 解压目录里确认存在的 Qt 模块：

```
Qt6Core / Gui / Network / OpenGL / Pdf / Qml / QmlMeta
/ QmlModels / QmlWorkerScript / Quick / Svg / VirtualKeyboard / Widgets
```

**没有 QtMultimedia**，也没有 `cv2` / `numpy`。

⇒ 录音和播放**都不能走 QtMultimedia**，必须用 `sounddevice` 之类的独立音频栈。

---

## 3. 参考实现：desk-ai

### 3.1 为什么这份参考价值极高

`D:\project\desk-ai` 是一个已经跑通的 Tauri 2 + Rust 语音桌宠，**完整实现了本文档要做的所有事**，并留下了大量实战数据。

| 维度 | desk-ai | dsh-dafeiyu |
|---|---|---|
| 壳 | Tauri 2（WebView2） | PySide6（Qt6） |
| 语言 | Vue 3 + TS / Rust | JS / Python |
| 本地引擎 | Rust 直接调 C API | 纯 Python |
| 音频 | Web Audio + getUserMedia | 需自建 |
| 体积 | 16.7 MB | 45.7 MB |
| 唤醒/ASR | Rust 侧（低延迟） | Python 侧（够用） |

**核心差异**：desk-ai 在浏览器环境里，`getUserMedia` 自动处理了重采样、AEC、降噪；dsh-dafeiyu 在 Qt 里，这些**全要自己做**。这是最大的移植成本。

### 3.2 可直接复用的资产

```
D:\project\desk-ai\src-tauri\kws\          KWS 模型 + DLL（约 5 MB）
  ├── kws_encoder.onnx      4.8 MB
  ├── kws_decoder.onnx      181 KB
  ├── kws_joiner.onnx       65 KB
  ├── tokens.txt            1.6 KB
  ├── keywords.txt          63 B      ← 格式样本
  ├── sherpa-onnx-c-api.dll 4.2 MB
  └── onnxruntime.dll       17 MB

D:\project\desk-ai\src-tauri\asr\          中英双语 ASR（58 MB，可选）
```

**KWS 模型规格**（`zipformer-wenetspeech-3.3M`，约 5 MB）：

```rust
FeatureConfig   { sample_rate: 16000, feature_dim: 80 }
max_active_paths:     4
num_trailing_blanks:  1
keywords_score:       3.0     ← 灵敏度旋钮
keywords_threshold:   0.1     ← 阈值旋钮
num_threads:          1
provider:             "cpu"
```

### 3.3 唤醒词格式（已验证可用）

`keywords.txt` 的实际格式：

```
n ǐ h ǎo x iǎo zh ì @你好小智
ài l ì s ī @爱丽丝
```

**规则**：拼音音节用空格分隔、带声调、`@` 后跟显示文本。

「大肥鱼」照此写：

```
d à f éi y ú @大肥鱼
```

一行可以写多个词（`拼音 @词1/拼音 @词2`），`#` 开头是注释。

---

## 4. 核心设计：三层路由

### 4.1 总体流程图

```
┌──────────────────────────────────────────────────────────────┐
│                        唤醒层（本地）                          │
│  KWS 常驻监听 → 命中「大肥鱼」→ 本地 TTS「我在」(50-200ms)     │
└───────────────────────────┬──────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                     识别层（云端流式）                         │
│  麦克风 → 降噪门控 → VAD 断句 → WebSocket 流式 ASR              │
└───────────────────────────┬──────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                      分流层（意图判定）                        │
│  规则命中 → 小模型分类 → 置信度低则兜底                         │
└───────┬─────────────────┬─────────────────┬──────────────────┘
        ↓                 ↓                 ↓
   ┌─────────┐      ┌──────────┐      ┌───────────────┐
   │  闲聊    │      │  查数据   │      │   长任务       │
   └─────────┘      └──────────┘      └───────────────┘
        │                 │                 │
   直连 LLM          直连 LLM +         DSH agent
   （无工具）         有限工具集          （全工具）
        │                 │                 │
   1-2 秒回话        1-2 秒回话       先垫场「好的，我看看」
        │                 │                 ↓
        │                 │            超过 8 秒补一句
        │                 │            「这个要花点时间」
        │                 │                 ↓
        │                 │            气泡显示进度
        ▼                 ▼                 ▼
   ┌────────────────────────────────────────────────┐
   │           合成层（云端流式 TTS）                 │
   │  边收边播，支持中途打断                          │
   └────────────────────────────────────────────────┘
```

### 4.2 三条路的职责边界

| 路径 | 触发条件 | 延迟目标 | 工具能力 | 记忆来源 |
|---|---|---|---|---|
| **闲聊** | 寒暄、情绪、玩笑、日常对话 | 1–2s | 无 | 闲聊记忆 |
| **查数据** | 天气、新闻、时间、搜索 | 1–2s | 天气/搜索/时间 API | 闲聊记忆 |
| **长任务** | 写文档、改代码、查资料、做开发 | 垫场即时，结果异步 | DSH 全工具 | 任务记忆 |

### 4.3 分流策略（三级）

```
第一级：规则命中（0ms）
  强任务词：文档 / 代码 / 写一个 / 帮我改 / 查一下资料 / 部署 / 调试
  → 直接判为长任务

第二级：小模型分类（300–500ms）
  用便宜快模型（如 qwen-turbo）判断意图类别
  输出：chat | data | task

第三级：兜底（默认 chat）
  - 分不清 → 默认闲聊路（有工具，可自己升级）
  - 兜底路也要有搜索/天气工具，让「顺便查下天气」能就地解决

强制覆盖：唤醒词变体
  「大肥鱼」        → 自动分流
  「大肥鱼，帮我干活」→ 强制 task
```

### 4.4 垫场话术的两级设计

**为什么是两级**：分流发生在「你说完话」那一刻，但任务要多久只有进 agent 才知道。

```
判定为长任务
    ↓
立即（<200ms）：本地 TTS「好的，我看看」
    ↓
进入 DSH agent，开一个 8 秒计时器
    ↓
    ├── 8 秒内完成 → 直接朗读结果
    └── 超过 8 秒 → 本地 TTS 补一句「这个要花点时间，你先忙别的」
                     ↓
                  继续等待，气泡显示实时进度
                     ↓
                  完成后朗读摘要 + 日志写入
```

**关键约束**：垫场话术必须走**本地 TTS**。云端往返 500ms–1s，撑不起这个体验。

### 4.5 会话窗口与「叫我才应答」

desk-ai 的实现里有一个极其重要的机制：**连续对话窗口**（`freeSpeechUntil`）。

```
窗口内说的话 → 不用再叫名字
窗口外说的话 → 必须重新叫（唤醒词 / 角色名），否则忽略
```

**这是过滤「旁人说话、电视声被当成提问」最有效的一招**。日志里 `[环境音过滤] "XXX" 未叫我且不在对话窗口内，忽略` 出现了 74 次。

---

## 5. 模块设计

### 5.1 进程划分

```
┌──────────────────────── Node 侧（DSH 插件）────────────────────────┐
│  plugin.js         配置 Schema / 设置页                              │
│  voice-router.js   ★新：意图分流                                     │
│  voice-llm.js      ★新：直连 LLM（闲聊路）                           │
│  voice-bridge.js   ★新：ASR/TTS 厂商适配（阿里/MiniMax）              │
│  session-bridge.js ★新：注入 DSH 会话（长任务路）                     │
│  companion-reducer.js  现有：状态机                                  │
│  helper-process.js     现有：子进程管理（扩展协议）                   │
└────────────────────────────┬────────────────────────────────────────┘
                             │ JSONL over stdio
┌──────────────────────────── Python 侧（Qt helper）─────────────────┐
│  helper.py         现有：窗口/动画/交互（扩展消息处理）               │
│  audio_capture.py  ★新：录音（16k mono PCM）                         │
│  audio_play.py     ★新：播放（支持中途停止）                          │
│  gate.py           ★新：降噪门控（RMS/过零率/有声占比）               │
│  vad.py            ★新：语音活动检测（可复用 silero）                 │
└──────────────────────────────────────────────────────────────────────┘
```

### 5.2 KWS 放哪一侧？

这是个关键决策。

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. Node 侧（用 `sherpa-onnx-node`）** | DSH 里已有原生模块；不用碰 Python | 音频要从 Python 传到 Node，有延迟 | 可行 |
| **B. Python 侧（`sherpa-onnx` Python 包）** | 音频不出进程 | 需要打包 Python 版 sherpa，exe 增大 | 可行 |
| **C. Python 侧直接 ctypes 调 DLL** | 复用 desk-ai 的 DLL，零下载 | 要手写结构体对齐（desk-ai 的 `kws.rs` 有完整参照） | 可行 |

**推荐 B**：`pip install sherpa-onnx` 即可，Python API 是现成的，不用手写 C 结构体。

**但模型文件可以直接用 desk-ai 的**（省一次下载）。

### 5.3 ★ 为什么 ASR/TTS 的网络调用放 Node 侧

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A. Python 侧直连厂商** | 音频不用跨进程传 | 要加 `websockets`/`httpx` 依赖，exe 变大 | ❌ |
| **B. Node 侧调用，音频走协议** | 复用 DSH 已有网络栈；便于限流/计费统计 | 音频要跨管道 | ✅ **推荐** |
| C. 混着来 | 灵活 | 两套网络依赖，维护成本翻倍 | ❌ |

**选 B 的理由**：Node 侧已有 `undici`、SSE、HTTP 服务器，API Key 管理、限流、计费统计天然在 Node 这边。

**音频传输优化**：不 Base64，改用**临时 WAV 文件 + 只传路径**，或分帧的二进制消息。

---

## 6. 协议扩展

### 6.1 现有协议

```js
PROTOCOL_VERSION = 1
Kinds: ready / hello / state / pulse / alert / task / tasks
       / config / balance / settings / ping / pong / closed / shutdown
```

### 6.2 新增消息类型

```js
// Python → Node
'wake'          { keyword, confidence, at }          唤醒命中
'asr-partial'   { text, seq }                        流式中间结果
'asr-final'     { text, seq }                        最终结果
'audio-chunk'   { data | path, seq }                 音频数据
'playback-done' { seq }                              播放完成
'playback-stop' { seq, reason }                      被打断

// Node → Python
'say'           { text, voiceId?, priority, seq }     要求说话
'say-stream'    { chunk, seq, final }                 流式合成结果
'listen-start'  { timeoutMs }                         开始听
'listen-stop'   { reason }                            停止听
'route'         { kind: 'chat'|'data'|'task', text }  分流结果（驱动动画）
```

### 6.3 ⚠️ 版本兼容策略

`protocol.js` 的 `assertCompanionMessage` 对未知 `kind` 直接抛错：

```js
if (!kinds.has(value.kind)) throw new TypeError(...)
```

**推荐**：提升到 `PROTOCOL_VERSION = 2`，改为**协商式**——

```js
// ready 消息带能力声明
{ kind: 'ready', protocolVersion: 2, capabilities: ['voice', 'kws', 'tts'] }
```

Node 收到后决定发哪些消息。旧 helper 没有 `capabilities`，只发 v1 消息。**同一个插件同时兼容新旧 helper**。

### 6.4 ⚠️⚠️ requestId 幂等陷阱（desk-ai 实测踩过）

`dsh-api-session-controller` 的 `prompt()`：

```js
if (hasPromptRequest(agent, request.requestId)) return { accepted: true }
```

**同名 requestId 会被静默吞掉**。

desk-ai 的 `cloud_asr.rs` 里有一段完全对应的教训：

> 会话身份。收尾时靠它判断「我现在退的是不是别人顶替过的槽」。
> 这个字段是修一个致命 bug 加的：一段话被切成多段时，前端会新建会话顶掉旧的，
> 旧会话几百毫秒后才真正退出。之前退出时无条件 `*g = None`，
> 把**新会话**的发送端一起清掉了 —— 之后每一帧 feed 都报"会话未启动"，
> 表现就是"识别完一段后继续说，它彻底不识别了"，且要等下一段 newSegment 才恢复。

**对策**：

```js
// ❌ 危险
requestId = hash(text)

// ✅ 安全
requestId = `${sessionId}:${++counter}:${Date.now()}`
```

---

## 7. 配置与密钥

### 7.1 新增配置项

在 `plugin.js` 的 `Config` Schema 里追加：

```js
// ── 唤醒 ──
wakeEnabled:        boolean  default(true)     '启用语音唤醒'
wakeWord:           string   default('大肥鱼') '唤醒词'
wakeTimeoutMs:      number   default(10000)    '唤醒后等待时长'
wakeSensitivity:    '低'|'中'|'高'  default('中')
kwsScore:           number   1.0–5.0  default(3.0)    'KWS 灵敏度'
kwsThreshold:       number   0.05–0.5 default(0.1)    'KWS 阈值'

// ── ASR ──
asrProvider:        'aliyun'|'minimax'|'local'  default('aliyun')
asrRegion:          string   default('cn')
asrLanguage:        string   default('zh-CN')

// ── TTS ──
ttsProvider:        'aliyun'|'minimax'|'local'  default('aliyun')
ttsVoiceId:         string   default('Cherry')  '音色 ID'
ttsSpeed:           number   0.5–2.0  default(1.0)
ttsLocalFallback:   boolean  default(true)

// ── 路由 ──
routeMode:          'auto'|'always-task'|'chat-only'  default('auto')
routeClassifierModel: string default('')        '分类小模型（空=用规则）'
forceTaskPhrase:    string   default('帮我干活')
requireAddress:     boolean  default(true)      '必须叫名字才回应'

// ── 垫场 ──
stallPhrase1:       string   default('好的，我看看')
stallPhrase2:       string   default('这个要花点时间，你先忙别的')
stallDelayMs:       number   default(8000)

// ── 记忆 ──
memoryEnabled:      boolean  default(false)
memoryMaxFacts:     number   default(200)       '事实库上限'
memoryMaxTokens:    number   default(800)       '注入上限'
memorySensitiveFilter: boolean default(true)

// ── 成本保护 ──
dailyCallLimit:     number   default(200)
showUsageStats:     boolean  default(true)
```

### 7.2 ⚠️ API Key 存储：绝不明文

**禁止**写进 `cordis.patch.yml` 或 `config.json`（明文，备份时易泄露）。

desk-ai 的做法值得借鉴：`wincred.rs` 直接调 `advapi32` 的 `CredWriteW / CredReadW / CredDeleteW`，零依赖，凭据在「控制面板 → 凭据管理器」里可见。

**但 dsh-dafeiyu 应该走 DSH 的 `credentials` 服务**：

```js
// 现在的 inject
export const inject = ['sessions', 'settings']

// 扩展后
export const inject = ['sessions', 'settings', 'credentials']
```

**参照物**：`src/balance.js` 已有成熟模式。

**desk-ai 的安全边界说明写得很诚实**，值得沿用这个表述：

> - ✅ 防得住：`keys.json` 被同步盘带走、被备份、被发给别人、换台电脑打开
> - ❌ 防不住：能以你的账号在本机运行的程序

### 7.3 设置页 UI

```
┌─ 语音 ────────────────────────────┐
│ ☑ 启用语音唤醒                     │
│   唤醒词  [大肥鱼        ]          │
│   灵敏度  (低) (中) (高)            │
│   等待时长 [10] 秒                 │
│ ☑ 必须叫名字才回应                  │
├─ 识别 ────────────────────────────┤
│   服务商  [阿里云 ▾]                │
│   API Key [sk-****abcd] [更换]      │
│   语言    [中文 ▾]                  │
├─ 合成 ────────────────────────────┤
│   服务商  [阿里云 Qwen-TTS ▾]       │
│   API Key [sk-****efgh] [更换]      │
│   音色    [Cherry ▾] [试听]         │
│   语速    ────●──── 1.0             │
│ ☑ 云端失败时用本地 TTS              │
├─ 路由 ────────────────────────────┤
│   模式    (自动) (总是任务) (仅闲聊) │
│   分类模型 [qwen-turbo ▾]           │
├─ 今日用量 ─────────────────────────┤
│   ASR 23 次 / TTS 41 次 ≈ ¥0.42    │
└────────────────────────────────────┘
```

---

## 8. 踩坑清单（实战验证）

> 以下全部来自 desk-ai 的源码注释与 50,970 行运行日志，**不是推测**。

### 8.1 🔥 DLL 加载：onnxruntime 劫持

`kws.rs` 的注释（原文）：

> **关键：先用绝对路径预加载 onnxruntime.dll。**
> system32 里有系统自带的旧版 onnxruntime（1.17），若不预加载，
> sherpa-onnx-c-api.dll 的依赖会被旧版抢先解析（API 版本不匹配崩溃）；
> 预加载后进程内已有 1.28 模块，依赖解析直接复用。

**影响**：不预加载会直接崩溃，且报错信息毫无指向性。

**对策**：DSH 里已有 `sherpa-onnx-win-x64/onnxruntime.dll`，加载前先绝对路径 `LoadLibrary` 一次。

### 8.2 🔥 阿里云 WebSocket：双向 `"streaming": "duplex"`

`cloud_asr.rs` 注释（原文）：

> 1. `run-task` 和 `finish-task` 的 header **都必须带 `"streaming": "duplex"`**。
>    少了 finish 那一份，服务端会**完全静默，一个字节都不回**（表现为 recv 超时），
>    而且不报错 —— 这是最费时间的一个坑。
> 2. **不能走系统代理**：代理会拦 WebSocket 握手（实测直接超时）。
> 3. **结果按句推送**：`sentence_end=true` 才是完整句，中间的是逐字增长的 partial。

**对策**：三条都写进实现，尤其是第 1 条——它只会表现为「超时」，不会报错。

### 8.3 🔥 云端识别会话被误清空（静默失败）

见 §6.4。现象是「识别完一段后继续说，彻底不识别」，要等下一个语音段才恢复。

**根因**：旧会话退出时无条件清空全局 `Option`，把新会话的发送端也清了。

**对策**：会话带自增 id，退出时只清自己的槽（`if g.as_ref().map(|s| s.id) == Some(id)`）。

### 8.4 ⚠️ 静音切段阈值：800ms 太短

注释（原文）：

> 长句子中间换气、想下一个词，800ms 很容易超 —— 一超就把一句话切成两段，
> 前半段已经送去回复，后半段再单独成句，用户感觉就是"说到一半它就不听了"。
> 1.05 秒仍然远小于"说完一句"的自然停顿，但能吃掉大部分换气空档。

**实测参数**：

| 场景 | 阈值 |
|---|---|
| 待命态 | 800ms |
| **对话态** | **1050ms** |
| 放音乐时 | 段长上限压到 3200ms |

### 8.5 ⚠️ 单段录音上限：12 秒太紧

> 12 秒对"说长句"太紧：背一段诗、讲一件事很容易超过，中途被切断就成了两次独立提问。
> 放到 18 秒能覆盖绝大多数情况。

**放歌时必须压小**（3200ms）：音乐连续有声，VAD 永远等不到静音，段长会顶到上限，用户夹在中间说的「下一首」会被淹没在十几秒歌词里。

### 8.6 ⚠️ 待命态「长度窗口」是省钱关键

> 「你好小智」四个音节典型 0.8~2 秒；电视/旁人连续对话通常远超此长度。
> **超出直接丢弃、不送云端 ASR**。这是待命态省 ASR 费用的主要手段。

**日志实证**：`[待命省费] 段长 Nms 超出唤醒词窗口，不送识别` —— **1892 次**。

### 8.7 ⚠️ 打断参数：0.05 偏高，降到 0.035

> `getUserMedia` 已开启 echoCancellation/noiseSuppression，
> AI 自身播报回传经 AEC 后通常 <0.015；贴着麦克风说话一般 >0.04。
> 之前 0.05 偏高，小声说话打不断，降到 0.035。

**日志实证**：352 次打断，RMS 均值 **0.080**，最小 **0.035**，最大 **0.292**。

**注意**：0.035 这个值依赖 AEC。**Qt + sounddevice 没有 AEC**，所以这个阈值要重新标定——很可能需要更高（比如 0.06+），或者干脆换「设备级 AEC」方案。

### 8.8 ⚠️ 打断后保留 1 秒音频重喂

```typescript
BARGE_IN_KEEP_MS = 1000
```

**避免「刚打断的半句话丢失」**。这个细节很容易漏，但直接影响体验。

### 8.9 ⚠️ ASR 幻觉短语要过滤

日志实证命中（2 次）：

```
"这个字幕上就不能全部显示出来了。啊，它就显示不全..."
"在这个字幕上方剧中的位置"
```

**这是真实痛点**：ASR 听到背景音（视频、音乐）会输出字幕套话。

过滤词表（直接可用）：

```typescript
["谢谢观看","感谢观看","请订阅","订阅","点赞","字幕","出品","独家",
 "禁止转载","未经允许","网易云音乐","qq音乐","伴奏","纯音乐",
 "bilibili","哔哩哔哩"]
```

### 8.10 ⚠️ 回声守卫

```typescript
ECHO_GUARD_MS = 3000   // 播放结束后 3 秒内，识别到「AI 自己的话」认为是回声
```

**实现**：记录 `replyText`，识别结果与它高度相似就丢弃。

### 8.11 ⚠️ 本地模型的语言陷阱

`asr.rs` 注释：

> 原来的 wenetspeech-small 只训练过中文，用户说英语会被强行转成中文谐音
> （"Could you say it again" → "三儿追婷卡特拉"）。

**实测数据**：

| 模型 | 中文 CER | 英文 WER |
|---|---|---|
| 纯中文 small | 0% | **100%** |
| 双语 exp/32 | 2.1% | 11.6% |
| 双语 exp/64 | **62.5%** | 严重丢字 |

**教训**：同一个双语模型换了 chunk 尺寸就崩了，**别乱调 chunk**。

### 8.12 ⚠️ 云端 ASR 实测明显优于本地

`cloud_asr.rs` 注释原文：

> 本地 58MB 双语小模型英文 WER 11.6%（标准朗读音），实际带口音时更差
> （"but tomorrow I have" → "BUTTER MICHA HARVA"）。
> 同一批 12 句音频实测云端：**英文 WER 0.0%、中文 2.1%**，
> 而且自带标点（本地模型输出全大写、无标点）。

**结论**：这直接支撑了「云端 ASR」的选型决策。

### 8.13 ⚠️ 降噪门控：过零率是最有效的过滤器

日志实证（637 次噪声过滤）：

| 判据 | 命中次数 | 占比 |
|---|---|---|
| **过零率过高**（>0.32） | **451** | 70.8% |
| 语音过短（<500ms） | 163 | 25.6% |
| 有声帧占比低（<40%） | 21 | 3.3% |
| 幻觉短语 | 2 | 0.3% |

**过零率是主力**——键盘敲击、碰撞、高频噪声的过零率显著高于人声。

### 8.14 ⚠️ TTS 的两个实际故障

日志里：

```
[语音错误] TTS 任务失败：Please ensure input text is valid.   （5 次）
[语音错误] TTS 业务错误 (1008)：insufficient balance           （2 次）
[音频播放] EncodingError: Unable to decode audio data          （16 次）
```

**对策**：
1. 空文本/纯符号要在发送前过滤
2. 余额不足要单独提示（不能只报「失败」）
3. 解码失败要有重试（16 次说明不是偶发）

### 8.15 ⚠️ 唤醒词不在词表时的降级

日志：

```
[WARN] [本地唤醒] 唤醒词「爱丽丝」不在本地词表（你好小智）中，待命将用语音识别匹配
```

**行为**：KWS 词表里没有的词，自动降级为「云端 ASR 转写 + 字符串匹配」。

**代价**：降级路径会烧钱（待命态一直跑 ASR）。**这解释了为什么它只是个 WARN 不是 ERROR**——功能能用，但成本上去了。

### 8.16 ⚠️ 开发期大量 `ReferenceError` 是 HMR 副作用

日志里 13019 条 ERROR 中，**12916 条是同一个**：

```
IndexSizeError: Failed to execute 'arc' on CanvasRenderingContext2D: The radius provided (-0.9) is negative
```

其余是 `heartbeat is not defined`、`rescueIfLeftExpanded is not defined` 等——**全是 Vite HMR 热更新时的模块边界问题**，不是线上故障。

**教训**：日志分级要区分「开发噪声」和「真实故障」，否则真正的语音错误会被淹没。

---

## 9. 技术风险

### 🟢 已消除的风险

| 原风险 | 状态 | 依据 |
|---|---|---|
| ~~KWS 模型不存在~~ | ✅ 已解决 | 模型在 desk-ai 里现成（5MB） |
| ~~唤醒词格式不明~~ | ✅ 已确认 | `n ǐ h ǎo x iǎo zh ì @你好小智` |
| ~~云端 ASR 效果差~~ | ✅ 已排除 | 实测英 0.0% / 中 2.1% |
| ~~流式协议不会写~~ | ✅ 有参照 | `cloud_asr.rs` 完整实现 |

### 🟠 剩余风险

| # | 风险 | 严重度 | 缓解 |
|---|---|---|---|
| 1 | **Qt 没有 AEC** | 高 | 打断阈值需重新标定；或加设备级 AEC |
| 2 | **音频栈要自建** | 高 | `sounddevice` + 自己重采样到 16k mono |
| 3 | **意图分流准确率** | 中 | 三级策略 + 兜底 + 强制词 |
| 4 | **PyInstaller 打包** | 中 | 加 `sounddevice`/`websockets`，模型不打包 |
| 5 | **云端成本失控** | 中 | 待命窗口 + 日限 + 用量显示 |
| 6 | **会话注入目标选择** | 低 | 专用语音会话 + 可切换 |
| 7 | **角色设定双源不一致** | 低 | 复用 persona 插件 |

### 重点说明：风险 1（没有 AEC）为什么严重

desk-ai 的打断阈值 0.035 是**建立在浏览器 AEC 之上**的：

- AEC 把自身播报消到 <0.015
- 用户说话 >0.04
- 中间留 0.035 做闸门

**Qt + sounddevice 没有 AEC**，播放的声音会被麦克风原样收回来，能量可能 >0.05。这会导致：

- 阈值设低了 → 它自己在说话时把自己打断
- 阈值设高了 → 你说话打不断

**三条可能的解法**：

1. **用 WASAPI 的回环消除**（Windows 支持，但 Python 支持有限）
2. **播放时暂停录音**（简单，但打断就没了）
3. **软件 AEC**（如 `speexdsp`、`webrtc-audio-processing`，有 Python 绑定）

**建议**：Phase 1 先用方案 2（播放时暂停录音）保底，Phase 4 再考虑方案 1 或 3。

---

## 10. 路线图

### Phase 0 · 唤醒 PoC（**已完成 ✅**）

**目标**：叫「大肥鱼」→ 气泡有反应

**实测结果**：

| 项目 | 结果 |
|---|---|
| 模型 | sherpa-onnx KWS（zipformer-wenetspeech-3.3M） |
| 模型体积 | 4.8 MB |
| 唤醒词 | `d à f éi y ú @大肥鱼` |
| **首次实测** | **3 次全中** ✅ |
| 参数 | score=3.0 / threshold=0.1（沿用 desk-ai 验证值） |
| 采样率 | 设备 48k → 重采样 16k |
| 录音后端 | **WDM-KS**（其他三个后端都被占用） |

**踩坑记录**：

1. **sherpa-onnx 2.0 的 Python API 变了**
   文档还是 1.x 的写法（`KeywordSpotterConfig` 对象），2.0 改成了扁平参数：
   ```python
   # 2.0 实际写法
   sherpa_onnx.KeywordSpotter(tokens=..., encoder=..., decoder=...,
                              joiner=..., keywords_file=...,
                              keywords_score=3.0, keywords_threshold=0.1)
   ```

2. **麦克风被独占（Windows）**
   四个音频后端实测：

   | 后端 | 结果 |
   |---|---|
   | MME | ❌ PortAudioError -9999 |
   | Windows DirectSound | ❌ 同上 |
   | Windows WASAPI | ❌ 同上 |
   | **Windows WDM-KS** | ✅ **成功** |

   WDM-KS 是内核流，绕开系统混音器，所以不被别的程序独占。
   **对策**：按 WDM-KS → WASAPI → DirectSound → MME 顺序自动降级。

3. **声卡默认不是 16k**
   绝大多数设备是 44.1k / 48k，模型要 16k。desk-ai 在浏览器里靠 Web Audio
   自动重采样，我们这边必须自己做。

**产出**：
- `phase0_kws_poc.py` —— 自检 / 实时监听 / WAV 测试 / 批量评估
- `collect_samples.py` —— 采集正负样本
- `record_wav.py` —— 单次录音

**下一步**：采集负样本，测假阳性率。只看「三次都命中」不够，
还要知道「该不该叫都叫」的比例。

---

### Phase 1 · 音频栈 + 本地出声

**目标**：能录音、能播放、能播「我在」

**工作**：
- `sounddevice` 录音（48k → 重采样 16k mono PCM16）
- `sounddevice` 播放（支持中途停止）
- 本地 TTS 兜底（sherpa-onnx OfflineTts 或 Windows SAPI）
- 降噪门控（RMS / 过零率 / 有声占比）
- 协议扩展 `say` / `playback-done` / `playback-stop`

**关键**：这是**移植风险最高的一段**，浏览器帮 desk-ai 做的重采样/降噪都要自己做

**估时**：5–6 天

---

### Phase 2 · 云端流式 ASR

**目标**：你说完，文字出现在气泡里

**工作**：
- 阿里云 WebSocket 接入
- **双向 `"streaming": "duplex"`**（§8.2）
- **会话 id 隔离**（§8.3）
- 断句参数（对话态 1050ms，§8.4）
- 幻觉过滤 + 回声守卫
- 协议扩展 `asr-partial` / `asr-final`

**估时**：5–6 天

---

### Phase 3 · 意图分流 + 直连 LLM（**分水岭**）

**目标**：闲聊能 1–2 秒回话

**工作**：
- `voice-router.js`：三级分流
- `voice-llm.js`：直连 LLM（带天气/搜索工具）
- 角色设定接入（复用 persona）
- 连续对话窗口（`freeSpeechUntil`，§4.5）

**估时**：4–6 天

---

### Phase 4 · 接 DSH agent

**目标**：长任务能派活，有垫场

**工作**：
- `session-bridge.js`：注入会话（**requestId 递增**，§6.4）
- 两级垫场话术
- 8 秒计时器
- 打断机制（含 1 秒重喂，§8.8）

**估时**：4–5 天

---

### Phase 5 · 记忆

**目标**：记住重要的事

**工作**：
- 两套记忆分离（闲聊 / 任务）
- **非向量方案**：关键词命中 × 类别保底 × 重要度 × 时间新鲜度（desk-ai 已验证）
- 每条事实必须带 `source`（用户原话），防幻觉
- 记忆上限 200 条 + 注入上限 800 token
- 敏感内容过滤

**估时**：4–5 天

---

### 总估时

**约 23–29 个工作日**（比 v1 少，因为 Phase 0 大幅缩短）。

含调试、打包、踩坑，**实际 30–40 天**。

---

## 11. 待确认决策

| # | 决策点 | 本鱼建议 | 状态 |
|---|---|---|---|
| 1 | KWS 放 Python 还是 Node | Python（`sherpa-onnx` 包现成） | ⏳ |
| 2 | 没 AEC 怎么做打断 | Phase 1 先「播放时暂停录音」保底 | ⏳ |
| 3 | 「你好呀，顺便查下天气」算哪条路 | 闲聊，但配天气工具 | ⏳ |
| 4 | 要不要「强制干活」唤醒词 | 要，避免期望漂移 | ⏳ |
| 5 | 角色设定复用 persona 还是新建 | 复用，但做精简版 | ⏳ |
| 6 | ASR 用阿里 / TTS 用阿里还是 MiniMax | 阿里全家桶（desk-ai 已验证 Qwen-TTS 效果好） | ⏳ |
| 7 | 语音会话独立还是注入当前 | 独立 + 可切换 | ⏳ |
| 8 | 唤醒词「大肥鱼」是否要加英文别名 | 先只做中文，实测后再说 | ⏳ |

---

## 12. 附录

### 12.1 DSH 内置的语音基础设施

从 `app.asar` 确认存在：

```
@sherpa-onnx-node/           ← JS 封装
  ├── keyword-spotter.js     ← KWS
  ├── non-streaming-asr.js
  ├── streaming-asr.js       ← 流式 ASR
  ├── non-streaming-tts.js   ← TTS
  ├── vad.js                 ← VAD
  ├── punctuation.js
  └── speaker-identification.js

sherpa-onnx-win-x64/
  ├── sherpa-onnx.node
  ├── sherpa-onnx-c-api.dll
  ├── onnxruntime.dll            ← 同样要防被 system32 劫持
  └── onnxruntime_providers_shared.dll

@deepseek-ai/dsh-experimental-speech-to-text/
@deepseek-ai/dsh-experimental-speech-to-text-sensevoice/
@deepseek-ai/dsh-experimental-voice-input-bundle/
@deepseek-ai/dsh-experimental-api-speech-to-text/
```

**注意**：官方 ASR 服务只支持**完整录音转写，不支持流式**，所以必须直连厂商 API。

### 12.2 已有的本地模型

```
C:\Users\iscoo\.dsh\speech-to-text\sensevoice\models\
├── sensevoice-onnx\model.int8.onnx   239 MB   ASR（中文 CER 优于 desk-ai 那个）
├── sensevoice-onnx\tokens.txt        316 KB
└── silero\silero_vad.onnx            1.8 MB   VAD
```

**没有 KWS 模型** → 从 desk-ai 复制。

### 12.3 desk-ai 实测参数汇总表

| 参数 | 值 | 来源 |
|---|---|---|
| VAD_START_RMS | 0.03 | 对话态起说话 |
| VAD_END_RMS | 0.012 | 迟滞收尾 |
| SILENCE_FLUSH_MS | 800 | 待命态 |
| **SILENCE_FLUSH_MS_DIALOGUE** | **1050** | 对话态（关键） |
| MIN_SPEECH_MS | 500 | 最短有效语音 |
| MIN_VOICED_RATIO | 0.4 | 有声帧占比 |
| MAX_ZCR | 0.32 | 过零率上限 |
| MAX_SEGMENT_MS | 18000 | 单段上限 |
| MAX_SEGMENT_MS_MUSIC | 3200 | 放歌时 |
| ECHO_GUARD_MS | 3000 | 回声保护期 |
| IDLE_VAD_START_RMS | 0.04 | 待命态起说话 |
| **IDLE_MAX_SPEECH_MS** | **3000** | 待命态长度窗口（省钱关键） |
| BARGE_IN_RMS | 0.035 | 打断阈值（**依赖 AEC**） |
| BARGE_IN_KEEP_MS | 1000 | 打断后重喂 |
| KWS keywords_score | 3.0 | 灵敏度 |
| KWS keywords_threshold | 0.1 | 阈值 |

### 12.4 运行数据（50,970 行日志统计）

| 指标 | 数值 |
|---|---|
| 唤醒成功 | 299 |
| 本地 KWS 命中 | 116（你好小智 69 / 爱丽丝 47） |
| 待命省费跳过 | 1892 |
| 噪声过滤总计 | 637 |
| ├─ 过零率 | 451（70.8%） |
| ├─ 语音过短 | 163（25.6%） |
| ├─ 断续 | 21（3.3%） |
| └─ 幻觉短语 | 2（0.3%） |
| 打断（barge-in） | 352（RMS 均值 0.080） |
| 环境音过滤 | 74 |
| 状态 awakened | 1302 |
| 状态 speaking | 996 |
| 状态 thinking | 719 |
| 状态 idle | 244 |

### 12.5 desk-ai 的模块清单（可直接参考）

| 文件 | 大小 | 作用 |
|---|---|---|
| `voiceController.ts` | 84 KB | 语音状态机（核心） |
| `conversation.ts` | 31 KB | 对话编排 |
| `llm.ts` | 12 KB | LLM 调用 |
| `tools.ts` | 12.9 KB | function calling |
| `memory.ts` | 14.5 KB | 记忆（非向量方案） |
| `persona.ts` | 5.2 KB | 人设/时间上下文 |
| `tts.ts` | 10.9 KB | 合成封装 |
| `asr.ts` | 3.8 KB | MiniMax ASR |
| `wav.ts` | 2 KB | WAV 封装 |
| `musicIntent.ts` | 14.2 KB | 音乐指令（纯函数规则） |
| `kws.rs` | 26 KB | KWS |
| `asr.rs` | 29 KB | 流式 ASR |
| `cloud_asr.rs` | 10.9 KB | 阿里云 WebSocket |
| `qwen_tts.rs` | 34 KB | Qwen TTS |
| `wincred.rs` | 6.9 KB | 凭据存储 |

---

## 变更记录

| 日期 | 内容 |
|---|---|
| 2026-11-09 | 初稿。基于 dsh-dafeiyu 0.1.23 源码勘察 + 三轮需求讨论 |
| 2026-11-09 | v2：吸收 desk-ai 参考实现 + 50,970 行运行日志分析。KWS 风险消除，新增 16 条踩坑清单、实测参数表、运行数据。Phase 0 从 1-2 天降到半天 |

---

## 参考资料

- [dsh-dafeiyu 仓库](https://github.com/QCYTSN/dsh-dafeiyu)
- [dsh-persona-dafeiyu（人设插件）](https://github.com/huaizhuanghub/dsh-persona-dafeiyu)
- [sherpa-onnx 文档](https://k2-fsa.github.io/sherpa/onnx/)
- [阿里云 paraformer-realtime 文档](https://help.aliyun.com/zh/model-studio/)
- 本地参考：`D:\project\desk-ai`（Tauri 2 语音桌宠，已跑通）
