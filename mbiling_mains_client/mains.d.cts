// Types of mains.cjs, for Mbiling Desktop (TypeScript) which embeds it.

export interface MainsConfig {
  relais_url: string
  jeton_env?: string
  bash?: string | null
  marge_timeout_s: number
  reconnexion: { delai_initial_ms: number; delai_max_ms: number }
  politique?: { dossiers_autorises?: string[]; interdits?: string[]; sites_interdits?: string[] }
  navigateur?: MainsNavigateur
}

/** Navigateur piloté par l'agent sur ce PC (lot 6). */
export interface MainsNavigateur {
  actif: boolean
  /** Chemin explicite du navigateur ; sinon le premier trouvé selon `preference`. */
  chemin?: string | null
  preference: string[]
  /** Dossier du profil dédié à Mbiling (séparé du profil personnel). */
  profil: string
  delai_demarrage_s: number
}

export interface MainsEtat {
  connecte: boolean
  appareil: string | null
  raison: string | null
}

export interface MainsOptions {
  journal?: (...elements: unknown[]) => void
  jeton?: string
  bashEmbarques?: string[]
  surEtat?: (etat: MainsEtat) => void
  WebSocket?: unknown
}

export function demarrer(config: MainsConfig, options?: MainsOptions): { arreter(): void }
export function trouverBash(config: { bash?: string | null }, autres?: string[]): string
export function versCheminBash(chemin: string): string
