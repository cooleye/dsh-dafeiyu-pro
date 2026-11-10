/**
 * 语音服务桥：把 voice_service.py 拉起来，并在它和桌宠之间转发。
 *
 * 为什么单独一个进程，不塞进 helper.py：
 *   1. helper 是 PyInstaller 打包的 Qt exe，加音频/网络依赖要重打 45MB
 *   2. 语音挂了不能影响桌宠动画，桌宠挂了也不能影响语音
 *   3. 沿用「JSONL over stdio」的统一模式，调试简单
 *
 *     DSH 插件
 *        ├── helper-process.js → helper.py（Qt 窗口）
 *        └── voice-bridge.js   → voice_service.py（语音）
 *
 * 协议见 voice_service.py 头部注释。
 */

import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const defaultScript = resolve(here, '..', 'voice_service.py')

/** 语音服务崩溃后重启的退避上限 */
const MAX_RESTART_DELAY_MS = 30000

export const VoiceMessageKind = Object.freeze({
  READY: 'ready',
  STATE: 'state',
  USER_TEXT: 'user_text',
  REPLY_START: 'reply_start',
  DELTA: 'delta',
  REPLY_END: 'reply_end',
  TOOL: 'tool',
  TASK_START: 'task_start',
  TASK_DONE: 'task_done',
  PERMISSION_ASK: 'permission_ask',
  ERROR: 'error',
})

/**
 * 语音桥。
 *
 * 用法：
 *   const bridge = createVoiceBridge({ pythonPath, onEvent, logger })
 *   bridge.start()
 *   bridge.send({ kind: 'config', config: {...} })
 *   bridge.dispose()
 */
export function createVoiceBridge({
  pythonPath = 'python',
  scriptPath = defaultScript,
  config = {},
  env = {},
  onEvent = () => {},
  logger = console,
  autoStart = true,
} = {}) {
  let child = null
  let rl = null
  let disposed = false
  let restartTimer = null
  let restartDelay = 1000
  let ready = false
  /** 收到的最后一条 ready 里的信息（版本等） */
  let info = null

  function log(message) {
    logger.info?.(`[voice] ${message}`)
  }

  function fail(message) {
    logger.warn?.(`[voice] ${message}`)
  }

  // ---------- 生命周期 ----------

  function start() {
    if (disposed || child) return
    if (!existsSync(scriptPath)) {
      fail(`找不到语音脚本：${scriptPath}`)
      return
    }

    const args = [scriptPath, '--stdio']
    if (Object.keys(config).length > 0) {
      args.push('--config', JSON.stringify(config))
    }

    try {
      child = spawn(pythonPath, args, {
        stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...process.env, ...env, PYTHONIOENCODING: 'utf-8' },
        windowsHide: true,
      })
    } catch (error) {
      fail(`启动语音服务失败：${error?.message ?? error}`)
      scheduleRestart()
      return
    }

    child.on('error', (error) => {
      fail(`语音服务错误：${error?.message ?? error}`)
    })

    child.on('exit', (code, signal) => {
      const wasReady = ready
      ready = false
      child = null
      if (rl) {
        rl.close()
        rl = null
      }
      if (disposed) return
      fail(`语音服务退出（code=${code}, signal=${signal}）`)
      onEvent({ kind: 'error', message: '语音服务已退出' })
      if (wasReady || code !== 0) scheduleRestart()
    })

    // stdout 是协议流：一行一条 JSON
    rl = createInterface({ input: child.stdout })
    rl.on('line', (line) => handleLine(line))

    // stderr 是日志，转给宿主日志
    createInterface({ input: child.stderr }).on('line', (line) => {
      const text = String(line).trim()
      if (text) logger.info?.(text)
    })

    child.stdin.on('error', () => {})
    child.stdout.on('error', () => {})

    if (autoStart) send({ kind: 'start' })
  }

  function scheduleRestart() {
    if (disposed || restartTimer) return
    const delay = restartDelay
    restartDelay = Math.min(restartDelay * 2, MAX_RESTART_DELAY_MS)
    log(`语音服务 ${delay}ms 后重启`)
    restartTimer = setTimeout(() => {
      restartTimer = null
      start()
    }, delay)
  }

  function stop() {
    if (!child) return
    try {
      send({ kind: 'stop' })
    } catch {
      /* 进程可能已经没了 */
    }
  }

  function dispose() {
    disposed = true
    if (restartTimer) {
      clearTimeout(restartTimer)
      restartTimer = null
    }
    try {
      send({ kind: 'shutdown' })
    } catch {
      /* ignore */
    }
    if (child) {
      const c = child
      child = null
      try {
        c.stdin.end()
        setTimeout(() => {
          try {
            if (c.exitCode === null) c.kill()
          } catch {
            /* ignore */
          }
        }, 500)
      } catch {
        /* ignore */
      }
    }
    if (rl) {
      rl.close()
      rl = null
    }
  }

  // ---------- 协议 ----------

  function send(message) {
    if (!child?.stdin?.writable || child.stdin.destroyed) return false
    try {
      child.stdin.write(`${JSON.stringify(message)}\n`)
      return true
    } catch {
      return false
    }
  }

  function handleLine(line) {
    const text = String(line).trim()
    if (!text) return
    let msg
    try {
      msg = JSON.parse(text)
    } catch {
      fail(`语音服务输出不是 JSON：${text.slice(0, 120)}`)
      return
    }
    if (msg?.kind === 'ready') {
      ready = true
      restartDelay = 1000
      info = msg
      log(`就绪（版本 ${msg.version ?? '?'}）`)
    }
    try {
      onEvent(msg)
    } catch (error) {
      fail(`事件处理失败：${error?.message ?? error}`)
    }
  }

  // ---------- 对外 ----------

  return {
    start,
    stop,
    dispose,
    send,
    get ready() {
      return ready
    },
    get info() {
      return info
    },
    get alive() {
      return Boolean(child)
    },
    /** 把配置热推给语音服务（目前重启生效，见 voice_service.py） */
    updateConfig(patch) {
      return send({ kind: 'config', config: patch })
    },
    /** 告诉语音服务 DSH 正在干活，别抢着说话 */
    setDsBusy(busy) {
      return send({ kind: 'ds_busy', busy: Boolean(busy) })
    },
  }
}
