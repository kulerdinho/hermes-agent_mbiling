import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { FirstRunSignIn } from './sign-in'

afterEach(cleanup)

describe('FirstRunSignIn', () => {
  it('leaves nothing behind it usable while signed out', () => {
    const app = document.createElement('div')
    const composer = document.createElement('textarea')
    app.appendChild(composer)
    document.body.appendChild(app)
    composer.focus()

    const { unmount } = render(<FirstRunSignIn />)

    // The chat that had the focus can no longer be typed into or clicked.
    expect(app.hasAttribute('inert')).toBe(true)
    expect(document.activeElement).not.toBe(composer)

    // Signed back in: the app is usable again.
    unmount()
    expect(app.hasAttribute('inert')).toBe(false)
    app.remove()
  })
})
