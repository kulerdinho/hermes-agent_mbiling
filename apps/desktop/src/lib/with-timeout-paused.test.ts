import { afterEach, describe, expect, it, vi } from 'vitest'

import { TimeoutError, withTimeoutPausedWhile } from './with-timeout'

afterEach(() => {
  vi.useRealTimers()
})

describe('withTimeoutPausedWhile', () => {
  it('times out like withTimeout when nobody is waiting on the user', async () => {
    vi.useFakeTimers()
    const result = withTimeoutPausedWhile(new Promise(() => {}), 10_000, 'boom', async () => false, 1_000)
    const assertion = expect(result).rejects.toBeInstanceOf(TimeoutError)
    await vi.advanceTimersByTimeAsync(10_000)
    await assertion
  })

  it('never times out while the user is being waited on (sign-in screen)', async () => {
    vi.useFakeTimers()
    let settled = false
    const result = withTimeoutPausedWhile(new Promise(() => {}), 10_000, 'boom', async () => true, 1_000)
    result.catch(() => undefined).finally(() => (settled = true))
    await vi.advanceTimersByTimeAsync(600_000)
    expect(settled).toBe(false)
  })

  it('gives the full budget again once the user is done', async () => {
    vi.useFakeTimers()
    let waiting = true
    let rejected = false
    const result = withTimeoutPausedWhile(new Promise(() => {}), 10_000, 'boom', async () => waiting, 1_000)
    result.catch(() => (rejected = true))
    await vi.advanceTimersByTimeAsync(300_000)
    waiting = false
    await vi.advanceTimersByTimeAsync(9_000)
    expect(rejected).toBe(false)
    await vi.advanceTimersByTimeAsync(2_000)
    expect(rejected).toBe(true)
  })

  it('resolves with the value as soon as it arrives', async () => {
    await expect(withTimeoutPausedWhile(Promise.resolve(42), 10_000, 'boom', async () => false)).resolves.toBe(42)
  })
})
