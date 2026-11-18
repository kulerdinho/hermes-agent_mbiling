/**
 * Mbiling user builds: signing out (or a session the server no longer accepts) ends the use of the app,
 * not just the cookies. The account's session is dropped, then the connection falls back to "local",
 * which these builds never install: the main process tears the server connection down and waits for a
 * sign-in, so the sign-in screen covers the whole window again and nothing reaches the server until
 * the user signs back in.
 */
export async function signOutOfAccount(): Promise<void> {
  const desktop = window.mbilingDesktop

  if (!desktop) {
    return
  }

  const config = await desktop.getConnectionConfig().catch(() => null)

  if (config?.remoteUrl) {
    await desktop.oauthLogoutConnectionConfig(config.remoteUrl).catch(() => undefined)
  }

  await desktop.applyConnectionConfig({ mode: 'local' })

  // Leave whatever page was open (settings, a chat): the next screen is the sign-in.
  window.location.hash = '#/'
}
