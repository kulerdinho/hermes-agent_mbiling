import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'

import { BrandMark } from '@/components/brand-mark'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { Loader2 } from '@/lib/icons'

import { RemoteSetupFields } from './fields'
import { useRemoteSetup } from './use-remote-setup'

interface FirstRunRemoteSetupProps {
  onBack: () => void
  /** False for the remote-only client: there is no other setup to go back to. */
  canGoBack?: boolean
}

export function FirstRunRemoteSetup({ onBack, canGoBack = true }: FirstRunRemoteSetupProps): ReactElement {
  const { t } = useI18n()
  const copy = t.install
  const setup = useRemoteSetup({ host: 'first-run' })
  const [applying, setApplying] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)

  // Propose the Mbiling server address (connexion-par-defaut.json, never written in code).
  useEffect(() => {
    let cancelled = false

    void window.mbilingDesktop
      ?.getDefaultConnection?.()
      .then(({ adresse }) => {
        if (!cancelled && adresse && !setup.credentials.url) {
          setup.setUrl(adresse)
        }
      })
      .catch(() => undefined)

    return () => {
      cancelled = true
    }
    // Once, on mount: a later edit by the user must never be overwritten.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const apply = async (): Promise<void> => {
    if (!setup.canCommit || applying) {
      return
    }

    setApplying(true)
    setError(null)

    try {
      await window.mbilingDesktop.applyConnectionConfig(setup.payload)
      onBack()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err || t.settings.gateway.applyFailed))
    } finally {
      setApplying(false)
    }
  }

  return (
    <div className="fixed inset-0 z-(--z-setup) flex items-center justify-center bg-background/90 p-4 backdrop-blur-md">
      <div className="flex w-full max-w-xl flex-col rounded-xl border border-(--stroke-nous) bg-card p-8 shadow-nous">
        <div className="flex items-start gap-4">
          <BrandMark className="size-11 shrink-0" />
          <div className="min-w-0">
            <h2 className="text-xl font-semibold tracking-tight">{copy.remoteSetupTitle}</h2>
            <p className="mt-1.5 text-sm text-muted-foreground">{copy.remoteSetupDesc}</p>
          </div>
        </div>
        <div className="mt-6">
          <RemoteSetupFields disabled={applying} setup={setup} />
          {error ? <div className="mt-3 text-sm text-destructive">{error}</div> : null}
        </div>
        <div className="mt-7 flex flex-wrap items-center justify-between gap-3">
          {canGoBack ? (
            <Button disabled={applying} onClick={onBack} size="sm" variant="ghost">
              {copy.backToSetup}
            </Button>
          ) : (
            <span />
          )}
          <div className="flex items-center gap-2">
            <Button
              disabled={setup.testing || applying || !setup.canTest}
              onClick={() => void setup.test()}
              size="sm"
              variant="secondary"
            >
              {setup.testing ? <Loader2 className="animate-spin" /> : null}
              {copy.testConnection}
            </Button>
            <Button disabled={applying || !setup.canCommit} onClick={() => void apply()} size="sm">
              {applying ? <Loader2 className="animate-spin" /> : null}
              {copy.applyRemote}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
