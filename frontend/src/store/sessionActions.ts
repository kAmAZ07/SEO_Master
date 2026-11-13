import { createAction } from '@reduxjs/toolkit'
import type { SessionEndReason } from '@/api/session'

// Отдельный модуль без зависимостей от срезов: на это действие реагируют все срезы и корневой редьюсер.
export const sessionEnded = createAction<{ reason: SessionEndReason }>('session/ended')
export const tokenRefreshed = createAction<{ token: string }>('session/tokenRefreshed')
