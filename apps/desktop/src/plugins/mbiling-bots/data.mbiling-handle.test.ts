/**
 * @mbiling belongs to the default bot only while its source has no real
 * (pre-reservation) `mbiling` profile. With one, the roster shows exactly one
 * @mbiling — the real profile — and the default answers to @default, so a
 * typed @mbiling reaches the same profile the backend's message_agent does.
 */

import { describe, expect, it, vi } from 'vitest'

import { botHandle, botMentionTag, claimDefaultHandles, mergeMultiSourceRoster, resolveRosterMentions } from './data'
import { previewKind } from './row-helpers'
import type { RosterRow } from './types'

vi.mock('@mbiling/plugin-sdk', async () => {
  const { atom } = await import('nanostores')

  return { atom, host: {}, queryClient: undefined, useQuery: vi.fn(), useValue: vi.fn() }
})

vi.mock('./shared', () => ({ getPluginCtx: () => null, ID: 'mbiling-bots' }))

function roster(names: string[]): RosterRow[] {
  const local = { profiles: names.map(name => ({ name })) }

  // What Electron's buildAgentRoster reports for a single local source: bare-name handles.
  const union = {
    agents: names.map(profile => ({
      connectionId: 'local',
      connectionKind: 'local' as const,
      connectionLabel: 'This device',
      handle: profile,
      profile,
      targetProfile: profile
    })),
    primaryConnectionId: 'local'
  }

  return mergeMultiSourceRoster(local, union, 'local').profiles as RosterRow[]
}

describe('the @mbiling handle', () => {
  it.each([
    [['default', 'researcher'], 'default'],
    [['default', 'mbiling', 'researcher'], 'mbiling']
  ])('roster %j shows one @mbiling and a typed @mbiling reaches %s', (names, owner) => {
    const rows = roster(names)
    const handles = rows.map(row => botHandle(row.name, row))

    expect(handles.filter(handle => handle === 'mbiling')).toHaveLength(1)
    expect(rows.map(row => botMentionTag(row)).filter(tag => tag === 'mbiling')).toHaveLength(1)
    expect(resolveRosterMentions('@mbiling ping', rows, { name: 'researcher' }).map(row => row.name)).toEqual([owner])
  })

  it('names the default @default once a real mbiling profile holds the handle', () => {
    const rows = roster(['default', 'mbiling', 'researcher'])
    const byName = Object.fromEntries(rows.map(row => [row.name, botHandle(row.name, row)]))

    expect(byName).toEqual({ default: 'default', mbiling: 'mbiling', researcher: 'researcher' })
    expect(resolveRosterMentions('@default ping', rows, { name: 'researcher' }).map(row => row.name)).toEqual(['default'])
  })

  it('keeps each source separate: a remote mbiling profile does not rename the local default', () => {
    const rows = claimDefaultHandles<Partial<RosterRow>>([
      { connectionId: 'local', name: 'default' },
      { connectionId: 'mini', name: 'default' },
      { connectionId: 'mini', name: 'mbiling' }
    ])

    expect(rows.map(row => botHandle(String(row.name), row))).toEqual(['mbiling', 'default', 'mbiling'])
  })

  it("never lets a source's stale 'mbiling' handle on its default answer @mbiling", () => {
    // An older backend labels its default @mbiling even beside a real mbiling profile.
    const rows = claimDefaultHandles<RosterRow>([
      { connectionId: 'mini', handle: 'mbiling', name: 'default', remoteSource: true },
      { connectionId: 'mini', handle: 'mbiling', name: 'mbiling', remoteSource: true },
      { connectionId: 'local', name: 'researcher' }
    ])

    expect(resolveRosterMentions('@mbiling ping', rows, { name: 'researcher' }).map(row => row.name)).toEqual(['mbiling'])
  })

  it('names a DM sender in a row preview by its roster handle', () => {
    const rows = claimDefaultHandles<RosterRow>(
      ['default', 'mbiling', 'researcher'].map(name => ({ connectionId: 'local', name }))
    )

    const sender = (name: string, list: RosterRow[]) =>
      previewKind(`Message from 🤖 ${name} (@x): hi`, list, 'local').fromBot

    expect([sender('default', rows), sender('mbiling', rows)]).toEqual(['default', 'mbiling'])
    // No real mbiling profile: the default is still @mbiling (#89484).
    expect(sender('default', claimDefaultHandles<RosterRow>([{ connectionId: 'local', name: 'default' }]))).toBe(
      'mbiling'
    )
  })
})
