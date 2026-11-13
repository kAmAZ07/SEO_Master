import { describe, expect, it } from 'vitest'

import { safeRedirectPath } from './safeRedirect'

describe('safeRedirectPath', () => {
  it.each(['/dashboard', '/audit/results/a1?saveProject=1', '/'])('пропускает внутренний путь %s', (value) => {
    expect(safeRedirectPath(value)).toBe(value)
  })

  it.each(['//evil.example', '/\\evil.example', 'https://evil.example', 'javascript:alert(1)', '', null, undefined])(
    'отбрасывает %s',
    (value) => {
      expect(safeRedirectPath(value as string | null | undefined)).toBeNull()
    },
  )

  it.each([
    '/' + String.fromCharCode(92) + 'evil.example',
    '/\t/evil.example',
    '/\n/evil.example',
    '/\r/evil.example',
  ])('отбрасывает внешний URL после нормализации браузером: %j', (value) => {
    expect(new URL(value, 'https://app.example').origin).toBe('https://evil.example')
    expect(safeRedirectPath(value)).toBeNull()
  })
})
