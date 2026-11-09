# dsh-dafeiyu-pro 🐋🎙️

**给「大肥鱼」桌宠加上语音能力** —— 基于 [QCYTSN/dsh-dafeiyu](https://github.com/QCYTSN/dsh-dafeiyu) 的二次开发。

> ⚠️ **这是一个 fork，不是原创项目。**
> 原项目 `dsh-dafeiyu` 由 [@QCYTSN](https://github.com/QCYTSN) 开发，MIT 协议。
> 本仓库在其基础上增加语音交互能力，遵循同样的 MIT 协议。

---

## 目录

- [这是什么](#这是什么)
- [与上游的关系](#与上游的关系)
- [当前进度](#当前进度)
- [规划中的功能](#规划中的功能)
- [技术选型](#技术选型)
- [开发环境](#开发环境)
- [许可与致谢](#许可与致谢)

---

## 这是什么

[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的桌面伴侣插件。

原版 `dsh-dafeiyu` 会显示 Agent 的实时状态（思考、改文件、等确认、出错），它有一个透明置顶的原生窗口，还能看余额。

**本项目要做的，是让它能听、能说：**

```
你叫「大肥鱼」
    ↓
它回「我在」
    ↓
你说话 → 流式识别 → 意图分流
    ↓
    ├─ 闲聊 → 直连 LLM → 1~2 秒回话
    ├─ 查数据 → 调天气/搜索 API
    └─ 长任务 → 派给 DSH agent，先垫一句「好的，我看看」
    ↓
流式合成，边收边播，可以中途打断
```

---

## 与上游的关系

### 来源

| 项目 | 地址 | 说明 |
|---|---|---|
| **上游** | [QCYTSN/dsh-dafeiyu](https://github.com/QCYTSN/dsh-dafeiyu) | 原作者，MIT |
| **本仓库** | [cooleye/dsh-dafeiyu-pro](https://github.com/cooleye/dsh-dafeiyu-pro) | 二次开发 |

### 我们改了什么

| 领域 | 上游 | 本项目 |
|---|---|---|
| 语音唤醒 | ❌ | 🔨 开发中 |
| 语音识别 | ❌ | 🔨 开发中 |
| 语音合成 | ❌ | 🔨 开发中 |
| 意图分流 | ❌ | 📋 规划中 |
| 长期记忆 | ❌ | 📋 规划中 |
| 状态显示 | ✅ 完整 | ✅ 保持不变 |
| 桌宠动画 | ✅ 完整 | ✅ 保持不变 |

### 我们没改什么

- **动画素材**：仍然来自上游，原始出处是 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)（MIT）
- **图标、光标、音效**：保持不变
- **状态机核心**：`companion-reducer.js` 的逻辑保持上游实现

---

## 当前进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| — | Fork 上游代码 | ✅ 完成 |
| Phase 0 | 唤醒词 PoC | 🔨 进行中 |
| Phase 1 | 音频栈 + 本地出声 | ⬜ 未开始 |
| Phase 2 | 云端流式 ASR | ⬜ 未开始 |
| Phase 3 | 意图分流 + 直连 LLM | ⬜ 未开始 |
| Phase 4 | 接入 DSH agent | ⬜ 未开始 |
| Phase 5 | 长期记忆 | ⬜ 未开始 |

详细设计见 [docs/VOICE-DESIGN.md](docs/VOICE-DESIGN.md)。

---

## 规划中的功能

### 语音唤醒（本地，零云端调用）

- 唤醒词可配置，默认「大肥鱼」
- 用 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 的 KWS 模型
- 待命态不烧钱：超出唤醒词长度窗口的音频直接丢弃

### 语音识别（云端流式）

- 阿里云 paraformer-realtime-v2
- 边说边出字，支持打断
- 多级降噪门控（能量迟滞 / 过零率 / 有声帧占比）

### 语音合成（云端流式）

- 阿里云 Qwen-TTS / MiniMax
- 边收边播，支持中途打断
- 云端失败时回落到本地 TTS

### 意图分流

三条路，各走各的：

| 路径 | 触发 | 响应 | 走的通道 |
|---|---|---|---|
| 闲聊 | 「你好呀」「今天怎么样」 | 1~2s | 直连 LLM |
| 查数据 | 「今天天气」「有什么新闻」 | 1~2s | LLM + 天气/搜索 API |
| 长任务 | 「帮我写个文档」 | 先垫话，后异步 | DSH agent |

### 长期记忆

- 事实库（关键词 × 类别 × 重要度 × 新鲜度打分）
- 每条事实带用户原话作为依据，防模型凭空记忆
- 闲聊记忆和任务记忆分开存

---

## 技术选型

| 层面 | 选型 | 理由 |
|---|---|---|
| 桌面壳 | PySide6 (Qt6) | 沿用上游 |
| 插件层 | Cordis (DSH) | 沿用上游 |
| 唤醒词 | sherpa-onnx KWS | 本地、低功耗、零成本 |
| 语音识别 | 阿里云 paraformer-realtime-v2 | 流式效果好，实测中文 CER 2.1% |
| 语音合成 | 阿里云 Qwen-TTS | 支持情感语气，首包几百毫秒 |
| 音频采集 | sounddevice (PortAudio) | Qt 打包里没有 QtMultimedia |
| 意图分类 | 规则 + 小模型兜底 | 快、可解释 |

### 参考实现

语音链路参考了 [desk-ai](https://github.com/) 的实测参数（私有项目），包括：

- VAD 参数（起说话 0.03 / 收尾 0.012 / 对话态静音切段 1050ms）
- 打断阈值与 1 秒重喂机制
- ASR 幻觉短语过滤词表
- 阿里云 WebSocket 的 `"streaming": "duplex"` 坑

---

## 开发环境

### 前置

- Node.js ≥ 22.19
- Python ≥ 3.10（用于 helper）
- pnpm

### 本地开发

```bash
# 克隆
git clone https://github.com/cooleye/dsh-dafeiyu-pro.git
cd dsh-dafeiyu-pro

# 安装依赖
pnpm install

# 跑测试
npm test
npm run test:python

# 直接跑 helper（开发期推荐，不用每次打包）
npm run helper:headless     # 验证协议，不开窗口
npm run helper:visual       # 开窗口
```

### 装到 DSH 里

```bash
# 用 link 模式，改代码实时生效
dsh plugin --profile desktop add link:<本仓库绝对路径>
```

### 打包

```bash
npm run build:helper:windows   # Windows exe
npm run build:helper:linux     # Linux
npm run build:helper:darwin    # macOS
```

---

## 目录结构

```
dsh-dafeiyu-pro/
├── src/                    Node 侧（插件逻辑）
│   ├── plugin.js           配置 Schema、事件订阅
│   ├── companion-reducer.js 状态机
│   ├── helper-process.js   子进程管理
│   ├── protocol.js         消息协议
│   └── balance.js          余额查询
├── runtime/                Python 侧（Qt helper）
│   ├── helper.py           窗口、动画、交互
│   ├── animation_model.py
│   └── bin/                各平台预编译 helper
├── assets/pet/             动画帧（WebP，来自 dsh-pet）
├── lib/client.js           浏览器侧
├── docs/                   文档
└── test/                   测试
```

### 与上游的目录差异

```
- legacy/       已移除（旧版素材，与开发无关，且不在 MIT 范围内）
- .github/      已重置（上游 CI 不适用）
```

---

## 许可与致谢

### 代码许可

本项目代码遵循 **MIT License**，与原项目一致。见 [LICENSE](LICENSE)。

### 素材许可

⚠️ **注意区分**：

| 资产 | 许可 | 说明 |
|---|---|---|
| 源代码 | MIT | 自由使用、修改、分发 |
| `assets/pet/` 动画帧 | MIT | 来自 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet) |
| `legacy/dafeiyu/` | ❌ 受限 | **本项目已移除该目录** |

详见 [ASSET_LICENSE.md](ASSET_LICENSE.md)。

### 致谢

**特别感谢原项目作者 [@QCYTSN](https://github.com/QCYTSN)** ——
`dsh-dafeiyu` 的架构设计（原生窗口 + JSONL 协议 + 事件驱动状态机）非常好用，
让二次开发变得很轻松。

同时感谢：

- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) —— 插件系统
- [Cordis](https://github.com/cordiverse/cordis) —— 插件框架
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) —— 本地语音引擎
- [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) —— 动画素材
- 大肥鱼人设的社区创作者们

---

## 声明

- 本项目是**独立维护的社区分支**，不代表原作者的立场
- 遇到问题请先看 [上游 issue](https://github.com/QCYTSN/dsh-dafeiyu/issues)，可能是因为上游已经修复了
- 如果上游合并了我们的改动，本项目会尽量跟随

---

**上游**：[QCYTSN/dsh-dafeiyu](https://github.com/QCYTSN/dsh-dafeiyu) ·
**本文档最后更新**：2026-11-10
