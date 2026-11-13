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

import { expandUserPath, readMbilingConfig, userConfigPath } from './mbiling-desktop-config'

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
}

/** Authenticated request to the connected Mbiling server (injected by main.ts). */
export type BackendRequest = (pathname: string, method: string, body?: string) => Promise<unknown>

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

function readSettings(): MainsDesktopSettings {
  return readMbilingConfig<MainsDesktopSettings>('mains')
}

function writeSettings(patch: Partial<MainsDesktopSettings>): void {
  const settings = readSettings()
  fs.writeFileSync(userConfigPath('mains'), `${JSON.stringify({ ...settings, ...patch }, null, 2)}\n`, 'utf8')
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
    inscriptionEnCours: enrolling !== null
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
      const reply = (await request(ENROL_PATH, 'POST', JSON.stringify({ nom: os.hostname() }))) as Enrolment | null

      if (!reply || typeof reply.jeton !== 'string' || !reply.jeton) {
        throw new Error("le serveur n'a pas renvoyé de jeton pour ce PC")
      }

      setToken(reply.jeton)
      writeSettings({ actif: true, ...(reply.relais_url ? { relais_url: reply.relais_url } : {}) })
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
