import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { MbilingMainsStatus } from '@/global'
import { useI18n } from '@/i18n'
import { Monitor } from '@/lib/icons'

import { ListRow, SectionHeading, SettingsContent, ToggleRow } from './primitives'

/**
 * This PC (the Mains): lets the Mbiling agent on the server run commands on this computer,
 * only inside the allowed folders. The local policy is enforced in the main process
 * (electron/mains-desktop.ts), before every command, whatever the server asks.
 */
export function MainsSettings() {
  const { t } = useI18n()
  const copy = t.settings.mains
  const [status, setStatus] = useState<MbilingMainsStatus | null>(null)
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Signed in to a Mbiling server, this PC is linked automatically; typing a token is a support path.
  const [manual, setManual] = useState(false)

  const refresh = useCallback(async () => {
    const mains = window.mbilingDesktop?.mains

    if (!mains) {
      return
    }

    try {
      setStatus(await mains.status())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), 3000)

    return () => window.clearInterval(timer)
  }, [refresh])

  const run = async (action: () => Promise<MbilingMainsStatus>) => {
    setBusy(true)
    setError(null)

    try {
      setStatus(await action())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const open = async (which: 'politique' | 'reglages' | 'journal') => {
    const problem = await window.mbilingDesktop.mains.open(which)

    if (problem) {
      setError(problem)
    }
  }

  const state = !status
    ? ''
    : !status.actif
      ? copy.statusOff
      : !status.jetonDefini
        ? copy.statusNoToken
        : status.connecte
          ? copy.statusConnected
          : copy.statusConnecting

  return (
    <SettingsContent>
      <SectionHeading icon={Monitor} page title={copy.title} />
      <p className="mb-4 max-w-2xl text-sm text-muted-foreground">{copy.intro}</p>

      <ToggleRow
        checked={Boolean(status?.actif)}
        description={state}
        disabled={busy || !status}
        label={copy.enabled}
        onChange={on => void run(() => window.mbilingDesktop.mains.setActive(on))}
      />

      <ListRow description={status?.relais || '—'} title={copy.relay} />

      {status?.inscriptionAutomatique && !manual ? (
        <ListRow
          action={
            <Button onClick={() => setManual(true)} size="sm" variant="ghost">
              {copy.manualToken}
            </Button>
          }
          description={status.inscriptionEnCours ? copy.tokenEnrolling : copy.tokenAuto}
          title={copy.token}
        />
      ) : (
      <ListRow
        action={
          <div className="flex items-center gap-2">
            <Input
              className="h-8 w-64"
              onChange={event => setToken(event.target.value)}
              placeholder={copy.tokenPlaceholder}
              type="password"
              value={token}
            />
            <Button
              disabled={busy || !token.trim()}
              onClick={() =>
                void run(async () => {
                  const next = await window.mbilingDesktop.mains.setToken(token)
                  setToken('')

                  return next
                })
              }
              size="sm"
            >
              {copy.saveToken}
            </Button>
            {status?.jetonDefini ? (
              <Button
                disabled={busy}
                onClick={() => void run(() => window.mbilingDesktop.mains.setToken(''))}
                size="sm"
                variant="outline"
              >
                {copy.removeToken}
              </Button>
            ) : null}
          </div>
        }
        description={status?.jetonDefini ? copy.tokenSet : copy.tokenMissing}
        title={copy.token}
      />
      )}

      <ListRow
        action={
          <Button onClick={() => void open('politique')} size="sm" variant="outline">
            {copy.editPolicy}
          </Button>
        }
        description={status?.dossiers.length ? status.dossiers.join(' · ') : copy.noFolders}
        title={copy.folders}
      />

      <ListRow description={status?.bash || copy.bashMissing} title={copy.bash} />

      <ListRow
        action={
          <div className="flex items-center gap-2">
            <Button onClick={() => void open('journal')} size="sm" variant="outline">
              {copy.openLog}
            </Button>
            <Button onClick={() => void open('reglages')} size="sm" variant="outline">
              {copy.editSettings}
            </Button>
            <Button
              disabled={busy}
              onClick={() => void run(() => window.mbilingDesktop.mains.restart())}
              size="sm"
              variant="secondary"
            >
              {copy.restart}
            </Button>
          </div>
        }
        description={error || status?.raison || copy.noProblem}
        title={copy.lastProblem}
      />
    </SettingsContent>
  )
}
