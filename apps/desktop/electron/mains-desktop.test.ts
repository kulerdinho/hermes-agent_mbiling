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
  versCheminBash: vi.fn((value: string) => value),
  trouverNavigateur: vi.fn((): string => 'C:/Edge/msedge.exe')
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
  writeShipped('connexion-par-defaut.json', { adresse: 'https://api.mbiling.test', relais_url: SETTINGS.relais_url })
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

  fs.writeFileSync(userCopy, `\uFEFF${JSON.stringify({ ...SETTINGS, actif: false })}`)
  assert.equal(mains.mainsStatus().actif, false)
})

test('the relay address always comes from the app, never from a copy left by an older version', async () => {
  const mains = await loadMains()
  const userCopy = path.join(dirs.root, 'userData', 'mains.json')
  fs.writeFileSync(userCopy, JSON.stringify({ ...SETTINGS, relais_url: 'wss://old-server.test/mains' }))

  assert.equal(mains.mainsStatus().relais, SETTINGS.relais_url)
  await invoke('mbiling:mains:set-active', false)
  assert.equal(JSON.parse(fs.readFileSync(userCopy, 'utf8')).relais_url, undefined, 'the user copy no longer stores it')
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
    clientLeger: true,
    connexionAvancee: false,
    fournisseurs: []
  })

  // A copy left in userData by an older version (old server address) is never read.
  fs.writeFileSync(
    path.join(dirs.root, 'userData', 'connexion-par-defaut.json'),
    JSON.stringify({ adresse: 'https://old-server.test', connexion_avancee: true })
  )
  const fresh = (await invoke('mbiling:default-connection')) as { adresse: string; connexionAvancee: boolean }
  assert.equal(fresh.adresse, 'https://api.mbiling.test')
  assert.equal(fresh.connexionAvancee, false)

  writeShipped('connexion-par-defaut.json', {
    adresse: 'https://api.mbiling.test',
    connexion_avancee: true,
    fournisseurs: [{ id: 'google', libelle: 'Google' }, { id: 'Bad Id!', libelle: 'x' }, { id: 'apple' }]
  })

  const avancee = (await invoke('mbiling:default-connection')) as {
    connexionAvancee: boolean
    fournisseurs: { id: string; libelle: string }[]
  }

  assert.equal(avancee.connexionAvancee, true)
  assert.deepEqual(avancee.fournisseurs, [{ id: 'google', libelle: 'Google' }], 'invalid entries are dropped')

  // An unreadable file never unlocks the manual connection.
  fs.writeFileSync(path.join(dirs.root, 'config', 'connexion-par-defaut.json'), 'not json')
  assert.deepEqual(await invoke('mbiling:default-connection'), {
    adresse: '',
    clientLeger: true,
    connexionAvancee: false,
    fournisseurs: []
  })
})

// --- Automatic pairing: the app enrols this PC with the connected server ------------------------

async function loadMainsConnected(reply: unknown = { appareil: 'pc-auto', jeton: 'jeton-auto', relais_url: 'wss://srv.test/mains' }) {
  const request = vi.fn(async (_pathname: string, _method: string, _body?: Record<string, unknown>) => reply)
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
  // An object, not a JSON string: the request function encodes it (a string arrived double-encoded, 422).
  assert.deepEqual(request.mock.calls[0][2], { nom: os.hostname() })
  assert.equal(status.jetonDefini, true)
  assert.equal(status.enMarche, true)
  assert.equal(status.relais, SETTINGS.relais_url, 'the relay address comes from the app, like the server address')
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
  const request = vi.fn(async (_pathname: string, _method: string, _body?: Record<string, unknown>) => ({ appareil: 'x', jeton: 'y' }))
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

test('a domain typed by the user blocks it and its subdomains, never look-alikes', async () => {
  const { siteToPattern, patternToSite } = await loadMains()
  const pattern = siteToPattern(' https://Banque.fr/ ')
  const rule = new RegExp(pattern, 'i')

  assert.equal(patternToSite(pattern), 'banque.fr')
  assert.ok(rule.test('https://banque.fr/compte'))
  assert.ok(rule.test('https://www.banque.fr'))
  assert.ok(rule.test('http://banque.fr:8443/'))
  assert.ok(!rule.test('https://mabanque.fr/'))
  assert.ok(!rule.test('https://banque.fr.exemple.com/'))
  assert.ok(!rule.test('https://bancaire.exemple.com/?r=banque.fr'))
})

test('a pattern that is not a domain must be a valid regular expression', async () => {
  const { siteToPattern, patternToSite } = await loadMains()

  assert.equal(siteToPattern('^file:'), '^file:')
  assert.equal(patternToSite('^file:'), '^file:')
  assert.throws(() => siteToPattern('(non ferme'))
  assert.throws(() => siteToPattern('   '))
})

test('the browser settings and blocked sites are saved and passed to the Mains', async () => {
  await loadMains()
  await invoke('mbiling:mains:set-token', 'jeton')

  let status = (await invoke('mbiling:mains:set-browser', { actif: false, prefere: 'chrome' })) as {
    navigateur: { actif: boolean; prefere: string; executable: string | null }
  }

  assert.equal(status.navigateur.actif, false)
  assert.equal(status.navigateur.prefere, 'chrome')
  assert.equal(status.navigateur.executable, 'C:/Edge/msedge.exe')
  await invoke('mbiling:mains:set-browser', { prefere: 'firefox' })
  const saved = JSON.parse(fs.readFileSync(path.join(dirs.root, 'userData', 'mains.json'), 'utf8'))
  assert.deepEqual({ actif: saved.navigateur.actif, prefere: saved.navigateur.prefere }, { actif: false, prefere: 'chrome' })

  await invoke('mbiling:mains:add-blocked-site', 'banque.fr')
  status = (await invoke('mbiling:mains:add-blocked-site', 'banque.fr')) as typeof status & {
    navigateur: { sitesInterdits: { motif: string; libelle: string }[] }
  }
  const sites = (status.navigateur as unknown as { sitesInterdits: { motif: string; libelle: string }[] }).sitesInterdits
  assert.deepEqual(sites.map(site => site.libelle), ['banque.fr'], 'a site is only listed once')

  const policy = JSON.parse(fs.readFileSync(path.join(dirs.root, 'userData', 'mains-politique.json'), 'utf8'))
  assert.deepEqual(policy.interdits, POLICY.interdits, 'the rest of the policy is kept')

  const config = core.demarrer.mock.calls.at(-1)![0] as {
    navigateur: { actif: boolean; profil: string }
    politique: { sites_interdits: string[] }
  }

  assert.equal(config.navigateur.actif, false)
  assert.equal(config.navigateur.profil, path.join(dirs.root, 'userData', 'navigateur'))
  assert.deepEqual(config.politique.sites_interdits, [sites[0].motif])

  await invoke('mbiling:mains:remove-blocked-site', sites[0].motif)
  const after = JSON.parse(fs.readFileSync(path.join(dirs.root, 'userData', 'mains-politique.json'), 'utf8'))
  assert.deepEqual(after.sites_interdits, [])
})
