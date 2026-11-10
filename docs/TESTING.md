# 测试指南

这份文档告诉你怎么运行本项目目前的语音功能。

---

## 前置：打开终端

按 `Win + X`，选 **「终端」** 或 **「Windows PowerShell」**。

> ⚠️ 不要用系统自带的「命令提示符(cmd)」，语法不一样。

---

## 每次测试前的准备（复制粘贴即可）

```powershell
cd D:\project\dsh-dafeiyu-pro
$env:PYTHONIOENCODING='utf-8'
```

> 第一行：进入项目目录
> 第二行：让 Python 能正确输出中文（不然会乱码）

**建议**：每次打开新终端都要跑这两行。

---

## 测试一：听它说一句话（最简单）

```powershell
python audio_player.py --say "你好，我是大肥鱼"
```

**你应该听到**：电脑喇叭念出「你好，我是大肥鱼」

**如果没声音**，检查：
- 系统音量是不是静音了
- 输出设备选对了吗（右下角喇叭图标）

---

## 测试二：唤醒词（只能听，不能对话）

```powershell
python phase0_kws_poc.py
```

**你应该看到**：
```
=== 实时唤醒词监听 ===
✓ KWS 就绪（score=3.0 threshold=0.1）
  输入设备：Windows WDM-KS / 麦克风 (Speakerphone)
  采样率：48000 Hz → 重采样到 16000

说「大肥鱼」试试。Ctrl+C 退出。

  音量 0.008 ██
```

**操作**：
1. 对着麦克风清楚地说 **「大肥鱼」**
2. 看到 `🎯 命中：大肥鱼` 就成功了
3. 按 `Ctrl + C` 退出

**音量条**：
- 不说话时：`0.001 ~ 0.010`（很短的条）
- 说话时：`0.05 ~ 0.3`（明显变长）
- 如果说话时条不动 → 麦克风没工作，见下面「排错」

---

## 测试三：唤醒 + 它回应（推荐！）

```powershell
python phase1_wake_reply.py
```

**这是最有意思的一个**——它真的会理你了。

**你应该看到**：
```
=== 唤醒 → 回应 Demo ===

  设备：Windows WDM-KS / 麦克风 (Speakerphone)
  采样率：48000 Hz → 重采样 16000
  回应语：「我在」
  冷却：2.0 秒（防止连续触发）

说「大肥鱼」，它会回你。Ctrl+C 退出。

  音量 0.005
```

**操作**：
1. 说 **「大肥鱼」**
2. 等 1-2 秒，它会说 **「我在」**
3. 再叫一次，它会再回一次
4. 按 `Ctrl + C` 退出

**换别的回应语**：
```powershell
python phase1_wake_reply.py --reply "在呢，有什么吩咐"
python phase1_wake_reply.py --reply "叫我干嘛"
```

---

## 测试四：让它听懂你说什么（Phase 2，需要 API Key）

```powershell
python phase2_listen.py
```

**这个才是真·对话的第一步**——它能把你说的话转成文字。

**你应该看到**：
```
=== 唤醒 → 听写 Demo ===

  设备：Windows WDM-KS / 麦克风 (Speakerphone)
  采样率：48000 Hz → 重采样 16000
  唤醒后 20 秒内不说话会退回待命

说「大肥鱼」叫醒它，然后直接说话。Ctrl+C 退出。

 💤 0.008 ██
```

**操作**：
1. 说 **「大肥鱼」** → 它回「我在」，图标变成 👂
2. **直接说一句话**，比如「今天天气怎么样」
3. 说完停顿 1 秒 → 自动断句
4. 看到 `💬 你说：今天天气怎么样？`
5. 继续说话它会继续识别
6. 20 秒不说话，自动退回待命（💤）
7. `Ctrl + C` 退出

**效果参考**：
```
  🎯 唤醒：大肥鱼
  🔊 「我在」
  👂 在听...

 ⏳ 今天天气怎么样
  📤 断句（2100ms），等识别结果 ...
  💬 你说：今天天气怎么样？给我讲个故事呗。
```

**前提**：需要 `apikey.txt` 里有 `qwen:sk-xxx`（阿里云 DashScope）。

**注意**：这个要联网，而且**按调用计费**。

---

## 测试五：分级路由助手（Phase 4，最强！）

```powershell
python phase4_assistant.py
```

**这个会自己判断该聊还是该干活。**

| 你说 | 它会 |
|---|---|
| 「你好呀」 | 直接聊（0.5~1 秒） |
| 「今天北京天气怎么样」 | 自己搜网，用真实数据答（1~2 秒） |
| 「帮我写一个 Python 脚本」 | 先应一声「好的，我看看」，再交给 DSH agent |

**操作**：
1. 说 **「大肥鱼」**
2. 试这几句，观察图标变化：
   - `👂` 听 → `🤔` 想 → `🐟` 出字 → `🗣` 念
   - 派活时变 `⚙️`，会先听到「好的，我看看」
3. 任务超过 8 秒会再补一句「这个要花点时间，你先忙别的」

**关掉 agent**（只聊天 + 搜索）：
```powershell
python phase4_assistant.py --no-agent
```

**调垫场延迟**：
```powershell
python phase4_assistant.py --stall 5
```

**效果参考**：
```
 👂 0.005
  🎯 唤醒：大肥鱼
  🔊 「我在」
  👂 在听...

 🔴 0.180
  📤 断句（2000ms），等识别结果 ...
  💬 你说：今天北京天气怎么样
  🧭 分流：data（命中数据模式，置信度 80%）

  🐟
  🔧 web_search {"query": "北京 天气 2026年11月10日"}
哼，本鱼查过了，今天北京天气是晴天，气温14到-1度……
  [0.35s 首字 / 1.34s 生成 / 2 句 / 工具 ['web_search']]
```

**派活的效果**：
```
  💬 你说：帮我写一个说明文档
  🧭 分流：task（命中任务模式，置信度 85%）
  🔊 垫场1：「好的，我看看」
  🚀 派给 DSH agent：帮我写一个说明文档
  🔊 垫场2：「这个要花点时间，你先忙别的」

  ✅ 任务完成，23.4 秒

  📄 要念的内容：文档已经写好了，放在……
```

> ⚠️ DSH agent 需要 `DEEPSEEK_API_KEY`，本项目会自动从 `apikey.txt` 的
> `deepseek:` 那行注入。

---

## 测试六：完整语音对话（Phase 3）

```powershell
python phase3_chat.py
```

不带路由的纯聊天版。想简单点聊就用这个。

---

## 测试七：不用麦克风，验证对话管线

```powershell
python test_phase3.py --say "你是谁"
python test_phase3.py --say "讲个笑话" --voice Serena
python test_phase3.py --no-audio          # 只测 LLM，不合成不播放
```

---

## 测试八：跑自动化测试

不需要麦克风、不联网（除了最后一个加 `--live`）：

```powershell
python test_phase2.py        # ASR 会话状态机
python test_chatloop.py      # 分句逻辑
python test_phase4.py        # 分流 + 垫场 + 结果清洗
python test_phase4.py --live # 额外跑一次真实 DSH agent
```

应该都是 **全绿**。改完代码跑一下，防止回归。

---

## 测试九：单独测各组件

```powershell
python asr_aliyun.py --mic                    # 只测 ASR
python asr_aliyun.py --wav test.wav           # 用文件测 ASR
python tts_qwen.py --say "你好"                # 只测 TTS
python llm_qwen.py --ask "你好"                # 只测 LLM
python tools.py --search "今天天气"            # 只测搜索
python tools.py --time                         # 只测时间
python router.py                               # 看分流规则效果
python dsh_agent.py "任务描述"                  # 只测 DSH agent
```

---

## 测试四：录音看看效果

```powershell
python record_wav.py test.wav -d 3
```

倒计时 3-2-1 后开始说，录 3 秒。

录完会显示：
```
录音完成： 峰值 0.234 / RMS 0.056
已保存：test.wav (93.8 KB)
```

**峰值 < 0.01** 说明没录到声音。

---

## 测试五：采集样本，测准确率

这是评估「认不认得准」的：

```powershell
# 1) 录 10 条「大肥鱼」（说清楚一点）
python collect_samples.py pos --count 10

# 2) 录 10 条日常说话（不要说唤醒词）
python collect_samples.py neg --count 10 --script "今天天气不错,我在写代码,这个功能挺难的,帮我看看这个bug"

# 3) 看结果
python phase0_kws_poc.py --eval ./samples/pos --neg ./samples/neg
```

**理想结果**：
- 真阳性率 ≥ 80%（叫 10 次至少中 8 次）
- 假阳性率 ≤ 10%（日常说话 10 次最多误触发 1 次）

---

## 排错

### 麦克风没反应（音量条不动）

先看有哪些设备：
```powershell
python phase0_kws_poc.py --list-devices
```

找到你的麦克风编号，然后指定：
```powershell
python phase1_wake_reply.py --device 59
```

常见设备编号：
- `59` = 麦克风 (Speakerphone)
- `40` / `41` = 麦克风阵列
- `34` = 网易虚拟麦克风阵列

### 报 `PortAudioError -9999`

说明麦克风被别的程序独占了。

**先关掉这些**：
- DSH 的语音输入（设置里关掉）
- 微信 / QQ 语音通话
- 其他录音软件
- 浏览器里正在录音的标签页

项目会自动尝试 WDM-KS → WASAPI → DirectSound → MME，但全被占就没办法。

### 唤醒不灵敏（叫了好几次才中）

**调高灵敏度**：
```powershell
python phase1_wake_reply.py --score 4.0
```

`score` 越高越容易命中，范围建议 2.0 ~ 5.0。

### 老是自己触发（没叫就响）

**调高阈值**（让它更难被触发）：
```powershell
python phase1_wake_reply.py --threshold 0.3
```

`threshold` 越高越难触发，范围建议 0.1 ~ 0.5。

### 中文乱码

在命令前加：
```powershell
$env:PYTHONIOENCODING='utf-8'
```

### 说 `python 不是命令`

试试 `py`：
```powershell
py phase1_wake_reply.py
```

---

## 快速参考

| 我想... | 命令 |
|---|---|
| 听它说话 | `python audio_player.py --say "你好"` |
| 测唤醒词 | `python phase0_kws_poc.py` |
| 唤醒+回应 | `python phase1_wake_reply.py` |
| 唤醒+听写 | `python phase2_listen.py` |
| 纯聊天 | `python phase3_chat.py` |
| **分级路由助手** | `python phase4_assistant.py` |
| 只测对话管线 | `python test_phase3.py --say "你是谁"` |
| 换音色 | `python phase3_chat.py --voice Serena` |
| 跑全部测试 | `python test_phase2.py; python test_chatloop.py; python test_phase4.py` |
| 只测 ASR | `python asr_aliyun.py --mic` |
| 只测 TTS | `python tts_qwen.py --say "你好"` |
| 只测 LLM | `python llm_qwen.py --ask "你好"` |
| 只测搜索 | `python tools.py --search "今天天气"` |
| 看分流效果 | `python router.py` |
| 只测 agent | `python dsh_agent.py "任务"` |
| 查配置 | `python voice_config.py` |
| 录一段 | `python record_wav.py test.wav -d 3` |
| 采样本 | `python collect_samples.py pos --count 10` |
| 测唤醒准确率 | `python phase0_kws_poc.py --eval ./samples/pos --neg ./samples/neg` |
| 查设备 | `python phase0_kws_poc.py --list-devices` |
| 调灵敏 | `python phase1_wake_reply.py --score 4.0` |
| 调阈值 | `python phase1_wake_reply.py --threshold 0.3` |

---

所有命令按 `Ctrl + C` 退出。
