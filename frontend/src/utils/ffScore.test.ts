import { describe, expect, it } from 'vitest'
import type { FFScore } from '@/types/dashboard'
import { describeFFScore } from './ffScore'

const score = (overrides: Partial<FFScore>): FFScore => ({
  total: 81.07,
  status: 'complete',
  formulaVersion: 'ff_score_v1',
  heuristic: true,
  legacyEstimated: false,
  freshness: 70,
  familiarity: 90.5,
  quality: 81,
  timestamp: '2026-11-03T12:00:00Z',
  ...overrides,
})

describe('describeFFScore', () => {
  it('marks a full score as a heuristic', () => {
    expect(describeFFScore(score({}))).toBe('81/100 (эвристика)')
  })

  it('never shows an unavailable score as zero', () => {
    const text = describeFFScore(score({ total: null, status: 'insufficient_data' }))
    expect(text).toBe('Полный балл недоступен: недостаточно данных')
    expect(text).not.toContain('0/100')
  })

  it('treats a null total as unavailable even when the status claims otherwise', () => {
    expect(describeFFScore(score({ total: null }))).toContain('недоступен')
  })

  it('distinguishes legacy estimated scores from full ones', () => {
    expect(describeFFScore(score({ status: 'legacy_estimated', legacyEstimated: true }))).toContain('не полный балл')
  })

  it('says so when no score was calculated', () => {
    expect(describeFFScore(null)).toBe('FF-Score не рассчитан')
    expect(describeFFScore(undefined)).toBe('FF-Score не рассчитан')
  })
})
