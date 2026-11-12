/**
 * Bearer 令牌存储：内存优先，localStorage 仅用于刷新后恢复。
 *
 * 令牌不是长期凭据；退出登录或遇到 401 时必须同时清掉两处，避免残留。
 */

export const TOKEN_STORAGE_KEY = 'bank_assistant_access_token'

let memoryToken: string | null = null
let storageLoaded = false

function readStorage(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY)
  } catch {
    // 隐私模式等场景 localStorage 不可用，仅保留内存令牌。
    return null
  }
}

export function getToken(): string | null {
  if (!storageLoaded) {
    memoryToken = readStorage()
    storageLoaded = true
  }
  return memoryToken
}

export function setToken(token: string): void {
  memoryToken = token
  storageLoaded = true
  try {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token)
  } catch {
    // 内存令牌仍然可用，UI 不因此中断。
  }
}

export function clearToken(): void {
  memoryToken = null
  storageLoaded = true
  try {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY)
  } catch {
    // 忽略不可用的 localStorage。
  }
}
