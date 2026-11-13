export function logError(error: unknown): void {
  if (!process.env.MBILING_INK_DEBUG_ERRORS) {
    return
  }

  console.error(error)
}
