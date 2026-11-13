import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { MbilingMainsStatus } from '@/global'

import { MainsSettings } from './mains-settings'

const BASE: MbilingMainsStatus = {
  actif: true,
  jetonDefini: false,
  chiffrementDisponible: true,
  enMarche: false,
  connecte: false,
  appareil: null,
  raison: null,
  relais: 'wss://api.mbiling.test/mains',
  dossiers: ['C:/Users/me/Documents/Mbiling-Test'],
  bash: 'C:/Program Files/Mbiling/resources/git/bin/bash.exe',
  fichierPolitique: 'C:/data/mains-politique.json',
  fichierReglages: 'C:/data/mains.json',
  journal: 'C:/data/logs/mains.log'
}

function installMains(status: MbilingMainsStatus) {
  const mains = {
    status: vi.fn().mockResolvedValue(status),
    setToken: vi.fn().mockResolvedValue({ ...status, jetonDefini: true, enMarche: true }),
    setActive: vi.fn().mockResolvedValue({ ...status, actif: false }),
    restart: vi.fn().mockResolvedValue(status),
    open: vi.fn().mockResolvedValue(null)
  }
  Object.defineProperty(window, 'mbilingDesktop', { configurable: true, value: { mains } })

  return mains
}

afterEach(() => {
  cleanup()
  Reflect.deleteProperty(window, 'mbilingDesktop')
})

describe('MainsSettings', () => {
  it('shows the relay, the allowed folders and that a token is still needed', async () => {
    installMains(BASE)
    render(<MainsSettings />)

    expect(await screen.findByText('wss://api.mbiling.test/mains')).toBeTruthy()
    expect(screen.getByText('C:/Users/me/Documents/Mbiling-Test')).toBeTruthy()
    expect(screen.getByText('Waiting for a device token')).toBeTruthy()
    expect(screen.getByText('No token yet: paste the one you received.')).toBeTruthy()
  })

  it('saves the pasted token and clears the field', async () => {
    const mains = installMains(BASE)
    render(<MainsSettings />)

    const field = (await screen.findByPlaceholderText('Paste the device token')) as HTMLInputElement
    fireEvent.change(field, { target: { value: 'jeton-appareil' } })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => expect(mains.setToken).toHaveBeenCalledWith('jeton-appareil'))
    await waitFor(() => expect(field.value).toBe(''))
    expect(await screen.findByText('A token is saved, encrypted by Windows.')).toBeTruthy()
  })

  it('reports a connected PC', async () => {
    installMains({ ...BASE, jetonDefini: true, enMarche: true, connecte: true, appareil: 'pc-test' })
    render(<MainsSettings />)

    expect(await screen.findByText('Connected: the agent can use this PC')).toBeTruthy()
  })

  it('opens the local policy file', async () => {
    const mains = installMains(BASE)
    render(<MainsSettings />)

    fireEvent.click(await screen.findByText('Edit the local policy'))
    await waitFor(() => expect(mains.open).toHaveBeenCalledWith('politique'))
  })

  it('surfaces the last problem reported by the Mains', async () => {
    installMains({ ...BASE, jetonDefini: true, raison: 'refusé par le Relais : jeton inconnu' })
    render(<MainsSettings />)

    expect(await screen.findByText('refusé par le Relais : jeton inconnu')).toBeTruthy()
  })
})
