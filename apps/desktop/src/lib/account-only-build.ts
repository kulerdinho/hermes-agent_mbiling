/**
 * True for Mbiling user builds (connexion_avancee off in connexion-par-defaut.json): the app only
 * works signed in to a Mbiling account in the cloud, so nothing may offer a local engine, a local
 * terminal or a manual connection. A bridge without Mbiling settings (older host) keeps every
 * option; an unreadable setting keeps the safe answer (account only).
 */
export async function accountOnlyBuild(): Promise<boolean> {
  const getDefaults = window.mbilingDesktop?.getDefaultConnection

  if (typeof getDefaults !== 'function') {
    return false
  }

  try {
    return !(await getDefaults()).connexionAvancee
  } catch {
    return true
  }
}
