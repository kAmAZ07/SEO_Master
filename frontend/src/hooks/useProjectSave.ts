import { useCallback, useEffect, useReducer, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAppDispatch } from '../store/hooks'
import { createProject } from '../store/slices/dashboardSlice'
import { PENDING_PROJECT_SAVE_KEY } from '../api/session'
import type { AuditStatus } from '../types/audit'
import {
  getProjectNameFromUrl,
  initialProjectSaveState,
  projectSaveReducer,
  readPendingProjectSave,
} from '../utils/projectSave'

interface UseProjectSaveOptions {
  audit: AuditStatus | null
  // Пользователь вошёл (профиль подтверждён сервером).
  canSave: boolean
  // Вернулись с входа/регистрации по ссылке ?saveProject=1: сохранение запускается само, один раз.
  autoSave: boolean
}

// Сохраняет публичный аудит как проект. Создание идемпотентно на сервере (та же область даёт тот же проект),
// поэтому повтор после ошибки безопасен. Автозапуск выполняется не более одного раза для каждого аудита.
export const useProjectSave = ({ audit, canSave, autoSave }: UseProjectSaveOptions) => {
  const dispatch = useAppDispatch()
  const navigate = useNavigate()
  const [state, dispatchSave] = useReducer(projectSaveReducer, initialProjectSaveState)
  const inFlight = useRef(false)
  const autoStartedFor = useRef<string | null>(null)

  const uid = audit?.uid
  const url = audit?.url

  const save = useCallback(
    async (sourceUrl?: string) => {
      const targetUrl = sourceUrl || url
      if (!uid || !targetUrl || inFlight.current) {
        return
      }
      inFlight.current = true
      dispatchSave({ type: 'start' })
      try {
        const project = await dispatch(
          createProject({ name: getProjectNameFromUrl(targetUrl), url: targetUrl, sourceAuditId: uid }),
        ).unwrap()
        localStorage.removeItem(PENDING_PROJECT_SAVE_KEY)
        dispatchSave({ type: 'success' })
        if (project.claim?.status === 'rejected') {
          // Проект создан, но аудит к нему не присоединён: ведём на карточку проекта с объяснением.
          navigate(`/dashboard/projects/${project.id}`, { state: { claimRejected: project.claim.reason ?? 'unknown' } })
        } else {
          navigate(`/dashboard/projects/${project.id}/audits/${uid}`)
        }
      } catch (error) {
        dispatchSave({
          type: 'failure',
          message: typeof error === 'string' && error ? error : 'Не удалось сохранить проект.',
        })
      } finally {
        inFlight.current = false
      }
    },
    [dispatch, navigate, uid, url],
  )

  // Ручной запуск: запоминаем намерение на случай перехода на вход/регистрацию.
  const requestSave = useCallback(() => {
    if (!uid || !url) {
      return false
    }
    localStorage.setItem(PENDING_PROJECT_SAVE_KEY, JSON.stringify({ uid, url }))
    if (!canSave) {
      return false
    }
    void save()
    return true
  }, [canSave, save, uid, url])

  useEffect(() => {
    if (!autoSave || !canSave || !uid || !url || autoStartedFor.current === uid) {
      return
    }
    const pending = readPendingProjectSave(localStorage, PENDING_PROJECT_SAVE_KEY)
    if (pending?.uid !== uid) {
      return
    }
    autoStartedFor.current = uid
    void save(pending.url)
  }, [autoSave, canSave, save, uid, url])

  const retry = useCallback(() => {
    void save()
  }, [save])

  return { state, requestSave, retry }
}
