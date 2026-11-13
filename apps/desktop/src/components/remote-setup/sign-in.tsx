import { useState } from 'react'
import type { ReactElement } from 'react'

import { BrandMark } from '@/components/brand-mark'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { useI18n } from '@/i18n'
import { Loader2 } from '@/lib/icons'

type Step = 'idle' | 'waiting' | 'connecting'

interface FirstRunSignInProps {
  /**
   * Team builds only (connexion_avancee): opens the manual connection form. Absent for users:
   * Mbiling only works signed in to a Mbiling account in the cloud.
   */
  onAdvanced?: () => void
  /** Called once the connection is applied. */
  onConnected?: () => void
}

/**
 * Mbiling first launch, like Claude's or Codex's client: one "Sign in" button. The server address
 * comes from the app's settings (connexion-par-defaut.json); sign-in happens in the system browser
 * (OAuth with PKCE, answer on 127.0.0.1); the app stores and renews the session itself. When the
 * user allows it, the Mains then enrol this PC with the server on their own: nothing is typed.
 */
export function FirstRunSignIn({ onAdvanced, onConnected }: FirstRunSignInProps): ReactElement {
  const { t } = useI18n()
  const copy = t.install.mbilingSignIn
  const [step, setStep] = useState<Step>('idle')
  const [allowPc, setAllowPc] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [offerAdvanced, setOfferAdvanced] = useState(false)

  const signIn = async (): Promise<void> => {
    const desktop = window.mbilingDesktop
    setError(null)
    setOfferAdvanced(false)
    setStep('connecting')

    try {
      const { adresse } = await desktop.getDefaultConnection()

      if (!adresse) {
        setError(copy.noSignIn)
        setOfferAdvanced(true)

        return
      }

      const probe = await desktop.probeConnectionConfig(adresse)

      if (!probe.reachable) {
        setError(copy.unreachable)

        return
      }

      if (probe.authMode !== 'oauth') {
        setError(copy.noSignIn)
        setOfferAdvanced(true)

        return
      }

      setStep('waiting')
      const login = await desktop.oauthLoginConnectionConfig(probe.baseUrl)

      if (!login.connected) {
        setError(login.error ? `${copy.failed} ${login.error}` : copy.cancelled)

        return
      }

      setStep('connecting')
      // Recorded before the connection is applied: applying it starts the Mains, which then
      // enrol this PC by themselves when the user allowed it.
      await desktop.mains?.consent(allowPc)
      await desktop.applyConnectionConfig({ mode: 'remote', remoteAuthMode: 'oauth', remoteUrl: probe.baseUrl })
      onConnected?.()
    } catch (err) {
      setError(`${copy.failed} ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setStep('idle')
    }
  }

  const busy = step !== 'idle'

  return (
    <div className="fixed inset-0 z-(--z-setup) flex items-center justify-center bg-background/90 p-4 backdrop-blur-md">
      <div className="flex w-full max-w-md flex-col items-center rounded-xl border border-(--stroke-nous) bg-card p-8 text-center shadow-nous">
        <BrandMark className="size-16" />
        <h2 className="mt-5 text-xl font-semibold tracking-tight">{copy.title}</h2>
        <p className="mt-1.5 text-sm text-muted-foreground">{copy.desc}</p>

        <Button className="mt-6 w-full" disabled={busy} onClick={() => void signIn()} size="lg">
          {busy ? <Loader2 className="animate-spin" /> : null}
          {step === 'waiting' ? copy.waiting : step === 'connecting' ? copy.connecting : copy.button}
        </Button>

        <label className="mt-5 flex cursor-pointer items-start gap-2.5 text-left text-sm">
          <Checkbox
            checked={allowPc}
            className="mt-0.5"
            disabled={busy}
            onCheckedChange={value => setAllowPc(value === true)}
          />
          <span>
            <span className="font-medium">{copy.allowPc}</span>
            <span className="mt-0.5 block text-xs text-muted-foreground">{copy.allowPcHint}</span>
          </span>
        </label>

        {error ? (
          <div className="mt-4 w-full text-sm text-destructive" role="alert">
            {error}
          </div>
        ) : null}

        {onAdvanced ? (
          <button
            className={`mt-5 text-xs underline-offset-2 hover:underline ${offerAdvanced ? 'text-foreground' : 'text-muted-foreground'}`}
            disabled={busy}
            onClick={onAdvanced}
            type="button"
          >
            {copy.advanced}
          </button>
        ) : null}
      </div>
    </div>
  )
}
