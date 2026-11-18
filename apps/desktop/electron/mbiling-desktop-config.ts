/**
 * Mbiling Desktop settings files (connection proposed at first launch, the Mains, the local
 * policy). Defaults ship in `resources/mbiling-config` (apps/desktop/config in a dev checkout);
 * on first use each one is copied into userData, where the user's copy wins. Server addresses are
 * the exception: they always come from the shipped files (the connection file is never copied, and
 * the Mains relay address is read from the shipped mains.json), so an update that changes the server
 * is never undone by a copy left by an older version. No value is written in code: see
 * apps/desktop/config/LISEZMOI.md.
 */
import fs from 'node:fs'
import path from 'node:path'

import { app } from 'electron'

export const MBILING_CONFIG_FILES = {
  connexion: 'connexion-par-defaut.json',
  mains: 'mains.json',
  politique: 'mains-politique.json'
} as const

export type MbilingConfigName = keyof typeof MBILING_CONFIG_FILES

/** Folder of the defaults shipped with the app. */
export function shippedConfigDir(): string {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'mbiling-config')
    : path.join(app.getAppPath(), 'config')
}

/** The user's editable copy (created from the shipped default on first use). */
export function userConfigPath(name: MbilingConfigName): string {
  return path.join(app.getPath('userData'), MBILING_CONFIG_FILES[name])
}

/** Settings files that always come from the app: never copied to, nor read from, userData. */
const SHIPPED_ONLY: ReadonlySet<MbilingConfigName> = new Set(['connexion'])

function parseConfig<T>(file: string): T {
  return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '')) as T
}

/** Reads the default shipped with the app, ignoring any user copy. */
export function readShippedConfig<T>(name: MbilingConfigName): T {
  return parseConfig<T>(path.join(shippedConfigDir(), MBILING_CONFIG_FILES[name]))
}

/** Reads a settings file: the user's copy, else the shipped default (copied for later edits). */
export function readMbilingConfig<T>(name: MbilingConfigName): T {
  if (SHIPPED_ONLY.has(name)) {
    return readShippedConfig<T>(name)
  }

  const userFile = userConfigPath(name)

  if (!fs.existsSync(userFile)) {
    const shipped = path.join(shippedConfigDir(), MBILING_CONFIG_FILES[name])
    fs.mkdirSync(path.dirname(userFile), { recursive: true })
    fs.copyFileSync(shipped, userFile)
  }

  return parseConfig<T>(userFile)
}

/** `{documents}` / `{home}` in a configured path become the user's real folders. */
export function expandUserPath(value: string): string {
  return value.replaceAll('{documents}', app.getPath('documents')).replaceAll('{home}', app.getPath('home'))
}

export interface DefaultConnection {
  /** The Mbiling server the app signs in to ('' when none is configured). */
  adresse: string
  /** Remote-only client (light variant): never offers a local install. */
  clientLeger: boolean
  /**
   * Team/support builds only: also offer the manual connection (address, token) and the local
   * install. Off for users: Mbiling only works signed in to a Mbiling account in the cloud.
   */
  connexionAvancee: boolean
  /** Social sign-ins offered as buttons ("Continue with Google"), from the settings file. */
  fournisseurs: { id: string; libelle: string }[]
}

/** IPC answering the renderer's first-launch screen. */
export function registerDefaultConnectionIpc(ipcMain: Electron.IpcMain, isLight: () => boolean): void {
  ipcMain.handle('mbiling:default-connection', (): DefaultConnection => {
    let config: { adresse?: unknown; connexion_avancee?: unknown; fournisseurs?: unknown } = {}

    try {
      config = readMbilingConfig<{ adresse?: unknown; connexion_avancee?: unknown; fournisseurs?: unknown }>(
        'connexion'
      )
    } catch {
      config = {}
    }

    const fournisseurs = (Array.isArray(config.fournisseurs) ? config.fournisseurs : [])
      .map(f => ({ id: String((f as { id?: unknown })?.id ?? '').trim(), libelle: String((f as { libelle?: unknown })?.libelle ?? '').trim() }))
      .filter(f => /^[a-z0-9_-]{1,40}$/.test(f.id) && f.libelle)

    return {
      adresse: String(config.adresse || '').trim(),
      clientLeger: isLight(),
      connexionAvancee: config.connexion_avancee === true,
      fournisseurs
    }
  })
}

/**
 * True for user builds: Mbiling only works signed in to a Mbiling account in the cloud, so no
 * manual connection and no local engine. Team builds set connexion_avancee: true. An unreadable
 * settings file keeps the safe answer (account only).
 */
export function accountOnly(): boolean {
  try {
    return readMbilingConfig<{ connexion_avancee?: unknown }>('connexion').connexion_avancee !== true
  } catch {
    return true
  }
}

/** The name user builds show wherever a connection would be named ("nom" in the settings file). */
export function accountDisplayName(): string {
  try {
    const nom = String(readMbilingConfig<{ nom?: unknown }>('connexion').nom ?? '').trim()

    if (nom) {
      return nom
    }
  } catch {
    // An unreadable settings file falls back to the app's own name.
  }

  return app.getName()
}

interface RegistryLike<C> {
  primary: string
  lastUsed?: string | null
  connections: C[]
}

/**
 * What user builds let leave the main process about the connection registry: never the local engine
 * (it is never installed), never a server address, never the hostname used as a label. `stripAddress`
 * is false where the registry is still needed to DIAL (roster enumeration): the address stays in this
 * process and only the labels change.
 */
export function accountOnlyRegistryView<C extends { kind: string; label?: string }, R extends RegistryLike<C>>(
  registry: R,
  { localId, stripAddress }: { localId: string; stripAddress: boolean }
): R {
  if (!accountOnly()) {
    return registry
  }

  const hideLocal = registry.primary !== localId
  const name = accountDisplayName()

  const connections = registry.connections
    .filter(connection => !(hideLocal && connection.kind === 'local'))
    .map(connection => {
      const view: Record<string, unknown> = { ...connection, label: name }

      if (stripAddress) {
        view.url = ''

        for (const key of ['host', 'user', 'port', 'org', 'remoteMbilingPath', 'remoteProfile']) {
          delete view[key]
        }
      }

      return view as unknown as C
    })

  return {
    ...registry,
    lastUsed: hideLocal && registry.lastUsed === localId ? registry.primary : registry.lastUsed,
    connections
  }
}
