// A small append-only log for the native Helper lifecycle.
//
// Before this file existed a failed Helper start was only visible in the Host's
// console: timeouts, `exited before ready` and spawn errors disappeared when the
// Desktop app was started from its icon. Users therefore saw a missing pet with
// no explanation (issues #88 and #91). Keep this log dependency-free, bounded,
// and best-effort: writing diagnostics must never be able to break the plugin.
import { appendFileSync, mkdirSync, renameSync, rmSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'

const MAX_LOG_BYTES = 1024 * 1024

// `DSH_DAFEIYU_LOG_FILE` overrides the location; `off` disables file logging
// for CI or for users who do not want another file in their profile.
export function defaultHelperLogPath({
  platform = process.platform,
  env = process.env,
  home = homedir(),
} = {}) {
  const override = env.DSH_DAFEIYU_LOG_FILE
  if (override === 'off') return undefined
  if (override) return resolve(override)
  if (platform === 'win32') {
    const base = env.LOCALAPPDATA || join(home, 'AppData', 'Local')
    return join(base, 'dsh-dafeiyu', 'logs', 'helper.log')
  }
  if (platform === 'darwin') {
    return join(home, 'Library', 'Logs', 'dsh-dafeiyu', 'helper.log')
  }
  const base = env.XDG_STATE_HOME || join(home, '.local', 'state')
  return join(base, 'dsh-dafeiyu', 'helper.log')
}

function rotate(file) {
  try {
    if (statSync(file).size < MAX_LOG_BYTES) return
  } catch {
    // No log yet (or it is unreadable): the append below will either create it
    // or fail, and both outcomes are handled by the caller.
    return
  }
  const previous = `${file}.1`
  try {
    rmSync(previous, { force: true })
    renameSync(file, previous)
  } catch {
    // Rotation is an optimization; never fail the append because of it.
  }
}

/**
 * Create a best-effort file logger. Returns a no-op logger when no path is
 * configured or when the directory cannot be created.
 */
export function createHelperLog(file, {
  now = () => new Date(),
  append = appendFileSync,
  makeDirectory = mkdirSync,
} = {}) {
  if (!file) {
    return { path: undefined, write() {} }
  }
  let usable = true
  let prepared = false
  let writes = 0
  const prepare = () => {
    if (prepared || !usable) return usable
    prepared = true
    try {
      makeDirectory(dirname(file), { recursive: true })
      rotate(file)
      append(file, '')
    } catch {
      usable = false
    }
    return usable
  }
  return {
    path: file,
    write(level, message) {
      if (!prepare()) return
      writes += 1
      // A long-running session can outlive the single startup check; re-check
      // occasionally so a crash loop cannot grow the file without bound.
      if (writes % 256 === 0) rotate(file)
      const line = `${now().toISOString()} ${level} ${String(message).replaceAll(/\s*\r?\n\s*/g, ' ⏎ ')}\n`
      try {
        append(file, line)
      } catch {
        usable = false
      }
    },
  }
}
