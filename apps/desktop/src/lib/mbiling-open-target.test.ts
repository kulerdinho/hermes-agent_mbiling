import { describe, expect, it } from 'vitest'

import {
  normalizeMbilingOpenString,
  pathFromMbilingDeepLink,
  pathFromOpenDeepLink,
  resolveMbilingOpenPath
} from './mbiling-open-target'

describe('normalizeMbilingOpenString', () => {
  it('accepts hash-router paths and strips a leading hash', () => {
    expect(normalizeMbilingOpenString('/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeMbilingOpenString('#/index-network/intent/1')).toBe('/index-network/intent/1')
  })

  it('maps plugin-scoped mbiling:// deep links to the same path', () => {
    expect(normalizeMbilingOpenString('mbiling://index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeMbilingOpenString('mbiling://index-network/intent/1?focus=true')).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('maps mbiling://open/… deep links by stripping the open host', () => {
    expect(normalizeMbilingOpenString('mbiling://open/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeMbilingOpenString('mbiling://open/settings/plugins')).toBe('/settings/plugins')
  })

  it('rejects reserved mbiling kinds and unsafe paths', () => {
    expect(normalizeMbilingOpenString('mbiling://blueprint/morning-brief')).toBeNull()
    expect(normalizeMbilingOpenString('mbiling://plugin/install')).toBeNull()
    expect(normalizeMbilingOpenString('https://example.com/x')).toBeNull()
    expect(normalizeMbilingOpenString('/../etc/passwd')).toBeNull()
    expect(normalizeMbilingOpenString('index-network')).toBeNull()
  })
})

describe('resolveMbilingOpenPath', () => {
  it('merges structured path + params', () => {
    expect(resolveMbilingOpenPath({ path: '/index-network/intent/1', params: { focus: 'true' } })).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('resolves href the same as a bare string', () => {
    expect(resolveMbilingOpenPath({ href: 'mbiling://index-network/intent/1' })).toBe('/index-network/intent/1')
  })
})

describe('pathFromMbilingDeepLink', () => {
  it('builds the navigate path from a plugin-scoped deep-link payload', () => {
    expect(pathFromMbilingDeepLink('index-network', 'intent/1')).toBe('/index-network/intent/1')
  })

  it('builds the navigate path from mbiling://open/… payloads', () => {
    expect(pathFromOpenDeepLink('index-network/intent/1')).toBe('/index-network/intent/1')
    expect(pathFromMbilingDeepLink('open', 'agent/42')).toBe('/agent/42')
  })

  it('ignores reserved kinds', () => {
    expect(pathFromMbilingDeepLink('blueprint', 'morning-brief')).toBeNull()
    expect(pathFromMbilingDeepLink('plugin', 'install')).toBeNull()
  })
})
