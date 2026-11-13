/**
 * Mbiling Desktop settings files (connection proposed at first launch, the Mains, the local
 * policy). Defaults ship in `resources/mbiling-config` (apps/desktop/config in a dev checkout);
 * on first use each one is copied into userData, where the user's copy always wins. No value is
 * written in code: see apps/desktop/config/LISEZMOI.md.
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

/** Reads a settings file: the user's copy, else the shipped default (copied for later edits). */
export function readMbilingConfig<T>(name: MbilingConfigName): T {
  const userFile = userConfigPath(name)

  if (!fs.existsSync(userFile)) {
    const shipped = path.join(shippedConfigDir(), MBILING_CONFIG_FILES[name])
    fs.mkdirSync(path.dirname(userFile), { recursive: true })
    fs.copyFileSync(shipped, userFile)
  }

  return JSON.parse(fs.readFileSync(userFile, 'utf8').replace(/^\uFEFF/, '')) as T
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
}

/** IPC answering the renderer's first-launch screen. */
export function registerDefaultConnectionIpc(ipcMain: Electron.IpcMain, isLight: () => boolean): void {
  ipcMain.handle('mbiling:default-connection', (): DefaultConnection => {
    let config: { adresse?: unknown; connexion_avancee?: unknown } = {}

    try {
      config = readMbilingConfig<{ adresse?: unknown; connexion_avancee?: unknown }>('connexion')
    } catch {
      config = {}
    }

    return {
      adresse: String(config.adresse || '').trim(),
      clientLeger: isLight(),
      connexionAvancee: config.connexion_avancee === true
    }
  })
}
