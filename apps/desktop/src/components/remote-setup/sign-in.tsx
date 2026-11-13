import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'

import { BrandMark } from '@/components/brand-mark'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { useI18n } from '@/i18n'
import { Loader2 } from '@/lib/icons'

type Step = 'idle' | 'waiting' | 'connecting'

interface SocialProvider {
  id: string
  libelle: string
}

/** Brand marks for the social sign-in buttons (others show their name only). */
function ProviderIcon({ id }: { id: string }): ReactElement | null {
  if (id === 'google') {
    return (
      <svg aria-hidden="true" className="size-4" viewBox="0 0 48 48">
        <path d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" fill="#FFC107" />
        <path d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" fill="#FF3D00" />
        <path d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z" fill="#4CAF50" />
        <path d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" fill="#1976D2" />
      </svg>
    )
  }

  if (id === 'apple') {
    return (
      <svg aria-hidden="true" className="size-4" fill="currentColor" viewBox="0 0 24 24">
        <path d="M16.37 12.46c-.02-2.17 1.77-3.22 1.85-3.27-1.01-1.48-2.58-1.68-3.14-1.7-1.33-.14-2.6.78-3.28.78-.68 0-1.72-.76-2.83-.74-1.45.02-2.8.85-3.55 2.15-1.52 2.63-.39 6.52 1.09 8.66.73 1.04 1.59 2.21 2.72 2.17 1.09-.04 1.5-.7 2.82-.7 1.31 0 1.69.7 2.84.68 1.17-.02 1.92-1.06 2.63-2.11.83-1.21 1.17-2.38 1.19-2.44-.03-.01-2.28-.87-2.3-3.48zM14.21 6.08c.6-.73 1.01-1.74.9-2.75-.87.04-1.92.58-2.54 1.31-.56.64-1.05 1.67-.92 2.66.97.08 1.96-.49 2.56-1.22z" />
      </svg>
    )
  }

  return null
}

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
  // "Continue with Google / Apple": listed in connexion-par-defaut.json (fournisseurs).
  const [providers, setProviders] = useState<SocialProvider[]>([])

  useEffect(() => {
    void window.mbilingDesktop
      ?.getDefaultConnection?.()
      .then(value => setProviders(value.fournisseurs ?? []))
      .catch(() => undefined)
  }, [])

  const signIn = async (idpHint?: string): Promise<void> => {
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
      const login = idpHint
        ? await desktop.oauthLoginConnectionConfig(probe.baseUrl, { idpHint })
        : await desktop.oauthLoginConnectionConfig(probe.baseUrl)

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

        {providers.length ? (
          <div className="mt-6 flex w-full flex-col gap-2">
            {providers.map(provider => (
              <Button
                className="w-full"
                disabled={busy}
                key={provider.id}
                onClick={() => void signIn(provider.id)}
                size="lg"
                variant="outline"
              >
                <ProviderIcon id={provider.id} />
                {`${copy.continueWith} ${provider.libelle}`}
              </Button>
            ))}
          </div>
        ) : null}

        <Button className={providers.length ? 'mt-2 w-full' : 'mt-6 w-full'} disabled={busy} onClick={() => void signIn()} size="lg">
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
