import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { afterEach, beforeEach, test, vi } from 'vitest'

// One temp tree per test: userData, the shipped defaults (app path /config) and Documents.
const dirs = vi.hoisted(() => ({ root: '' }))

const electron = vi.hoisted(() => {
  const handlers = new Map<string, (...args: unknown[]) => unknown>()

  return {
    handlers,
    app: {
      isPackaged: false,
      getAppPath: () => dirs.root,
      getPath: (name: string) => path.join(dirs.root, name)
    },
    ipcMain: {
      handle: vi.fn((channel: string, fn: (...args: unknown[]) => unknown) => handlers.set(channel, fn))
    },
    // Reversible stand-in for the OS encryption: never the plain token on disk.
    safeStorage: {
      isEncryptionAvailable: vi.fn(() => true),
      encryptString: vi.fn((value: string) => Buffer.from(`sealed:${Buffer.from(value).toString('base64')}`)),
      decryptString: vi.fn((buffer: Buffer) =>
        Buffer.from(buffer.toString().replace(/^sealed:/, ''), 'base64').toString()
      )
    },
    shell: { openPath: vi.fn(async () => '') }
  }
})

const core = vi.hoisted(() => ({
  demarrer: vi.fn(),
  trouverBash: vi.fn(() => 'C:\\bash.exe'),
  versCheminBash: vi.fn((value: string) => value)
}))

vi.mock('electron', () => electron)
vi.mock('../../../mbiling_mains_client/mains.cjs', () => core)

const SETTINGS = {
  actif: true,
  relais_url: 'wss://relais.test/mains',
  bash: null,
  marge_timeout_s: 15,
  reconnexion: { delai_initial_ms: 1000, delai_max_ms: 30000 }
}

const POLICY = { dossiers_autorises: ['{documents}\\Mbiling-Test'], interdits: ['c:[\\\\/]+windows'] }

function writeShipped(name: string, value: unknown): void {
  fs.writeFileSync(path.join(dirs.root, 'config', name), JSON.stringify(value), 'utf8')
}

beforeEach(() => {
  dirs.root = fs.mkdtempSync(path.join(os.tmpdir(), 'mbiling-mains-'))
  fs.mkdirSync(path.join(dirs.root, 'config'))
  fs.mkdirSync(path.join(dirs.root, 'userData'))
  writeShipped('mains.json', SETTINGS)
  writeShipped('mains-politique.json', POLICY)
  writeShipped('connexion-par-defaut.json', { adresse: 'https://api.mbiling.test' })
  electron.handlers.clear()
  core.demarrer.mockReset()
  core.demarrer.mockImplementation(() => ({ arreter: vi.fn() }))
  vi.resetModules()
})

afterEach(() => {
  fs.rmSync(dirs.root, { recursive: true, force: true })
})

async function loadMains() {
  const mod = await import('./mains-desktop')
  mod.registerMainsIpc()

  return mod
}

const invoke = (channel: string, ...args: unknown[]) => electron.handlers.get(channel)!({}, ...args)

test('does not start without a device token', async () => {
  const mains = await loadMains()
  mains.startMains()

  assert.equal(core.demarrer.mock.calls.length, 0)
  const status = mains.mainsStatus()
  assert.equal(status.jetonDefini, false)
  assert.equal(status.enMarche, false)
  assert.equal(status.relais, SETTINGS.relais_url)
})

test('copies the shipped settings to userData on first use, and the user copy then wins', async () => {
  const mains = await loadMains()
  mains.mainsStatus()
  const userCopy = path.join(dirs.root, 'userData', 'mains.json')
  assert.ok(fs.existsSync(userCopy))

  fs.writeFileSync(userCopy, `\uFEFF${JSON.stringify({ ...SETTINGS, relais_url: 'wss://mine.test/mains' })}`)
  assert.equal(mains.mainsStatus().relais, 'wss://mine.test/mains')
})

test('saving a token stores it encrypted and starts the Mains with the local policy', async () => {
  await loadMains()
  const status = (await invoke('mbiling:mains:set-token', '  secret-appareil  ')) as { enMarche: boolean }

  const sealed = fs.readFileSync(path.join(dirs.root, 'userData', 'mains-jeton.bin')).toString()
  assert.ok(!sealed.includes('secret-appareil'), 'the token must never be stored in clear')
  assert.equal(status.enMarche, true)

  const [config, options] = core.demarrer.mock.calls[0] as [
    { relais_url: string; politique: { dossiers_autorises: string[]; interdits: string[] } },
    { jeton: string; bashEmbarques: string[] }
  ]
  assert.equal(options.jeton, 'secret-appareil')
  assert.equal(config.relais_url, SETTINGS.relais_url)
  assert.deepEqual(config.politique.dossiers_autorises, [path.join(dirs.root, 'documents') + '\\Mbiling-Test'])
  assert.deepEqual(config.politique.interdits, POLICY.interdits)
})

test('reports the connection state the engine gives', async () => {
  const mains = await loadMains()
  await invoke('mbiling:mains:set-token', 'jeton')
  const options = core.demarrer.mock.calls[0][1] as { surEtat: (s: object) => void }

  options.surEtat({ connecte: true, appareil: 'pc-test', raison: null })
  assert.equal(mains.mainsStatus().connecte, true)
  assert.equal(mains.mainsStatus().appareil, 'pc-test')
})

test('turning the Mains off stops them and is saved in the settings file', async () => {
  const mains = await loadMains()
  await invoke('mbiling:mains:set-token', 'jeton')
  const engine = core.demarrer.mock.results[0].value as { arreter: ReturnType<typeof vi.fn> }

  const status = (await invoke('mbiling:mains:set-active', false)) as { actif: boolean; enMarche: boolean }
  assert.equal(engine.arreter.mock.calls.length, 1)
  assert.equal(status.actif, false)
  assert.equal(status.enMarche, false)
  const saved = JSON.parse(fs.readFileSync(path.join(dirs.root, 'userData', 'mains.json'), 'utf8'))
  assert.equal(saved.actif, false)
  assert.equal(mains.mainsStatus().actif, false)
})

test('removing the token deletes it and stops the Mains', async () => {
  await loadMains()
  await invoke('mbiling:mains:set-token', 'jeton')
  const status = (await invoke('mbiling:mains:set-token', '')) as { jetonDefini: boolean; enMarche: boolean }

  assert.equal(status.jetonDefini, false)
  assert.equal(status.enMarche, false)
  assert.ok(!fs.existsSync(path.join(dirs.root, 'userData', 'mains-jeton.bin')))
})

test('refuses to keep a token when OS encryption is unavailable', async () => {
  await loadMains()
  electron.safeStorage.isEncryptionAvailable.mockReturnValueOnce(false)

  assert.throws(() => invoke('mbiling:mains:set-token', 'jeton'), /chiffrement/)
  assert.ok(!fs.existsSync(path.join(dirs.root, 'userData', 'mains-jeton.bin')))
})

test('an engine start failure is reported instead of thrown', async () => {
  const mains = await loadMains()
  core.demarrer.mockImplementationOnce(() => {
    throw new Error('bash introuvable')
  })

  await invoke('mbiling:mains:set-token', 'jeton')
  assert.equal(mains.mainsStatus().enMarche, false)
  assert.match(mains.mainsStatus().raison ?? '', /bash introuvable/)
})

test('the first-launch address comes from the settings file', async () => {
  const config = await import('./mbiling-desktop-config')
  config.registerDefaultConnectionIpc(electron.ipcMain as never, () => true)

  assert.deepEqual(await invoke('mbiling:default-connection'), {
    adresse: 'https://api.mbiling.test',
    clientLeger: true
  })

  fs.writeFileSync(path.join(dirs.root, 'userData', 'connexion-par-defaut.json'), 'not json')
  assert.deepEqual(await invoke('mbiling:default-connection'), { adresse: '', clientLeger: true })
})

// --- Automatic pairing: the app enrols this PC with the connected server ------------------------

async function loadMainsConnected(reply: unknown = { appareil: 'pc-auto', jeton: 'jeton-auto', relais_url: 'wss://srv.test/mains' }) {
  const request = vi.fn(async () => reply)
  const mod = await import('./mains-desktop')
  mod.registerMainsIpc({ request, connected: () => true })

  return { mains: mod, request }
}

const tick = () => new Promise(resolve => setTimeout(resolve, 0))

test('the consent answer is recorded without starting anything', async () => {
  const { mains, request } = await loadMainsConnected()

  await invoke('mbiling:mains:consent', false)
  assert.equal(mains.mainsStatus().actif, false)
  assert.equal(request.mock.calls.length, 0)
  assert.equal(core.demarrer.mock.calls.length, 0)
})

test('once allowed and connected, the PC enrols by itself and the Mains start', async () => {
  const { mains, request } = await loadMainsConnected()

  const status = (await invoke('mbiling:mains:enrol')) as { jetonDefini: boolean; enMarche: boolean; relais: string }

  assert.deepEqual(request.mock.calls[0].slice(0, 2), ['/api/plugins/mbiling_mains/appareil', 'POST'])
  assert.equal(JSON.parse(request.mock.calls[0][2] as string).nom, os.hostname())
  assert.equal(status.jetonDefini, true)
  assert.equal(status.enMarche, true)
  assert.equal(status.relais, 'wss://srv.test/mains', 'the relay address given by the server is kept')
  const sealed = fs.readFileSync(path.join(dirs.root, 'userData', 'mains-jeton.bin')).toString()
  assert.ok(!sealed.includes('jeton-auto'), 'the device token must never be stored in clear')
  assert.equal((core.demarrer.mock.calls[0][1] as { jeton: string }).jeton, 'jeton-auto')
  assert.equal(mains.mainsStatus().inscriptionAutomatique, true)
})

test('applying a connection starts the Mains, which enrol when allowed and no token exists', async () => {
  const { mains, request } = await loadMainsConnected()
  await invoke('mbiling:mains:consent', true)

  mains.startMains()
  await tick()
  await tick()

  assert.equal(request.mock.calls.length, 1)
  assert.equal(mains.mainsStatus().jetonDefini, true)
})

test('never enrols while no remote server is connected', async () => {
  const request = vi.fn(async () => ({ appareil: 'x', jeton: 'y' }))
  const mod = await import('./mains-desktop')
  mod.registerMainsIpc({ request, connected: () => false })
  await invoke('mbiling:mains:consent', true)

  mod.startMains()
  await tick()

  assert.equal(request.mock.calls.length, 0)
  assert.equal(mod.mainsStatus().inscriptionAutomatique, false)
  await assert.rejects(() => invoke('mbiling:mains:enrol') as Promise<unknown>, /connectez-vous/)
})

test('a relay refusal (revoked token) triggers exactly one re-enrolment', async () => {
  const { request } = await loadMainsConnected()
  await invoke('mbiling:mains:enrol')
  const surEtat = (core.demarrer.mock.calls[0][1] as { surEtat: (s: object) => void }).surEtat

  surEtat({ connecte: false, appareil: null, raison: 'refusé par le Relais : jeton inconnu' })
  await tick()
  await tick()
  assert.equal(request.mock.calls.length, 2)

  const surEtat2 = (core.demarrer.mock.calls.at(-1)![1] as { surEtat: (s: object) => void }).surEtat
  surEtat2({ connecte: false, appareil: null, raison: 'refusé par le Relais : jeton inconnu' })
  await tick()
  assert.equal(request.mock.calls.length, 2, 'no enrolment loop')
})

test('an enrolment failure is reported and nothing is stored', async () => {
  const { mains } = await loadMainsConnected({ appareil: 'pc', jeton: '' })

  await assert.rejects(() => invoke('mbiling:mains:enrol') as Promise<unknown>, /inscription de ce PC impossible/)
  assert.equal(mains.mainsStatus().jetonDefini, false)
  assert.match(mains.mainsStatus().raison ?? '', /inscription de ce PC impossible/)
})
