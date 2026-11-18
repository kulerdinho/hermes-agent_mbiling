import { atom } from 'nanostores'

/**
 * Mbiling user builds (connexion_avancee off in connexion-par-defaut.json) only work signed in to the
 * cloud account: nothing may offer a local engine, SSH, another gateway or a manual connection, and
 * no server address may be shown. The atom serves synchronous gates (settings navigation, settings
 * search, command palette); it is loaded at startup by `main.tsx`, long before those can be opened.
 */
export const $accountOnlyBuild = atom(false)

export const isAccountOnlyBuild = (): boolean => $accountOnlyBuild.get()

/**
 * A bridge without Mbiling settings (older host, browser stub) keeps every option; an unreadable
 * setting keeps the safe answer (account only).
 */
export async function accountOnlyBuild(): Promise<boolean> {
  const getDefaults = window.mbilingDesktop?.getDefaultConnection

  let value: boolean

  if (typeof getDefaults !== 'function') {
    value = false
  } else {
    try {
      value = !(await getDefaults()).connexionAvancee
    } catch {
      value = true
    }
  }

  $accountOnlyBuild.set(value)

  return value
}
