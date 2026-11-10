/**
 * voice-bridge 的测试。
 *
 * 用 Node 自己当「假的语音服务」——正好验证 JSONL 协议，
 * 不依赖 Python、不联网、不开麦。
 */

import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import process from 'node:process'
import { createVoiceBridge } from '../src/voice-bridge.js'

/** 写一个假的语音服务脚本，行为由行为码决定。 */
function makeMock(behaviour = 'normal') {
  const dir = mkdtempSync(join(tmpdir(), 'voice-mock-'))
  const path = join(dir, 'mock.mjs')
  const body = `
const behaviour = ${JSON.stringify(behaviour)}
const out = (obj) => process.stdout.write(JSON.stringify(obj) + '\\n')
const readline = await import('node:readline')
out({ kind: 'ready', version: '9.9.9' })
if (behaviour === 'crash') {
  setTimeout(() => process.exit(3), 50)
} else {
  const rl = readline.createInterface({ input: process.stdin })
  rl.on('line', (line) => {
    const msg = JSON.parse(line)
    out({ kind: 'log', echo: msg.kind })
    if (msg.kind === 'start') {
      out({ kind: 'state', state: 'listening' })
      out({ kind: 'user_text', text: '你好' })
      out({ kind: 'reply_start' })
      out({ kind: 'delta', text: '哼' })
      out({ kind: 'reply_end', text: '哼' })
    }
    if (msg.kind === 'config') out({ kind: 'log', got: msg.config })
    if (msg.kind === 'shutdown') process.exit(0)
  })
}
`
  writeFileSync(path, body, 'utf8')
  return path
}

function waitFor(predicate, timeoutMs = 5000) {
  const start = Date.now()
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (predicate()) return resolve()
      if (Date.now() - start > timeoutMs) return reject(new Error('等待超时'))
      setTimeout(tick, 20)
    }
    tick()
  })
}

const silentLogger = { info() {}, warn() {} }

test('voice bridge 能拉起服务并收到 ready', async () => {
  const events = []
  const bridge = createVoiceBridge({
    pythonPath: process.execPath,
    scriptPath: makeMock(),
    onEvent: (m) => events.push(m),
    logger: silentLogger,
    autoStart: false,
  })

  try {
    bridge.start()
    await waitFor(() => bridge.ready)
    assert.equal(bridge.ready, true)
    assert.equal(bridge.info?.version, '9.9.9')
    assert.ok(events.some((e) => e.kind === 'ready'))
  } finally {
    bridge.dispose()
  }
})

test('voice bridge 能把 start 送进去并转发事件', async () => {
  const events = []
  const bridge = createVoiceBridge({
    pythonPath: process.execPath,
    scriptPath: makeMock(),
    onEvent: (m) => events.push(m),
    logger: silentLogger,
    autoStart: false,
  })

  try {
    bridge.start()
    await waitFor(() => bridge.ready)
    bridge.send({ kind: 'start' })

    await waitFor(() => events.some((e) => e.kind === 'reply_end'))

    const kinds = events.map((e) => e.kind)
    assert.ok(kinds.includes('state'), '应收到 state')
    assert.ok(kinds.includes('user_text'), '应收到 user_text')
    assert.ok(kinds.includes('delta'), '应收到 delta')
    const state = events.find((e) => e.kind === 'state')
    assert.equal(state.state, 'listening')
  } finally {
    bridge.dispose()
  }
})

test('voice bridge 转发配置更新', async () => {
  const events = []
  const bridge = createVoiceBridge({
    pythonPath: process.execPath,
    scriptPath: makeMock(),
    onEvent: (m) => events.push(m),
    logger: silentLogger,
    autoStart: false,
  })

  try {
    bridge.start()
    await waitFor(() => bridge.ready)
    bridge.updateConfig({ voice: 'Serena' })
    await waitFor(() => events.some((e) => e.kind === 'log' && e.got))
    const hit = events.find((e) => e.kind === 'log' && e.got)
    assert.deepEqual(hit.got, { voice: 'Serena' })
  } finally {
    bridge.dispose()
  }
})

test('voice bridge 忽略非 JSON 输出而不崩', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'voice-mock-'))
  const path = join(dir, 'noisy.mjs')
  writeFileSync(
    path,
    `
process.stdout.write('这不是 JSON\\n')
process.stdout.write(JSON.stringify({ kind: 'ready', version: '1.0.0' }) + '\\n')
setInterval(() => {}, 1000)
`,
    'utf8',
  )

  const events = []
  const bridge = createVoiceBridge({
    pythonPath: process.execPath,
    scriptPath: path,
    onEvent: (m) => events.push(m),
    logger: silentLogger,
    autoStart: false,
  })

  try {
    bridge.start()
    await waitFor(() => bridge.ready)
    assert.equal(bridge.ready, true, '脏行不该影响后续 ready')
  } finally {
    bridge.dispose()
  }
})

test('voice bridge 在服务崩溃后自动重启', async () => {
  const bridge = createVoiceBridge({
    pythonPath: process.execPath,
    scriptPath: makeMock('crash'),
    onEvent: () => {},
    logger: silentLogger,
    autoStart: false,
  })

  try {
    bridge.start()
    await waitFor(() => bridge.ready)
    // 进程 50ms 后退出 → 桥应该安排重启
    await waitFor(() => !bridge.alive, 3000)
    // 重启延迟 1s，给它时间
    await waitFor(() => bridge.alive, 5000)
    assert.equal(bridge.alive, true, '应该已经重启')
  } finally {
    bridge.dispose()
  }
})
