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
  journal: 'C:/data/logs/mains.log',
  inscriptionAutomatique: false,
  inscriptionEnCours: false,
  navigateur: {
    actif: true,
    prefere: 'edge',
    executable: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    sitesInterdits: [{ motif: '^file:', libelle: '^file:' }]
  }
}

function installMains(status: MbilingMainsStatus) {
  const mains = {
    status: vi.fn().mockResolvedValue(status),
    setToken: vi.fn().mockResolvedValue({ ...status, jetonDefini: true, enMarche: true }),
    setActive: vi.fn().mockResolvedValue({ ...status, actif: false }),
    restart: vi.fn().mockResolvedValue(status),
    setBrowser: vi.fn().mockResolvedValue({ ...status, navigateur: { ...status.navigateur, prefere: 'chrome' } }),
    addBlockedSite: vi.fn().mockResolvedValue({
      ...status,
      navigateur: {
        ...status.navigateur,
        sitesInterdits: [...status.navigateur.sitesInterdits, { motif: 'banque', libelle: 'banque.fr' }]
      }
    }),
    removeBlockedSite: vi.fn().mockResolvedValue({ ...status, navigateur: { ...status.navigateur, sitesInterdits: [] } }),
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

  it('shows the automatic link instead of a token field once signed in to a server', async () => {
    installMains({ ...BASE, inscriptionAutomatique: true, jetonDefini: true })
    render(<MainsSettings />)

    expect(await screen.findByText('This PC is linked to your Mbiling account automatically.')).toBeTruthy()
    expect(screen.queryByPlaceholderText('Paste the device token')).toBeNull()

    fireEvent.click(screen.getByText('Enter a token manually'))
    expect(await screen.findByPlaceholderText('Paste the device token')).toBeTruthy()
  })

  it('surfaces the last problem reported by the Mains', async () => {
    installMains({ ...BASE, jetonDefini: true, raison: 'refusé par le Relais : jeton inconnu' })
    render(<MainsSettings />)

    expect(await screen.findByText('refusé par le Relais : jeton inconnu')).toBeTruthy()
  })

  it('shows the browser that will be used and lets the user prefer another one', async () => {
    const mains = installMains(BASE)
    render(<MainsSettings />)

    expect(await screen.findByText('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Google Chrome' }))
    await waitFor(() => expect(mains.setBrowser).toHaveBeenCalledWith({ prefere: 'chrome' }))
  })

  it('says so when neither Edge nor Chrome is installed', async () => {
    installMains({ ...BASE, navigateur: { ...BASE.navigateur, executable: null } })
    render(<MainsSettings />)

    expect(await screen.findByText('Neither Edge nor Chrome was found on this PC.')).toBeTruthy()
  })

  it('adds a blocked site from what the user types and removes one', async () => {
    const mains = installMains(BASE)
    render(<MainsSettings />)

    const field = (await screen.findByPlaceholderText('e.g. mybank.com')) as HTMLInputElement
    fireEvent.change(field, { target: { value: 'banque.fr' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(mains.addBlockedSite).toHaveBeenCalledWith('banque.fr'))
    expect(await screen.findByText('banque.fr')).toBeTruthy()
    expect(field.value).toBe('')

    fireEvent.click(screen.getByRole('button', { name: 'Remove ^file:' }))
    await waitFor(() => expect(mains.removeBlockedSite).toHaveBeenCalledWith('^file:'))
  })
})
