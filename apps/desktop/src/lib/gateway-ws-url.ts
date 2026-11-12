import { resolveGatewayWsUrl } from '@mbiling/shared'

import type { MbilingConnection } from '@/global'

export function resolveDesktopGatewayWsUrl(
  desktop: Window['mbilingDesktop'],
  connection: MbilingConnection
): Promise<string> {
  // Only a registry-scoped descriptor may use the *For bridge (see
  // MbilingConnection.registryScoped); an absent bridge fails closed rather
  // than minting the peer's URL against the local pool.
  const { connectionId, profile, registryScoped } = connection

  if (!registryScoped || !connectionId) {
    return resolveGatewayWsUrl(desktop, connection)
  }

  const mint = desktop.getGatewayWsUrlFor

  return resolveGatewayWsUrl({ getGatewayWsUrl: mint ? () => mint({ connectionId, profile }) : undefined }, connection)
}
