import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import ProjectFFScore from './ProjectFFScore'
import type { FFScore } from '../../types/dashboard'

const score = (over: Partial<FFScore> = {}): FFScore => ({
  total: 71.4, status: 'complete', formulaVersion: 'ff-2', heuristic: true, legacyEstimated: false,
  freshness: 80, familiarity: null, quality: 55.5, timestamp: '2026-11-10T00:00:00Z', ...over,
})

describe('ProjectFFScore', () => {
  it('полный балл подписан как эвристика, недоступные компоненты — «нет данных», а не 0', () => {
    render(<ProjectFFScore score={score()} />)
    expect(screen.getByText('71/100 (эвристика)')).toBeInTheDocument()
    const familiarity = screen.getByText('Известность').closest('div')!
    expect(within(familiarity).getByText('нет данных')).toBeInTheDocument()
    expect(screen.queryByText('0/100')).not.toBeInTheDocument()
    expect(screen.getByText('Версия формулы: ff-2')).toBeInTheDocument()
  })

  it('недостаточно данных: полного балла нет, число не показывается', () => {
    render(<ProjectFFScore score={score({ total: null, status: 'insufficient_data', freshness: null, quality: null })} />)
    expect(screen.getByText('Полный балл недоступен: недостаточно данных')).toBeInTheDocument()
    expect(screen.getAllByText('нет данных')).toHaveLength(3)
  })

  it('оценка по старой формуле не выдаётся за полный балл', () => {
    render(<ProjectFFScore score={score({ status: 'legacy_estimated', legacyEstimated: true })} />)
    expect(screen.getByText(/оценка по старой формуле, не полный балл/)).toBeInTheDocument()
  })

  it('частичная оценка подписана отдельно', () => {
    render(<ProjectFFScore score={score({ total: null, status: 'insufficient_data', partial: { score: 42.2 } })} />)
    expect(screen.getByText(/Частичная оценка по доступным данным: 42\/100/)).toBeInTheDocument()
    expect(screen.getByText(/не сопоставима с полным баллом/)).toBeInTheDocument()
  })

  it('нет FF-Score вообще', () => {
    render(<ProjectFFScore score={undefined} />)
    expect(screen.getByText('FF-Score не рассчитан')).toBeInTheDocument()
  })
})
