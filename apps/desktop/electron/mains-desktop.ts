/**
 * The Mains inside Mbiling Desktop: this PC runs the commands an agent on the Mbiling server
 * sends, only within the local policy (mains-politique.json), which is applied before every
 * command whatever the server says. The engine is the same module as the standalone client
 * (mbiling_mains_client/mains.cjs); this file adds what the desktop owns: settings files, the
 * device token kept encrypted by the OS, the bundled Git Bash, a local log and IPC for the UI.
 */
import fs from 'node:fs'
import path from 'node:path'

import { app, ipcMain, safeStorage, shell } from 'electron'

import * as mainsCore from '../../../mbiling_mains_client/mains.cjs'
import type { MainsConfig, MainsEtat } from '../../../mbiling_mains_client/mains.cjs'

import { expandUserPath, readMbilingConfig, userConfigPath } from './mbiling-desktop-config'

interface MainsDesktopSettings {
  actif: boolean
  relais_url: string
  bash: string | null
  marge_timeout_s: number
  reconnexion: { delai_initial_ms: number; delai_max_ms: number }
}

interface MainsPolicy {
  dossiers_autorises: string[]
  interdits: string[]
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
}

const TOKEN_FILE = 'mains-jeton.bin'

let engine: { arreter(): void } | null = null
let state: MainsEtat = { connecte: false, appareil: null, raison: null }
let lastError: string | null = null

const tokenPath = (): string => path.join(app.getPath('userData'), TOKEN_FILE)
const logPath = (): string => path.join(app.getPath('userData'), 'logs', 'mains.log')

/** Git Bash shipped with the app first, then one Mbiling's own tools installed. */
function bundledBashCandidates(): string[] {
  if (process.platform !== 'win32') {
    return []
  }

  const local = process.env.LOCALAPPDATA || ''

  return [
    path.join(process.resourcesPath, 'git', 'bin', 'bash.exe'),
    path.join(local, 'mbiling', 'git', 'bin', 'bash.exe')
  ]
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

function readPolicy(): MainsPolicy {
  const policy = readMbilingConfig<MainsPolicy>('politique')

  return {
    dossiers_autorises: (policy.dossiers_autorises || []).map(expandUserPath),
    interdits: policy.interdits || []
  }
}

function resolveBash(settings: MainsDesktopSettings): string | null {
  try {
    return mainsCore.trouverBash({ bash: settings.bash }, bundledBashCandidates())
  } catch {
    return null
  }
}

/** Starts the Mains when they are enabled and a device token is set; stops them otherwise. */
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

  const token = readToken()

  if (!settings.actif || !token) {
    return
  }

  try {
    const config: MainsConfig = {
      relais_url: settings.relais_url,
      bash: settings.bash,
      marge_timeout_s: settings.marge_timeout_s,
      reconnexion: settings.reconnexion,
      politique: readPolicy()
    }

    engine = mainsCore.demarrer(config, {
      jeton: token,
      bashEmbarques: bundledBashCandidates(),
      journal: writeLog,
      surEtat: next => {
        state = next
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
    journal: logPath()
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

function setActive(active: boolean): void {
  const file = userConfigPath('mains')
  const settings = readSettings()
  fs.writeFileSync(file, `${JSON.stringify({ ...settings, actif: active }, null, 2)}\n`, 'utf8')
}

/** IPC for the settings page. Every change restarts the Mains with the new values. */
export function registerMainsIpc(): void {
  ipcMain.handle('mbiling:mains:status', () => mainsStatus())
  ipcMain.handle('mbiling:mains:set-token', (_event, token: unknown) => {
    setToken(typeof token === 'string' ? token : '')
    startMains()

    return mainsStatus()
  })
  ipcMain.handle('mbiling:mains:set-active', (_event, active: unknown) => {
    setActive(active === true)
    startMains()

    return mainsStatus()
  })
  ipcMain.handle('mbiling:mains:restart', () => {
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
