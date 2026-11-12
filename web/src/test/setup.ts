import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

import { clearToken } from '../auth/tokenStore'

/**
 * Node 25 自带一个未启用的全局 localStorage 存根（没有 getItem 等方法），
 * 在 jsdom 环境里会遮蔽真正的 Storage。用同语义的内存实现替换，保证测试
 * 验证的是令牌持久化行为，而不是 Node 的存根。
 */
class MemoryStorage implements Storage {
  private readonly items = new Map<string, string>()

  get length(): number {
    return this.items.size
  }

  clear(): void {
    this.items.clear()
  }

  getItem(key: string): string | null {
    return this.items.get(key) ?? null
  }

  key(index: number): string | null {
    return [...this.items.keys()][index] ?? null
  }

  removeItem(key: string): void {
    this.items.delete(key)
  }

  setItem(key: string, value: string): void {
    this.items.set(key, String(value))
  }
}

Object.defineProperty(globalThis, 'localStorage', {
  value: new MemoryStorage(),
  configurable: true,
  writable: true,
})

beforeEach(() => {
  clearToken()
})

afterEach(() => {
  cleanup()
  clearToken()
  vi.unstubAllGlobals()
})
