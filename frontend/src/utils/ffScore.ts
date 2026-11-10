import type { FFScore } from '@/types/dashboard'

// Единая подпись FF-Score: полный балл — эвристика, недоступный не показывается как 0.
export const describeFFScore = (score?: FFScore | null): string => {
  if (!score) {
    return 'FF-Score не рассчитан'
  }
  if (score.status === 'insufficient_data' || score.total === null) {
    return 'Полный балл недоступен: недостаточно данных'
  }
  const value = `${Math.round(score.total)}/100`
  return score.legacyEstimated ? `${value} (оценка по старой формуле, не полный балл)` : `${value} (эвристика)`
}
