import { contextBridge, ipcRenderer, webFrame, webUtils } from 'electron'

import type { DesktopProfileRoute } from './desktop-profile'
import type { HudModifierApi, HudModifierStatus } from './hud-modifier-types'
import { customWindowControlsEnabled } from './window-controls'

// Which translucency the OS can back. Asked synchronously because the renderer
// needs it before its first paint, and answered by main because deciding it
// needs `os.release()` — a sandboxed preload may only require electron, events,
// timers and url, so importing node:os here throws before contextBridge runs
// and takes the ENTIRE bridge down with it (window.mbilingDesktop undefined =>
// "Desktop IPC bridge is unavailable"). No reply means no glass, which degrades
// to an ordinary opaque window rather than a page thinned over nothing.
const translucencySupport = ipcRenderer.sendSync('mbiling:translucency:support')
const hudWindowing = ipcRenderer.sendSync('mbiling:hud:windowing')
const hudNativeDrag = hudWindowing?.nativeDrag === true

const launchFlags: { localModels?: boolean; assumeFreshInstall?: boolean } | undefined =
  ipcRenderer.sendSync('mbiling:feature-flags')

// Local, sanitized skin payload for the first renderer theme paint. This does
// not wait on `gateway.ready`, so an unreachable remote primary cannot force
// the built-in palette over the skin configured on this machine.
const localSkin = ipcRenderer.sendSync('mbiling:skin:local')

// This machine's UI preferences store (#145213), read before the first paint so
// a preference set in another window or in this machine's Webapp paints at once.
const uiPreferencesSnapshot = ipcRenderer.sendSync('mbiling:ui-preferences:snapshot')

import { unwrapExpectedNotFound } from './api-expected-404'

contextBridge.exposeInMainWorld('mbilingDesktop', {
  glassSupported: translucencySupport?.glass === true,
  translucencySupported: translucencySupport?.translucency === true,
  // Launch-flag fact: the app was started with --local, so the renderer may
  // show the local-models surfaces. Static for the window's lifetime.
  localModelsEnabled: launchFlags?.localModels === true,
  // Dev-only launch fact: an unpackaged app started with --fresh-install, so
  // onboarding treats this launch as a fresh install. Always false when packaged.
  assumeFreshInstall: launchFlags?.assumeFreshInstall === true,
  localSkin: localSkin && typeof localSkin === 'object' ? localSkin : null,
  uiPreferences: {
    snapshot: uiPreferencesSnapshot && typeof uiPreferencesSnapshot === 'object' ? uiPreferencesSnapshot : null,
    read: () => ipcRenderer.invoke('mbiling:ui-preferences:read'),
    write: payload => ipcRenderer.invoke('mbiling:ui-preferences:write', payload),
    onChanged: callback => {
      const listener = (_event, snapshot) => callback(snapshot)
      ipcRenderer.on('mbiling:ui-preferences:changed', listener)

      return () => ipcRenderer.removeListener('mbiling:ui-preferences:changed', listener)
    }
  },
  getConnection: (profile, opts) => ipcRenderer.invoke('mbiling:connection', profile, opts),
  // Loopback origin that hosts YouTube's player for the file:// renderer.
  getEmbedHostOrigin: () => ipcRenderer.invoke('mbiling:embed-host:origin'),
  // Registry-scoped backend resolution: { connectionId, profile } → descriptor.
  getConnectionFor: payload => ipcRenderer.invoke('mbiling:connection:for', payload),
  getProfileRoutes: profiles => ipcRenderer.invoke('mbiling:plugin-profile-routes', profiles),
  revalidateConnection: () => ipcRenderer.invoke('mbiling:connection:revalidate'),
  getGatewayWsUrl: profile => ipcRenderer.invoke('mbiling:gateway:ws-url', profile),
  // Registry-scoped fresh WS URL: { connectionId, profile } → result shape of
  // getGatewayWsUrl, minted against that connection's backend.
  getGatewayWsUrlFor: payload => ipcRenderer.invoke('mbiling:gateway:ws-url-for', payload),
  // { connectionId, profile, pluginId, path } → one-use local plugin socket URL, null, or { refused: 'permission' }.
  getPluginWsUrl: payload => ipcRenderer.invoke('mbiling:plugin:ws-url', payload),
  // Union agent roster across every registered connection.
  getAgentRoster: () => ipcRenderer.invoke('mbiling:agents:roster'),
  openSessionWindow: (sessionId, opts) => ipcRenderer.invoke('mbiling:window:openSession', sessionId, opts),
  openSessionInTerminal: (sessionId, opts) => ipcRenderer.invoke('mbiling:window:openInTerminal', sessionId, opts),
  openWindow: (options?: DesktopProfileRoute) => ipcRenderer.invoke('mbiling:window:openInstance', options),
  openBrowserWindow: tabId => ipcRenderer.invoke('mbiling:window:openBrowser', tabId),
  windowRelay: {
    send: payload => ipcRenderer.send('mbiling:window:relay', payload),
    onMessage: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('mbiling:window:relay', listener)

      return () => ipcRenderer.removeListener('mbiling:window:relay', listener)
    }
  },
  onBrowserPopoutClosed: callback => {
    const listener = (_event, tabId) => callback(tabId)
    ipcRenderer.on('mbiling:browser-popout:closed', listener)

    return () => ipcRenderer.removeListener('mbiling:browser-popout:closed', listener)
  },
  claimAmbientCue: key => ipcRenderer.invoke('mbiling:ambient:claim', key),
  windowControls: {
    custom: customWindowControlsEnabled(),
    minimize: () => ipcRenderer.send('mbiling:window-control', 'minimize'),
    toggleMaximize: () => ipcRenderer.send('mbiling:window-control', 'toggle-maximize'),
    close: () => ipcRenderer.send('mbiling:window-control', 'close')
  },
  wakeIndicator: {
    getState: () => ipcRenderer.invoke('mbiling:wake-indicator:get'),
    setState: state => ipcRenderer.send('mbiling:wake-indicator:set', state),
    onState: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('mbiling:wake-indicator:state', listener)

      return () => ipcRenderer.removeListener('mbiling:wake-indicator:state', listener)
    }
  },
  chatOnboarding: {
    size: mode => ipcRenderer.send('mbiling:window:size', mode)
  },
  petOverlay: {
    // Main renderer → main process: window lifecycle + drag. `request` is
    // `{ bounds, screen }`; resolves with the screen bounds it actually used.
    open: request => ipcRenderer.invoke('mbiling:pet-overlay:open', request),
    close: () => ipcRenderer.invoke('mbiling:pet-overlay:close'),
    setBounds: bounds => ipcRenderer.send('mbiling:pet-overlay:set-bounds', bounds),
    setIgnoreMouse: ignore => ipcRenderer.send('mbiling:pet-overlay:ignore-mouse', ignore),
    // Flip the overlay focusable (and focus it) while the composer needs keys.
    setFocusable: focusable => ipcRenderer.send('mbiling:pet-overlay:set-focusable', focusable),
    // Main renderer → overlay (forwarded by main): push the latest pet state.
    pushState: payload => ipcRenderer.send('mbiling:pet-overlay:state', payload),
    // Overlay → main renderer (forwarded by main): pop back in / composer submit.
    control: payload => ipcRenderer.send('mbiling:pet-overlay:control', payload),
    // Overlay subscribes to state pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('mbiling:pet-overlay:state', listener)

      return () => ipcRenderer.removeListener('mbiling:pet-overlay:state', listener)
    },
    // Main renderer subscribes to overlay control messages.
    onControl: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('mbiling:pet-overlay:control', listener)

      return () => ipcRenderer.removeListener('mbiling:pet-overlay:control', listener)
    }
  },
  // HUD mode: the chrome-free floating chat. A full app renderer (own gateway)
  // sized as a floating bar, so it mounts the real composer. Main owns the
  // window; `onChanged` keeps every window's toggle truthful.
  hud: {
    nativeDrag: hudNativeDrag,
    windowing: {
      clientPlacement: hudWindowing?.clientPlacement !== false,
      controlDrag: hudWindowing?.controlDrag === true,
      nativeDrag: hudNativeDrag,
      solid: hudWindowing?.solid === true,
      workspaceTransfer: hudWindowing?.workspaceTransfer === true
    },
    open: request => ipcRenderer.invoke('mbiling:hud:open', request),
    close: () => ipcRenderer.invoke('mbiling:hud:close'),
    setIgnoreMouse: ignore => ipcRenderer.send('mbiling:hud:ignore-mouse', ignore),
    beginMove: () => ipcRenderer.send('mbiling:hud:begin-move'),
    endMove: () => ipcRenderer.send('mbiling:hud:end-move'),
    moveBy: delta => ipcRenderer.send('mbiling:hud:move-by', delta),
    setWorkspaceTransfer: transferring => ipcRenderer.send('mbiling:hud:workspace-transfer', transferring),
    setBounds: bounds => ipcRenderer.send('mbiling:hud:set-bounds', bounds),
    resetLayout: () => ipcRenderer.invoke('mbiling:hud:reset-layout'),
    // Whether the band covers the window below the bar. Main pairs it with the
    // user's translucency setting to decide the native frost (macOS vibrancy /
    // Windows 11 DWM backdrop) — see hudFrostFor.
    setFrost: showing => ipcRenderer.invoke('mbiling:hud:frost', showing),
    // The HUD tells main which session it is on; main hands that back to the
    // app window when the HUD closes, so the app can re-home onto it.
    setSession: sessionId => ipcRenderer.send('mbiling:hud:session', sessionId),
    onGoto: callback => {
      const listener = (_event, sessionId) => callback(sessionId)
      ipcRenderer.on('mbiling:hud:goto', listener)

      return () => ipcRenderer.removeListener('mbiling:hud:goto', listener)
    },
    onChanged: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('mbiling:hud:changed', listener)

      return () => ipcRenderer.removeListener('mbiling:hud:changed', listener)
    },
    // Linux only, and silent elsewhere: where the cursor is, in page
    // coordinates, or null when it has left the window. Stands in for the
    // mousemove that `setIgnoreMouseEvents(true, { forward: true })` delivers on
    // macOS and Windows but not here.
    onCursor: callback => {
      const listener = (_event, point) => callback(point)
      ipcRenderer.on('mbiling:hud:cursor', listener)

      return () => ipcRenderer.removeListener('mbiling:hud:cursor', listener)
    },
    // Main's game-overlay watch: whether a fullscreen app (a game) is under
    // the HUD, so the renderer can step back to the low-opacity overlay
    // treatment while one owns the screen.
    onGameOverlay: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('mbiling:hud:game-overlay', listener)

      return () => ipcRenderer.removeListener('mbiling:hud:game-overlay', listener)
    }
  },
  hudModifier: {
    getSettings: () => ipcRenderer.invoke('mbiling:hud-modifier:settings:get'),
    setEnabled: enabled => ipcRenderer.invoke('mbiling:hud-modifier:settings:set', enabled),
    openPermissionSettings: () => ipcRenderer.invoke('mbiling:hud-modifier:permission'),
    onStatus: callback => {
      const listener = (_event: Electron.IpcRendererEvent, status: HudModifierStatus) => callback(status)
      ipcRenderer.on('mbiling:hud-modifier:status', listener)

      return () => ipcRenderer.removeListener('mbiling:hud-modifier:status', listener)
    }
  } satisfies HudModifierApi,
  // macOS native screenshot gesture; captures require a main-issued request.
  screenshot:
    process.platform === 'darwin'
      ? {
          getSettings: () => ipcRenderer.invoke('mbiling:screenshot:settings:get'),
          setEnabled: enabled => ipcRenderer.invoke('mbiling:screenshot:settings:set', enabled),
          openPermissionSettings: kind => ipcRenderer.invoke('mbiling:screenshot:permission', kind),
          capture: requestId => ipcRenderer.invoke('mbiling:screenshot:capture', requestId),
          onStatus: callback => {
            const listener = (_event, status) => callback(status)
            ipcRenderer.on('mbiling:screenshot:status', listener)

            return () => ipcRenderer.removeListener('mbiling:screenshot:status', listener)
          },
          onRequest: callback => {
            const channel = 'mbiling:screenshot:request'
            const listener = (_event, requestId) => callback(requestId)

            if (ipcRenderer.listenerCount(channel) === 0) {
              ipcRenderer.send('mbiling:screenshot:subscribe', true)
            }

            ipcRenderer.on(channel, listener)

            return () => {
              ipcRenderer.removeListener(channel, listener)

              if (ipcRenderer.listenerCount(channel) === 0) {
                ipcRenderer.send('mbiling:screenshot:subscribe', false)
              }
            }
          }
        }
      : undefined,
  // Quick Entry: the global-hotkey mini composer window. Main owns the OS
  // shortcut + the persisted preference; the quick window only captures text
  // and hands it back, and the primary renderer submits it through the normal
  // prompt path.
  quickEntry: {
    getSettings: () => ipcRenderer.invoke('mbiling:quick-entry:settings:get'),
    setSettings: patch => ipcRenderer.invoke('mbiling:quick-entry:settings:set', patch),
    // Invoke returns the delivery result so the draft is not lost (#85590).
    submit: payload => ipcRenderer.invoke('mbiling:quick-entry:submit', payload),
    // Main cannot invoke the primary renderer, so it receives this ack (#85590).
    ackSubmit: (correlationId, result) => ipcRenderer.send('mbiling:quick-entry:ack', { correlationId, result }),
    dismiss: () => ipcRenderer.send('mbiling:quick-entry:dismiss'),
    // Primary renderer → main → quick window: gateway connection state + the
    // recent-session options the target picker offers. Main caches the latest
    // payload so a freshly spawned quick window starts from truth.
    pushState: payload => ipcRenderer.send('mbiling:quick-entry:state', payload),
    // Quick window subscribes to those pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('mbiling:quick-entry:state', listener)

      return () => ipcRenderer.removeListener('mbiling:quick-entry:state', listener)
    },
    // Main → primary renderer: a submit captured by the quick window.
    onSubmit: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('mbiling:quick-entry:submit', listener)

      return () => ipcRenderer.removeListener('mbiling:quick-entry:submit', listener)
    },
    // Main → quick window: you were just summoned (reset draft + refocus).
    onShown: callback => {
      const listener = () => callback()
      ipcRenderer.on('mbiling:quick-entry:shown', listener)

      return () => ipcRenderer.removeListener('mbiling:quick-entry:shown', listener)
    },
    // Main → quick window: the outcome of a submit whose relay already timed
    // out. Delivery is now KNOWN — reconcile the unknown state instead of
    // leaving the user to resend a prompt that may already be delivered.
    onLateResult: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('mbiling:quick-entry:late-result', listener)

      return () => ipcRenderer.removeListener('mbiling:quick-entry:late-result', listener)
    }
  },
  getBootProgress: () => ipcRenderer.invoke('mbiling:boot-progress:get'),
  getConnectionConfig: profile => ipcRenderer.invoke('mbiling:connection-config:get', profile),
  saveConnectionConfig: payload => ipcRenderer.invoke('mbiling:connection-config:save', payload),
  applyConnectionConfig: payload => ipcRenderer.invoke('mbiling:connection-config:apply', payload),
  testConnectionConfig: payload => ipcRenderer.invoke('mbiling:connection-config:test', payload),
  // Opt-in OS-keychain encryption for stored gateway secrets (default off —
  // see secret-storage-policy.ts). get never touches the OS keychain.
  getSecretStorageEncryption: () => ipcRenderer.invoke('mbiling:secret-storage:get'),
  setSecretStorageEncryption: (on: boolean) => ipcRenderer.invoke('mbiling:secret-storage:set', on),
  // v2 multi-connection registry: named agent sources (local / remote / cloud / ssh).
  connections: {
    list: () => ipcRenderer.invoke('mbiling:connections:list'),
    save: payload => ipcRenderer.invoke('mbiling:connections:save', payload),
    remove: id => ipcRenderer.invoke('mbiling:connections:remove', id),
    setPrimary: id => ipcRenderer.invoke('mbiling:connections:set-primary', id),
    setLaunchMode: mode => ipcRenderer.invoke('mbiling:connections:set-launch-mode', mode),
    setLastUsed: id => ipcRenderer.invoke('mbiling:connections:set-last-used', id),
    test: id => ipcRenderer.invoke('mbiling:connections:test', id),
    updateManaged: id => ipcRenderer.invoke('mbiling:connections:update-managed', id),
    // Fan out `mbiling update` to every eligible registered connection.
    // Optional excludeIds skips rows the caller updates through another path.
    updateAll: options => ipcRenderer.invoke('mbiling:connections:update-all', options),
    // Registry lifecycle push (main → renderer): a connection was removed or
    // materially edited, so secondaries scoped to it must be disposed (and,
    // for edits, re-dialed at the new target).
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('mbiling:connections:changed', listener)

      return () => ipcRenderer.removeListener('mbiling:connections:changed', listener)
    }
  },
  sshConfigHosts: () => ipcRenderer.invoke('mbiling:ssh-config:hosts'),
  sshResolveHost: host => ipcRenderer.invoke('mbiling:ssh-config:resolve', host),
  probeConnectionConfig: remoteUrl => ipcRenderer.invoke('mbiling:connection-config:probe', remoteUrl),
  // `options` lets a registry-editor draft sign in BEFORE it is saved: the
  // main process settles the draft's connection id up front so the login
  // window writes into the per-connection cookie jar the saved entry will
  // read (not the legacy shared jar an unsaved URL would fall back to).
  oauthLoginConnectionConfig: (remoteUrl, options) =>
    ipcRenderer.invoke('mbiling:connection-config:oauth-login', remoteUrl, options),
  oauthLogoutConnectionConfig: remoteUrl => ipcRenderer.invoke('mbiling:connection-config:oauth-logout', remoteUrl),
  // Mbiling Cloud: one portal login powers discovery + silent per-agent sign-in
  // (cloud-auto-discovery Phase 3).
  cloud: {
    status: () => ipcRenderer.invoke('mbiling:cloud:status'),
    login: () => ipcRenderer.invoke('mbiling:cloud:login'),
    logout: () => ipcRenderer.invoke('mbiling:cloud:logout'),
    discover: org => ipcRenderer.invoke('mbiling:cloud:discover', org),
    agentSignIn: dashboardUrl => ipcRenderer.invoke('mbiling:cloud:agent-sign-in', dashboardUrl)
  },
  profile: {
    getDefault: () => ipcRenderer.invoke('mbiling:profile:default:get'),
    setDefault: (route: DesktopProfileRoute) => ipcRenderer.invoke('mbiling:profile:default:set', route),
    onDefaultChanged: (callback: (route: DesktopProfileRoute | null) => void) => {
      const listener = (_event: Electron.IpcRendererEvent, route: DesktopProfileRoute | null) => callback(route)
      ipcRenderer.on('mbiling:profile:default:changed', listener)

      return () => ipcRenderer.removeListener('mbiling:profile:default:changed', listener)
    },
    get: () => ipcRenderer.invoke('mbiling:profile:get'),
    remember: name => ipcRenderer.invoke('mbiling:profile:remember', name),
    importConnections: (record: Record<string, string>) =>
      ipcRenderer.invoke('mbiling:profile:connections:import', record),
    rememberConnection: (connectionId: string, profile: string) =>
      ipcRenderer.invoke('mbiling:profile:connections:remember', connectionId, profile),
    set: name => ipcRenderer.invoke('mbiling:profile:set', name)
  },
  // The handler resolves an expected 404 with a sentinel instead of rejecting
  // (Electron logs a stack for every rejected invoke). Turn it back into the
  // rejection the renderer expects — see electron/api-expected-404.ts.
  api: request => ipcRenderer.invoke('mbiling:api', request).then(unwrapExpectedNotFound),
  notify: payload => ipcRenderer.invoke('mbiling:notify', payload),
  claimStartupLatency: () => ipcRenderer.invoke('mbiling:startup-latency:claim'),
  requestMicrophoneAccess: () => ipcRenderer.invoke('mbiling:requestMicrophoneAccess'),
  readWindowBelow: () => ipcRenderer.invoke('mbiling:window:readBelow'),
  readFileDataUrl: filePath => ipcRenderer.invoke('mbiling:readFileDataUrl', filePath),
  readFileDataUrlForAttach: filePath => ipcRenderer.invoke('mbiling:readFileDataUrlForAttach', filePath),
  dataUrlReadMax: {
    get: () => ipcRenderer.invoke('mbiling:data-url-read-max:get'),
    set: maxMb => ipcRenderer.invoke('mbiling:data-url-read-max:set', maxMb)
  },
  readFileText: filePath => ipcRenderer.invoke('mbiling:readFileText', filePath),
  readPluginSource: (filePath: string) => ipcRenderer.invoke('mbiling:readPluginSource', filePath),
  selectPaths: options => ipcRenderer.invoke('mbiling:selectPaths', options),
  selectSavePath: options => ipcRenderer.invoke('mbiling:selectSavePath', options),
  saveTextFileAs: request => ipcRenderer.invoke('mbiling:saveTextFileAs', request),
  writeClipboard: text => ipcRenderer.invoke('mbiling:writeClipboard', text),
  readClipboard: () => ipcRenderer.invoke('mbiling:readClipboard'),
  saveGatewayFile: payload => ipcRenderer.invoke('mbiling:saveGatewayFile', payload),
  saveImageFromUrl: url => ipcRenderer.invoke('mbiling:saveImageFromUrl', url),
  contextMenuEdit: command => ipcRenderer.invoke('mbiling:context-menu:edit', command),
  contextMenuCopyImage: () => ipcRenderer.invoke('mbiling:context-menu:copy-image'),
  contextMenuSpellcheck: action => ipcRenderer.invoke('mbiling:context-menu:spellcheck', action),
  contextMenuGuestAddWord: payload => ipcRenderer.invoke('mbiling:context-menu:guest-add-word', payload),
  spellcheckDictionary: (op, word) => ipcRenderer.invoke('mbiling:spellcheck:dictionary', { op, word }),
  onContextMenuSpellcheck: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('mbiling:context-menu-spellcheck', listener)

    return () => ipcRenderer.removeListener('mbiling:context-menu-spellcheck', listener)
  },
  saveImageBuffer: (data, ext, name) => ipcRenderer.invoke('mbiling:saveImageBuffer', { data, ext, name }),
  capturePreview: payload => ipcRenderer.invoke('mbiling:capturePreview', payload),
  savePastedText: text => ipcRenderer.invoke('mbiling:savePastedText', { text }),
  saveClipboardImage: () => ipcRenderer.invoke('mbiling:saveClipboardImage'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  normalizePreviewTarget: (target, baseDir) => ipcRenderer.invoke('mbiling:normalizePreviewTarget', target, baseDir),
  watchPreviewFile: url => ipcRenderer.invoke('mbiling:watchPreviewFile', url),
  watchDirectory: dir => ipcRenderer.invoke('mbiling:watchDirectory', dir),
  stopPreviewFileWatch: id => ipcRenderer.invoke('mbiling:stopPreviewFileWatch', id),
  setActiveWork: payload => ipcRenderer.send('mbiling:active-work', payload),
  scheduleStreamWorkWake: (token, delayMs) => ipcRenderer.send('mbiling:stream-work-wake', { token, delayMs }),
  onStreamWorkWake: callback => {
    const listener = (_event, token) => callback(token)
    ipcRenderer.on('mbiling:stream-work-ready', listener)

    return () => ipcRenderer.removeListener('mbiling:stream-work-ready', listener)
  },
  setTitleBarTheme: payload => ipcRenderer.send('mbiling:titlebar-theme', payload),
  setNativeTheme: mode => ipcRenderer.send('mbiling:native-theme', mode),
  setTranslucency: payload => ipcRenderer.send('mbiling:translucency', payload),
  setKeepAwake: mode => ipcRenderer.send('mbiling:keep-awake', mode),
  // Once per computer, after a timed sleep interrupted a turn with keep-awake off.
  onKeepAwakeOffer: callback => {
    const listener = () => callback()
    ipcRenderer.on('mbiling:keep-awake-offer', listener)

    return () => ipcRenderer.removeListener('mbiling:keep-awake-offer', listener)
  },
  minimizeToTray: {
    get: () => ipcRenderer.invoke('mbiling:minimize-to-tray:get'),
    set: on => ipcRenderer.invoke('mbiling:minimize-to-tray:set', on),
    onChanged: callback => {
      const listener = (_event, status) => callback(status)
      ipcRenderer.on('mbiling:minimize-to-tray:changed', listener)

      return () => ipcRenderer.removeListener('mbiling:minimize-to-tray:changed', listener)
    }
  },
  setDisableF12: blocked => ipcRenderer.send('mbiling:devtools:disable-f12', blocked),
  setF12ShortcutActive: active => ipcRenderer.send('mbiling:f12ShortcutActive', Boolean(active)),
  onF12Shortcut: callback => {
    const listener = (_event, input) => callback(input)
    ipcRenderer.on('mbiling:f12-shortcut', listener)

    return () => ipcRenderer.removeListener('mbiling:f12-shortcut', listener)
  },
  setPreviewShortcutActive: active => ipcRenderer.send('mbiling:previewShortcutActive', Boolean(active)),
  setPreviewGuestHidden: (webContentsId, hidden) =>
    ipcRenderer.send('mbiling:preview-guest-hidden', { webContentsId, hidden: Boolean(hidden) }),
  beginPreviewGuestActivity: webContentsId => ipcRenderer.invoke('mbiling:preview-guest-activity:begin', webContentsId),
  endPreviewGuestActivity: token => ipcRenderer.invoke('mbiling:preview-guest-activity:end', token),
  executePreviewGuestScript: (token, code) => ipcRenderer.invoke('mbiling:preview-guest-activity:script', token, code),
  openExternal: url => ipcRenderer.invoke('mbiling:openExternal', url),
  freeTierChallenge: {
    // Load the account service's challenge page in a hidden window (revealed
    // only if the page asks for the human). Resolves with how it ended.
    run: request => ipcRenderer.invoke('mbiling:freeTierChallenge:run', request)
  },
  mcpOauth: {
    // One-shot loopback listener for MCP OAuth against remote backends: bind
    // on this machine, hand redirectUri to mcp.servers.oauth.start, then wait
    // for the provider redirect and relay code/state via oauth.callback.
    listen: () => ipcRenderer.invoke('mbiling:mcp-oauth:listen'),
    wait: (id, timeoutMs) => ipcRenderer.invoke('mbiling:mcp-oauth:wait', id, timeoutMs),
    cancel: id => ipcRenderer.invoke('mbiling:mcp-oauth:cancel', id)
  },
  openPreviewInBrowser: url => ipcRenderer.invoke('mbiling:openPreviewInBrowser', url),
  reachPreviewUrl: url => ipcRenderer.invoke('mbiling:preview:reach', url),
  setActiveConnectionRoute: route => ipcRenderer.send('mbiling:connection:active-route', route),
  fetchLinkTitle: url => ipcRenderer.invoke('mbiling:fetchLinkTitle', url),
  resolveFavicon: url => ipcRenderer.invoke('mbiling:resolveFavicon', url),
  sanitizeWorkspaceCwd: cwd => ipcRenderer.invoke('mbiling:workspace:sanitize', cwd),
  settings: {
    getDefaultProjectDir: () => ipcRenderer.invoke('mbiling:setting:defaultProjectDir:get'),
    setDefaultProjectDir: dir => ipcRenderer.invoke('mbiling:setting:defaultProjectDir:set', dir),
    pickDefaultProjectDir: () => ipcRenderer.invoke('mbiling:setting:defaultProjectDir:pick')
  },
  preparedSubmissions: {
    read: () => ipcRenderer.invoke('mbiling:prepared-submissions:read'),
    update: (key, entry) => ipcRenderer.invoke('mbiling:prepared-submissions:update', key, entry),
    compareAndSet: (key, expected, entry) =>
      ipcRenderer.invoke('mbiling:prepared-submissions:compare-and-set', key, expected, entry)
  },
  zoom: {
    // Current zoom of this window, as { level, percent }.
    get: () => ipcRenderer.invoke('mbiling:zoom:get'),
    // Synchronous zoom factor (1 = 100%). Coordinate math needs it in the
    // same tick as the event it converts, so no IPC round-trip here.
    factor: () => webFrame.getZoomFactor(),
    setPercent: percent => ipcRenderer.send('mbiling:zoom:set-percent', percent),
    // Fires on every zoom change, including the Ctrl/Cmd +/-/0 shortcuts,
    // so the settings UI can stay in sync with the keyboard.
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('mbiling:zoom:changed', listener)

      return () => ipcRenderer.removeListener('mbiling:zoom:changed', listener)
    }
  },
  revealLogs: () => ipcRenderer.invoke('mbiling:logs:reveal'),
  getRecentLogs: () => ipcRenderer.invoke('mbiling:logs:recent'),
  // Fire-and-forget: persists a renderer error-boundary catch (with component
  // stack) to desktop.log so crashes survive the window (#79428).
  reportRendererError: report => ipcRenderer.send('mbiling:logs:renderer-error', report),
  logLine: (line: string): void => ipcRenderer.send('mbiling:logs:renderer-line', line),
  readDir: dirPath => ipcRenderer.invoke('mbiling:fs:readDir', dirPath),
  gitRoot: startPath => ipcRenderer.invoke('mbiling:fs:gitRoot', startPath),
  revealPath: targetPath => ipcRenderer.invoke('mbiling:fs:reveal', targetPath),
  openDir: dirPath => ipcRenderer.invoke('mbiling:fs:openDir', dirPath),
  desktopPluginsRoot: () => ipcRenderer.invoke('mbiling:fs:desktopPluginsRoot'),
  reconcileDesktopPlugins: async () => (await ipcRenderer.invoke('mbiling:fs:reconcileDesktopPlugins')).published,
  reconcileDesktopPluginsReport: () => ipcRenderer.invoke('mbiling:fs:reconcileDesktopPlugins'),
  logsRoot: (profile?: string) => ipcRenderer.invoke('mbiling:fs:logsRoot', profile),
  renamePath: (targetPath, newName) => ipcRenderer.invoke('mbiling:fs:rename', targetPath, newName),
  writeTextFile: (filePath, content) => ipcRenderer.invoke('mbiling:fs:writeText', filePath, content),
  trashPath: targetPath => ipcRenderer.invoke('mbiling:fs:trash', targetPath),
  pluginFiles: {
    request: (pluginId, labels) => ipcRenderer.invoke('mbiling:pluginFiles:request', pluginId, labels),
    grants: pluginId => ipcRenderer.invoke('mbiling:pluginFiles:grants', pluginId),
    revoke: (pluginId, folderId) => ipcRenderer.invoke('mbiling:pluginFiles:revoke', pluginId, folderId),
    op: (pluginId, request) => ipcRenderer.invoke('mbiling:pluginFiles:op', pluginId, request),
    onChanged: callback => {
      const listener = (_event, change) => callback(change)
      ipcRenderer.on('mbiling:pluginFiles:changed', listener)

      return () => ipcRenderer.removeListener('mbiling:pluginFiles:changed', listener)
    }
  },
  git: {
    worktreeList: repoPath => ipcRenderer.invoke('mbiling:git:worktreeList', repoPath),
    worktreeAdd: (repoPath, options) => ipcRenderer.invoke('mbiling:git:worktreeAdd', repoPath, options),
    worktreeRemove: (repoPath, worktreePath, options) =>
      ipcRenderer.invoke('mbiling:git:worktreeRemove', repoPath, worktreePath, options),
    branchSwitch: (repoPath, branch) => ipcRenderer.invoke('mbiling:git:branchSwitch', repoPath, branch),
    branchList: repoPath => ipcRenderer.invoke('mbiling:git:branchList', repoPath),
    baseBranchList: repoPath => ipcRenderer.invoke('mbiling:git:baseBranchList', repoPath),
    repoStatus: repoPath => ipcRenderer.invoke('mbiling:git:repoStatus', repoPath),
    fileDiff: (repoPath, filePath) => ipcRenderer.invoke('mbiling:git:fileDiff', repoPath, filePath),
    scanRepos: (roots, options) => ipcRenderer.invoke('mbiling:git:scanRepos', roots, options),
    review: {
      list: (repoPath, scope, baseRef) => ipcRenderer.invoke('mbiling:git:review:list', repoPath, scope, baseRef),
      diff: (repoPath, filePath, scope, baseRef, staged) =>
        ipcRenderer.invoke('mbiling:git:review:diff', repoPath, filePath, scope, baseRef, staged),
      stage: (repoPath, filePath) => ipcRenderer.invoke('mbiling:git:review:stage', repoPath, filePath),
      unstage: (repoPath, filePath) => ipcRenderer.invoke('mbiling:git:review:unstage', repoPath, filePath),
      revert: (repoPath, filePath) => ipcRenderer.invoke('mbiling:git:review:revert', repoPath, filePath),
      revParse: (repoPath, ref) => ipcRenderer.invoke('mbiling:git:review:revParse', repoPath, ref),
      commit: (repoPath, message, push) => ipcRenderer.invoke('mbiling:git:review:commit', repoPath, message, push),
      commitContext: repoPath => ipcRenderer.invoke('mbiling:git:review:commitContext', repoPath),
      push: repoPath => ipcRenderer.invoke('mbiling:git:review:push', repoPath),
      shipInfo: repoPath => ipcRenderer.invoke('mbiling:git:review:shipInfo', repoPath),
      prList: (repoPath, branches, numbers) =>
        ipcRenderer.invoke('mbiling:git:review:prList', repoPath, branches, numbers),
      createPr: repoPath => ipcRenderer.invoke('mbiling:git:review:createPr', repoPath)
    }
  },
  terminal: {
    attach: id => ipcRenderer.invoke('mbiling:terminal:attach', id),
    cwd: id => ipcRenderer.invoke('mbiling:terminal:cwd', id),
    dispose: id => ipcRenderer.invoke('mbiling:terminal:dispose', id),
    resize: (id, size) => ipcRenderer.invoke('mbiling:terminal:resize', id, size),
    start: options => ipcRenderer.invoke('mbiling:terminal:start', options),
    write: (id, data) => ipcRenderer.invoke('mbiling:terminal:write', id, data),
    onData: (id, callback) => {
      const channel = `mbiling:terminal:${id}:data`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    },
    onExit: (id, callback) => {
      const channel = `mbiling:terminal:${id}:exit`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    }
  },
  onClosePreviewRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('mbiling:close-preview-requested', listener)

    return () => ipcRenderer.removeListener('mbiling:close-preview-requested', listener)
  },
  onPreviewNav: callback => {
    const listener = (_event, command) => callback(command)
    ipcRenderer.on('mbiling:preview-nav', listener)

    return () => ipcRenderer.removeListener('mbiling:preview-nav', listener)
  },
  onOpenFolderRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('mbiling:open-folder-requested', listener)

    return () => ipcRenderer.removeListener('mbiling:open-folder-requested', listener)
  },
  onOpenUpdatesRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('mbiling:open-updates', listener)

    return () => ipcRenderer.removeListener('mbiling:open-updates', listener)
  },
  onDeepLink: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('mbiling:deep-link', listener)

    return () => ipcRenderer.removeListener('mbiling:deep-link', listener)
  },
  signalDeepLinkReady: () => ipcRenderer.invoke('mbiling:deep-link-ready'),
  probePluginRepo: payload => ipcRenderer.invoke('mbiling:plugin:probe', payload),
  installDesktopPlugin: payload => ipcRenderer.invoke('mbiling:plugin:installDesktop', payload),
  removeDesktopPlugin: payload => ipcRenderer.invoke('mbiling:plugin:removeDesktop', payload),
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('mbiling:window-state-changed', listener)

    return () => ipcRenderer.removeListener('mbiling:window-state-changed', listener)
  },
  onFocusSession: callback => {
    const listener = (_event, sessionId) => callback(sessionId)
    ipcRenderer.on('mbiling:focus-session', listener)

    return () => ipcRenderer.removeListener('mbiling:focus-session', listener)
  },
  onNotificationAction: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('mbiling:notification-action', listener)

    return () => ipcRenderer.removeListener('mbiling:notification-action', listener)
  },
  onNotificationActivate: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('mbiling:notification-activate', listener)

    return () => ipcRenderer.removeListener('mbiling:notification-activate', listener)
  },
  onExternalOpenFailed: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('mbiling:external-open-failed', listener)

    return () => ipcRenderer.removeListener('mbiling:external-open-failed', listener)
  },
  onPreviewFileChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('mbiling:preview-file-changed', listener)

    return () => ipcRenderer.removeListener('mbiling:preview-file-changed', listener)
  },
  onBackendExit: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('mbiling:backend-exit', listener)

    return () => ipcRenderer.removeListener('mbiling:backend-exit', listener)
  },
  // Soft gateway-mode apply finished tearing down the primary backend. Renderer
  // should wipe session lists + re-dial without a window reload.
  onConnectionApplied: callback => {
    const listener = () => callback()
    ipcRenderer.on('mbiling:connection:applied', listener)

    return () => ipcRenderer.removeListener('mbiling:connection:applied', listener)
  },
  onPowerResume: callback => {
    const listener = () => callback()
    ipcRenderer.on('mbiling:power-resume', listener)

    return () => ipcRenderer.removeListener('mbiling:power-resume', listener)
  },
  // AC ↔ battery transitions; renderers slow their backstop polls on battery.
  getOnBattery: () => ipcRenderer.invoke('mbiling:power-battery:get'),
  onBatteryChanged: callback => {
    const listener = (_event, onBattery) => callback(Boolean(onBattery))
    ipcRenderer.on('mbiling:power-battery', listener)

    return () => ipcRenderer.removeListener('mbiling:power-battery', listener)
  },
  onBootProgress: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('mbiling:boot-progress', listener)

    return () => ipcRenderer.removeListener('mbiling:boot-progress', listener)
  },
  // First-launch bootstrap progress -- emitted by the install.ps1 stage
  // runner in main.ts (apps/desktop/electron/bootstrap-runner.ts).
  // Renderer's install overlay subscribes to live events and queries the
  // current snapshot via getBootstrapState() to recover after a devtools
  // reload mid-bootstrap.
  getBootstrapState: () => ipcRenderer.invoke('mbiling:bootstrap:get'),
  probeLocalBackend: () => ipcRenderer.invoke('mbiling:local-backend:probe'),
  continueBootstrapLocal: () => ipcRenderer.invoke('mbiling:bootstrap:continue-local'),
  recycleBackend: profile => ipcRenderer.invoke('mbiling:backend:recycle', profile),
  resetBootstrap: () => ipcRenderer.invoke('mbiling:bootstrap:reset'),
  updateHold: {
    recheck: () => ipcRenderer.invoke('mbiling:update-hold:recheck'),
    quit: () => ipcRenderer.invoke('mbiling:update-hold:quit'),
    startAnyway: (request: { holdId: string; confirmed: true }) =>
      ipcRenderer.invoke('mbiling:update-hold:start-anyway', request)
  },
  repairBootstrap: () => ipcRenderer.invoke('mbiling:bootstrap:repair'),
  cancelBootstrap: () => ipcRenderer.invoke('mbiling:bootstrap:cancel'),
  onBootstrapEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('mbiling:bootstrap:event', listener)

    return () => ipcRenderer.removeListener('mbiling:bootstrap:event', listener)
  },
  getVersion: (scope?: { connectionId?: string; profile?: string }) => ipcRenderer.invoke('mbiling:version', scope),
  relaunchApp: () => ipcRenderer.invoke('mbiling:app:relaunch'),
  getMachineProfile: () => ipcRenderer.invoke('mbiling:machine:profile'),
  getRemoteDisplayReason: () => ipcRenderer.invoke('mbiling:get-remote-display-reason'),
  uninstall: {
    summary: () => ipcRenderer.invoke('mbiling:uninstall:summary'),
    run: mode => ipcRenderer.invoke('mbiling:uninstall:run', { mode }),
    openAppsSettings: () => ipcRenderer.invoke('mbiling:uninstall:openAppsSettings')
  },
  updates: {
    check: opts => ipcRenderer.invoke('mbiling:updates:check', opts),
    apply: opts => ipcRenderer.invoke('mbiling:updates:apply', opts),
    setChannel: name => ipcRenderer.invoke('mbiling:updates:channel:set', name),
    onProgress: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('mbiling:updates:progress', listener)

      return () => ipcRenderer.removeListener('mbiling:updates:progress', listener)
    },
    takePendingRun: () => ipcRenderer.invoke('mbiling:updates:metric:take'),
    ackPendingRun: sent => ipcRenderer.invoke('mbiling:updates:metric:ack', sent),
    onPendingRun: callback => {
      const listener = () => callback()
      ipcRenderer.on('mbiling:updates:metric:pending', listener)

      return () => ipcRenderer.removeListener('mbiling:updates:metric:pending', listener)
    }
  },
  desktopMetrics: {
    setEnabled: (on, profile) => ipcRenderer.invoke('mbiling:desktop-metrics:set-enabled', on, profile),
    takeRendererCrashes: () => ipcRenderer.invoke('mbiling:desktop-metrics:crash:take'),
    ackRendererCrashes: sent => ipcRenderer.invoke('mbiling:desktop-metrics:crash:ack', sent)
  },
  themes: {
    fetchMarketplace: id => ipcRenderer.invoke('mbiling:vscode-theme:fetch', id),
    searchMarketplace: query => ipcRenderer.invoke('mbiling:vscode-theme:search', query)
  },
  // Find-in-page (Ctrl/Cmd+F): delegates to Electron's
  // webContents.findInPage on the IPC sender's window so a Cmd+F pressed
  // in a secondary session window searches THAT window, not the primary.
  // `onFoundInPage` returns the unsubscribe fn; the renderer wires it via
  // `initFindInPageListener` in store/find-in-page.ts and tears it down
  // when the FindBar unmounts.
  findInPage: (query, options) => ipcRenderer.invoke('mbiling:find-in-page', query, options),
  stopFindInPage: () => ipcRenderer.invoke('mbiling:stop-find-in-page'),
  onFoundInPage: callback => {
    const listener = (_event, result) => callback(result)
    ipcRenderer.on('mbiling:found-in-page', listener)

    return () => ipcRenderer.removeListener('mbiling:found-in-page', listener)
  },
  // Main-process `before-input-event` forwards Ctrl/Cmd+F here so renderer
  // can open the FindBar even when the GTK compositor has already grabbed
  // the chord at the windowing layer (#81727).
  onOpenFindBarRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('mbiling:open-find-bar', listener)

    return () => ipcRenderer.removeListener('mbiling:open-find-bar', listener)
  }
})
