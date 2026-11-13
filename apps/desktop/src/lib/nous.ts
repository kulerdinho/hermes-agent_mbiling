/**
 * Nous Portal (Nous Research's accounts, inference, billing and cloud agents) is not offered by
 * Mbiling. The engine's own switch is `nous.enabled` in config.yaml; this build-time flag hides the
 * desktop surfaces that are written into the UI rather than driven by the engine's provider list.
 * Set VITE_MBILING_NOUS_ENABLED=1 at build time to show them again.
 */
export const NOUS_ENABLED = import.meta.env.VITE_MBILING_NOUS_ENABLED === '1'
