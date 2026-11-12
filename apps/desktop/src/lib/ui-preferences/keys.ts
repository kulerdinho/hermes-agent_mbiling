/**
 * Which persisted keys are machine-level UI preferences (#145213).
 *
 * Only these leave localStorage for the machine's shared store, so native
 * Desktop and every browser that opens this machine's Webapp show the same
 * choices. Scoped families keep their scope inside the key name (a
 * `….profile.<name>` or per-connection suffix): only the location changes.
 *
 * Deliberately NOT here (they stay in this browser/window):
 * - credentials, grants and connection config: gateway registry, custom model
 *   endpoints, plugin decisions and preview-script grants, embed permissions;
 * - per-window or per-device runtime state: layout tree, open tiles and tabs,
 *   last route/session, scroll, zoom, pet and popout geometry, keep-awake;
 * - drafts, queues, journals and caches: composer queue, in-flight turns,
 *   pending/prepared submissions, previews, pricing, backend skin cache, the
 *   "provider configured" onboarding cache (each backend answers that itself);
 * - one-off notices: butterbar, update and real-profile prompts.
 */

const SHARED_KEYS: ReadonlySet<string> = new Set([
  // Appearance.
  'mbiling-desktop-theme-v2',
  'mbiling-desktop-mode-v1',
  'mbiling-desktop-profile-themes-v1',
  'mbiling-desktop-profile-modes-v1',
  'mbiling-desktop-user-themes-v1',
  'mbiling.desktop.backdrop.v1',
  'mbiling.desktop.chat-text-scale.v1',
  'mbiling.desktop.interfaceMode.v1',
  'mbiling.desktop.textDirection',
  'mbiling.desktop.translucency.v2',
  'mbiling.desktop.user-bubble-transparency.v1',
  // Sessions sidebar and profile rail.
  'mbiling.desktop.agentsGroupedByWorkspace',
  'mbiling.desktop.profileColors',
  'mbiling.desktop.profileOrder',
  'mbiling.desktop.profileRailVisible',
  'mbiling.desktop.sessionListDensity',
  'mbiling.desktop.sessionOrder',
  'mbiling.desktop.sessionOrder.manual',
  'mbiling.desktop.showAllProfiles',
  'mbiling.desktop.sidebarAgentsGrouped.allProfiles',
  'mbiling.desktop.sidebarCardRows',
  'mbiling.desktop.sidebarGrouping',
  'mbiling.desktop.sidebarGrouping.allProfiles',
  'mbiling.desktop.sidebarRowMeta',
  'mbiling.desktop.sidebarShowAllSessions',
  'mbiling.desktop.sidebarShowArchived',
  'mbiling.desktop.sidebarSortKey',
  // Status bar.
  'mbiling.desktop.statusbarHidden.v2',
  'mbiling.desktop.statusbarVisible.v2',
  // Transcript and tools.
  'mbiling.desktop.hideThreadTimeline',
  'mbiling.desktop.reasoning.collapsedByDefault',
  'mbiling.desktop.toolView.hideCodeDiffs',
  'mbiling.desktop.toolView.technical',
  // Shell, panes and behaviour.
  'mbiling.desktop.alwaysExternalLinks.v1',
  'mbiling.desktop.capabilities.catalogCardView',
  'mbiling.desktop.composerPopout.enabled',
  'mbiling.desktop.composerPopout.gesturesEnabled',
  'mbiling.desktop.files.showIgnored',
  'mbiling.desktop.keybinds',
  'mbiling.desktop.layoutPresets.v2',
  'mbiling.desktop.panesFlipped',
  'mbiling.desktop.tabStripDefault',
  'mbiling.desktop.titlebarAppActions',
  // Guidance: what the user already skipped, saw or dismissed does not come
  // back in every new browser or origin (#145600).
  'mbiling-onboarding-skipped-v1',
  'mbiling.desktop.tips.last.v1',
  'mbiling.desktop.tips.next.v1',
  'mbiling.desktop.tips.retired.v1',
  'mbiling.desktop.tips.rotation.v1',
  'mbiling.desktop.tips.shownAt.v1',
  'mbiling.desktop.tours.v1',
  // Sound and media.
  'mbiling.desktop.autoSpeakReplies',
  'mbiling.desktop.completionSoundVariantId',
  'mbiling.desktop.hapticsMuted',
  'mbiling.desktop.videoPlaybackSpeed',
  // Model picker.
  'mbiling.desktop.collapsed-providers',
  'mbiling.desktop.favorite-models',
  // visible-models is a diff against known-models: they only make sense together.
  'mbiling.desktop.known-models',
  'mbiling.desktop.model-presets',
  'mbiling.desktop.visible-models'
])

/** Families whose scope rides in the key suffix (gateway aliases/order/collapsed). */
const SHARED_PREFIXES: readonly string[] = ['mbiling.desktop.sidebar.gatewayGroups.v1.']

export function isSharedPreferenceKey(key: string): boolean {
  return SHARED_KEYS.has(key) || SHARED_PREFIXES.some(prefix => key.startsWith(prefix))
}
