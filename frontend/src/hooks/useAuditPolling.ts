import { useCallback, useEffect, useState } from 'react'
import { useAppDispatch } from '../store/hooks'
import { pollAuditStatus, selectAudit, setPolling } from '../store/slices/auditSlice'
import { isPermanentAuditError, isTerminalAuditStatus } from '../utils/auditStatus'

export const AUDIT_POLL_INTERVAL_MS = 2500
// Столько подряд неудачных запросов терпим (сеть, таймаут, 5xx), затем останавливаемся и предлагаем «Повторить».
export const AUDIT_POLL_MAX_FAILURES = 3

// Опрашивает статус аудита uid. Опрос последовательный (следующий запрос после ответа на предыдущий),
// прекращается на конечном статусе, при постоянной ошибке, при смене uid и размонтировании;
// запрос, который ещё в пути, отменяется.
export const useAuditPolling = (uid: string | undefined, intervalMs = AUDIT_POLL_INTERVAL_MS) => {
  const dispatch = useAppDispatch()
  const [attempt, setAttempt] = useState(0)
  const [gaveUpKey, setGaveUpKey] = useState<string | null>(null)

  useEffect(() => {
    if (!uid) {
      return undefined
    }

    dispatch(selectAudit(uid))

    let cancelled = false
    let timer: number | undefined
    let inFlight: { abort: () => void } | undefined
    let failures = 0

    const tick = async () => {
      const request = dispatch(pollAuditStatus(uid))
      inFlight = request
      const result = await request
      if (cancelled) {
        return
      }

      if (pollAuditStatus.fulfilled.match(result)) {
        failures = 0
        if (isTerminalAuditStatus(result.payload.status)) {
          return
        }
      } else {
        if (isPermanentAuditError(result.payload?.kind ?? 'error')) {
          return
        }
        failures += 1
        if (failures >= AUDIT_POLL_MAX_FAILURES) {
          dispatch(setPolling(false))
          setGaveUpKey(`${uid}:${attempt}`)
          return
        }
      }

      timer = window.setTimeout(() => void tick(), intervalMs)
    }

    void tick()

    return () => {
      cancelled = true
      window.clearTimeout(timer)
      inFlight?.abort()
    }
  }, [attempt, dispatch, intervalMs, uid])

  const retry = useCallback(() => setAttempt((value) => value + 1), [])

  return { gaveUp: Boolean(uid) && gaveUpKey === `${uid}:${attempt}`, retry }
}
