/**
 * The Mains inside Mbiling Desktop: this PC runs the commands an agent on the Mbiling server
 * sends, only within the local policy (mains-politique.json), which is applied before every
 * command whatever the server says. The engine is the same module as the standalone client
 * (mbiling_mains_client/mains.cjs); this file adds what the desktop owns: settings files, the
 * device token kept encrypted by the OS, the bundled Git Bash, a local log, IPC for the UI, and
 * automatic pairing: once the user is signed in and has allowed it, the app asks the server for
 * this PC's device token by itself (the user never types an address or a token).
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { app, ipcMain, safeStorage, shell } from 'electron'

import * as mainsCore from '../../../mbiling_mains_client/mains.cjs'
import type { MainsConfig, MainsEtat, NavigateurConfig } from '../../../mbiling_mains_client/mains.cjs'

import { expandUserPath, readMbilingConfig, readShippedConfig, userConfigPath } from './mbiling-desktop-config'

interface MainsDesktopSettings {
  actif: boolean
  relais_url: string
  bash: string | null
  marge_timeout_s: number
  reconnexion: { delai_initial_ms: number; delai_max_ms: number }
  /** The PC's browser, driven by the agent through the Relay (CDP tunnel). */
  navigateur?: NavigateurConfig
}

interface MainsPolicy {
  dossiers_autorises: string[]
  interdits: string[]
  /** Addresses the agent may never open in the PC's browser. */
  sites_interdits: string[]
}

export interface MainsStatus {
  actif: boolean
  jetonDefini: boolean
  chiffrementDisponible: boolean
  enMarche: boolean
  connecte: boolean
  appareil: string | null
  raison: string | null
  relais: string
  dossiers: string[]
  bash: string | null
  fichierPolitique: string
  fichierReglages: string
  journal: string
  /** The app can enrol this PC by itself (a Mbiling server connection is available). */
  inscriptionAutomatique: boolean
  inscriptionEnCours: boolean
  /** The PC's browser the agent may drive (CDP tunnel). */
  navigateur: MainsBrowserStatus
}

export interface BlockedSite {
  /** Pattern stored in mains-politique.json (sites_interdits). */
  motif: string
  /** What the user typed: the domain for a domain rule, otherwise the pattern itself. */
  libelle: string
}

export interface MainsBrowserStatus {
  actif: boolean
  prefere: string
  /** Browser that will be launched, or null when neither Edge nor Chrome is found. */
  executable: string | null
  sitesInterdits: BlockedSite[]
}

const DOMAIN = /^[a-z0-9-]+(\.[a-z0-9-]+)+$/i
const DOMAIN_RULE = /^\^https\?:\/\/\(\[\^\/\]\*\\\.\)\?(.+)\(\?=\[\/:\?#\]\|\$\)$/

/**
 * A site the user types becomes a pattern: a domain ("banque.fr", with or without https://)
 * blocks it and its subdomains; anything else must be a valid regular expression (support).
 */
export function siteToPattern(entry: string): string {
  const value = entry.trim()
  const bare = value.replace(/^https?:\/\//i, '').replace(/\/+$/, '')

  if (DOMAIN.test(bare)) {
    return `^https?://([^/]*\\.)?${bare.toLowerCase().replaceAll('.', '\\.')}(?=[/:?#]|$)`
  }

  if (!value) {
    throw new Error('adresse vide')
  }

  new RegExp(value, 'i')

  return value
}

/** The label shown for a stored pattern (the domain back for a domain rule). */
export function patternToSite(pattern: string): string {
  const match = DOMAIN_RULE.exec(pattern)

  return match ? match[1].replaceAll('\\.', '.') : pattern
}

/** Authenticated request to the connected Mbiling server (injected by main.ts). */
// `body` is a plain object: the request function JSON-encodes it itself (never pass a string).
export type BackendRequest = (pathname: string, method: string, body?: Record<string, unknown>) => Promise<unknown>

interface Enrolment {
  appareil: string
  jeton: string
  relais_url?: string | null
}

const TOKEN_FILE = 'mains-jeton.bin'
const ENROL_PATH = '/api/plugins/mbiling_mains/appareil'

let engine: { arreter(): void } | null = null
let state: MainsEtat = { connecte: false, appareil: null, raison: null }
let lastError: string | null = null
let requestBackend: BackendRequest | null = null
// True when the app is connected to a remote Mbiling server (never enrol against a local backend).
let serverConnected: () => boolean = () => false
let enrolling: Promise<void> | null = null
// A relay refusal (revoked or unknown token) triggers ONE re-enrolment, never a loop.
let reenrolTried = false

const tokenPath = (): string => path.join(app.getPath('userData'), TOKEN_FILE)
const logPath = (): string => path.join(app.getPath('userData'), 'logs', 'mains.log')

/** Git Bash shipped with the app first, then one Mbiling's own tools installed. */
function bundledBashCandidates(): string[] {
  if (process.platform !== 'win32') {
    return []
  }

  const candidates: string[] = []

  // resourcesPath is set by Electron; it is absent when the module runs outside it (tests, tools).
  if (typeof process.resourcesPath === 'string' && process.resourcesPath) {
    candidates.push(path.join(process.resourcesPath, 'git', 'bin', 'bash.exe'))
  }

  if (process.env.LOCALAPPDATA) {
    candidates.push(path.join(process.env.LOCALAPPDATA, 'mbiling', 'git', 'bin', 'bash.exe'))
  }

  return candidates
}

function writeLog(...parts: unknown[]): void {
  const line = `${new Date().toISOString()} ${parts.map(p => (typeof p === 'string' ? p : JSON.stringify(p))).join(' ')}\n`

  try {
    fs.mkdirSync(path.dirname(logPath()), { recursive: true })
    fs.appendFileSync(logPath(), line, 'utf8')
  } catch {
    // The log is best effort: a full disk must not stop the Mains.
  }
}

function readToken(): string | null {
  try {
    const sealed = fs.readFileSync(tokenPath())

    return safeStorage.decryptString(sealed)
  } catch {
    return null
  }
}

/** The user's settings, except the relay address, which always comes from the app (see readShippedConfig). */
function readSettings(): MainsDesktopSettings {
  const settings = readMbilingConfig<MainsDesktopSettings>('mains')

  return { ...settings, relais_url: readShippedConfig<MainsDesktopSettings>('mains').relais_url }
}

function writeSettings(patch: Partial<Omit<MainsDesktopSettings, 'relais_url'>>): void {
  const { relais_url: _shipped, ...settings } = { ...readSettings(), ...patch }
  fs.writeFileSync(userConfigPath('mains'), `${JSON.stringify(settings, null, 2)}\n`, 'utf8')
}

function writePolicy(patch: Partial<MainsPolicy>): void {
  const raw = readMbilingConfig<Record<string, unknown>>('politique')
  fs.writeFileSync(userConfigPath('politique'), `${JSON.stringify({ ...raw, ...patch }, null, 2)}\n`, 'utf8')
}

function browserStatus(settings: MainsDesktopSettings | null, policy: MainsPolicy | null): MainsBrowserStatus {
  const browser = settings?.navigateur || {}
  let executable: string | null = null

  try {
    executable = mainsCore.trouverNavigateur(browser)
  } catch {
    executable = null
  }

  return {
    actif: browser.actif !== false,
    prefere: browser.prefere || 'edge',
    executable,
    sitesInterdits: (policy?.sites_interdits ?? []).map(motif => ({ motif, libelle: patternToSite(motif) }))
  }
}

function readPolicy(): MainsPolicy {
  const policy = readMbilingConfig<MainsPolicy>('politique')

  return {
    dossiers_autorises: (policy.dossiers_autorises || []).map(expandUserPath),
    interdits: policy.interdits || [],
    sites_interdits: policy.sites_interdits || []
  }
}

/** Browser settings; its profile lives in the app's data folder unless set otherwise. */
function browserSettings(settings: MainsDesktopSettings): NavigateurConfig {
  const browser = settings.navigateur || {}

  return {
    ...browser,
    profil: browser.profil ? expandUserPath(browser.profil) : path.join(app.getPath('userData'), 'navigateur')
  }
}

function resolveBash(settings: MainsDesktopSettings): string | null {
  try {
    return mainsCore.trouverBash({ bash: settings.bash }, bundledBashCandidates())
  } catch {
    return null
  }
}

/**
 * Starts the Mains when the user allowed them and a device token is set. Allowed but without a
 * token: enrols this PC with the connected server first (when one is connected).
 */
export function startMains(): void {
  stopMains()
  lastError = null

  let settings: MainsDesktopSettings

  try {
    settings = readSettings()
  } catch (err) {
    lastError = `réglages des Mains illisibles : ${err instanceof Error ? err.message : String(err)}`
    writeLog('ERREUR', lastError)

    return
  }

  if (!settings.actif) {
    return
  }

  const token = readToken()

  if (!token) {
    if (requestBackend && serverConnected()) {
      void enrolMains().catch(() => undefined)
    }

    return
  }

  try {
    const config: MainsConfig = {
      relais_url: settings.relais_url,
      bash: settings.bash,
      marge_timeout_s: settings.marge_timeout_s,
      reconnexion: settings.reconnexion,
      politique: readPolicy(),
      navigateur: browserSettings(settings)
    }

    engine = mainsCore.demarrer(config, {
      jeton: token,
      bashEmbarques: bundledBashCandidates(),
      journal: writeLog,
      surEtat: next => {
        state = next

        if (next.raison?.startsWith('refusé par le Relais') && requestBackend && serverConnected() && !reenrolTried) {
          reenrolTried = true
          writeLog('RÉINSCRIPTION', next.raison)
          setToken('')
          void enrolMains().catch(() => undefined)
        }
      }
    })
    writeLog('DÉMARRAGE', settings.relais_url)
  } catch (err) {
    lastError = err instanceof Error ? err.message : String(err)
    writeLog('ERREUR', lastError)
  }
}

export function stopMains(): void {
  if (engine) {
    engine.arreter()
    engine = null
    writeLog('ARRÊT')
  }

  state = { connecte: false, appareil: null, raison: null }
}

export function mainsStatus(): MainsStatus {
  let settings: MainsDesktopSettings | null = null
  let policy: MainsPolicy | null = null

  try {
    settings = readSettings()
    policy = readPolicy()
  } catch (err) {
    lastError = err instanceof Error ? err.message : String(err)
  }

  return {
    actif: Boolean(settings?.actif),
    jetonDefini: fs.existsSync(tokenPath()),
    chiffrementDisponible: safeStorage.isEncryptionAvailable(),
    enMarche: engine !== null,
    connecte: state.connecte,
    appareil: state.appareil,
    raison: lastError ?? state.raison,
    relais: settings?.relais_url ?? '',
    dossiers: policy?.dossiers_autorises ?? [],
    bash: settings ? resolveBash(settings) : null,
    fichierPolitique: userConfigPath('politique'),
    fichierReglages: userConfigPath('mains'),
    journal: logPath(),
    inscriptionAutomatique: requestBackend !== null && serverConnected(),
    inscriptionEnCours: enrolling !== null,
    navigateur: browserStatus(settings, policy)
  }
}

function setToken(token: string): void {
  const trimmed = token.trim()

  if (!trimmed) {
    fs.rmSync(tokenPath(), { force: true })

    return
  }

  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error('Le chiffrement de Windows est indisponible : le jeton ne peut pas être conservé.')
  }

  fs.mkdirSync(path.dirname(tokenPath()), { recursive: true })
  fs.writeFileSync(tokenPath(), safeStorage.encryptString(trimmed))
}

/**
 * Enrols this PC with the connected Mbiling server: the server asks its relay for a device token
 * for the signed-in user's organisation; the token is stored encrypted, then the Mains start.
 * Concurrent calls share one request.
 */
export function enrolMains(): Promise<void> {
  if (enrolling) {
    return enrolling
  }

  const request = requestBackend

  if (!request || !serverConnected()) {
    return Promise.reject(new Error("Aucun serveur Mbiling connecté : connectez-vous d'abord."))
  }

  const run = async (): Promise<void> => {
    try {
      const reply = (await request(ENROL_PATH, 'POST', { nom: os.hostname() })) as Enrolment | null

      if (!reply || typeof reply.jeton !== 'string' || !reply.jeton) {
        throw new Error("le serveur n'a pas renvoyé de jeton pour ce PC")
      }

      setToken(reply.jeton)
      writeSettings({ actif: true })
      writeLog('INSCRIT', reply.appareil)
    } catch (err) {
      const message = `inscription de ce PC impossible : ${err instanceof Error ? err.message : String(err)}`
      writeLog('ERREUR', message)

      throw new Error(message)
    }
  }

  enrolling = run()
    .then(() => {
      enrolling = null
      startMains()
    })
    .catch((err: unknown) => {
      enrolling = null
      lastError = err instanceof Error ? err.message : String(err)

      throw err
    })

  return enrolling
}

/**
 * IPC for the first launch and the settings page. Every change restarts the Mains.
 * `request` lets the Mains enrol this PC with the connected server (automatic pairing).
 */
export function registerMainsIpc(options: { request?: BackendRequest; connected?: () => boolean } = {}): void {
  requestBackend = options.request ?? null
  serverConnected = options.connected ?? (() => false)
  ipcMain.handle('mbiling:mains:status', () => mainsStatus())
  // The user's answer to "Allow the agent to use this PC?", recorded before signing in: the Mains
  // then enrol on their own as soon as the server connection is applied.
  ipcMain.handle('mbiling:mains:consent', (_event, allowed: unknown) => {
    writeSettings({ actif: allowed === true })

    return mainsStatus()
  })
  ipcMain.handle('mbiling:mains:enrol', async () => {
    writeSettings({ actif: true })
    await enrolMains()

    return mainsStatus()
  })
  ipcMain.handle('mbiling:mains:set-token', (_event, token: unknown) => {
    setToken(typeof token === 'string' ? token : '')
    startMains()

    return mainsStatus()
  })
  ipcMain.handle('mbiling:mains:set-active', (_event, active: unknown) => {
    writeSettings({ actif: active === true })
    startMains()

    return mainsStatus()
  })
  ipcMain.handle('mbiling:mains:set-browser', (_event, patch: unknown) => {
    const next: NavigateurConfig = { ...(readSettings().navigateur || {}) }
    const value = (patch && typeof patch === 'object' ? patch : {}) as { actif?: unknown; prefere?: unknown }

    if (typeof value.actif === 'boolean') {
      next.actif = value.actif
    }

    if (value.prefere === 'edge' || value.prefere === 'chrome') {
      next.prefere = value.prefere
    }

    writeSettings({ navigateur: next })
    startMains()

    return mainsStatus()
  })
  ipcMain.handle('mbiling:mains:add-blocked-site', (_event, entry: unknown) => {
    const pattern = siteToPattern(typeof entry === 'string' ? entry : '')
    const current = readPolicy().sites_interdits

    if (!current.includes(pattern)) {
      writePolicy({ sites_interdits: [...current, pattern] })
      writeLog('SITE INTERDIT AJOUTÉ', pattern)
      startMains()
    }

    return mainsStatus()
  })
  ipcMain.handle('mbiling:mains:remove-blocked-site', (_event, pattern: unknown) => {
    const current = readPolicy().sites_interdits

    if (typeof pattern === 'string' && current.includes(pattern)) {
      writePolicy({ sites_interdits: current.filter(item => item !== pattern) })
      writeLog('SITE INTERDIT RETIRÉ', pattern)
      startMains()
    }

    return mainsStatus()
  })
  ipcMain.handle('mbiling:mains:restart', () => {
    reenrolTried = false
    startMains()

    return mainsStatus()
  })
  ipcMain.handle('mbiling:mains:open', async (_event, which: unknown) => {
    readPolicy()
    readSettings()

    const target =
      which === 'politique' ? userConfigPath('politique') : which === 'journal' ? logPath() : userConfigPath('mains')

    if (which === 'journal' && !fs.existsSync(target)) {
      writeLog('JOURNAL CRÉÉ')
    }

    const error = await shell.openPath(target)

    return error || null
  })
}
