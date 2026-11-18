import { isAccountOnlyBuild } from '@/lib/account-only-build'
import { hostHas } from '@/lib/host-capabilities'

import type { SettingsSubpage } from './subpages'

// Gateway tasks and search rows that only native Desktop can act on; the Webapp's
// Gateways page is just its serving host (see WebappGatewaySettings).
export const nativeHostOnly = () => hostHas('gatewayManagement')

// Mbiling user builds only work signed in to the cloud account: no saved connections to add, edit or
// test, no SSH gateways to update, no connection mode to search for. Only the account page remains.
export const connectionManagementOnly = () => nativeHostOnly() && !isAccountOnlyBuild()

// Pages with separate tasks expose content-only subpages. Billing keeps its
// existing capability-gated bview=overview|plans flow rather than adding a
// second route parameter that could disagree with a pending financial action.
export const OTHER_SUBPAGES: Record<string, SettingsSubpage[]> = {
  gateway: [
    { id: 'connection', labelKey: 'gatewayConnection', available: nativeHostOnly },
    { id: 'devices', labelKey: 'gatewayDevices', available: connectionManagementOnly },
    { id: 'managed-updates', labelKey: 'gatewayManagedUpdates', available: connectionManagementOnly }
  ],
  keybinds: [
    { id: 'shortcuts', labelKey: 'keyboardShortcuts' },
    { id: 'hud-gesture', labelKey: 'hudGesture' },
    { id: 'screen-capture', labelKey: 'screenCapture' }
  ],
  notifications: [
    { id: 'alerts', labelKey: 'notificationAlerts' },
    { id: 'sounds', labelKey: 'notificationSounds' }
  ],
  sessions: [
    { id: 'archived', labelKey: 'archivedSessions' },
    { id: 'default-directory', labelKey: 'defaultDirectory' }
  ],
  vault: [
    { id: 'credentials', labelKey: 'vaultCredentials' },
    { id: 'sources', labelKey: 'vaultSources' }
  ],
  about: [
    { id: 'updates', labelKey: 'appUpdates' },
    { id: 'uninstall', labelKey: 'uninstall' }
  ]
}
