import { vi } from 'vitest'

import type { MbilingApiRequest } from '@/global'

/** Install a fake `window.mbilingDesktop.api`, the preload bridge every REST helper sends through. */
export function installRestBridge(answer: (request: MbilingApiRequest) => object) {
  const api = vi.fn(async (request: MbilingApiRequest) => answer(request))

  Object.defineProperty(window, 'mbilingDesktop', { configurable: true, value: { api } })

  return api
}
