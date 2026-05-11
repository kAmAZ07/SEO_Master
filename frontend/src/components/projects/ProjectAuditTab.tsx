import { FormEvent, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAppDispatch, useAppSelector } from '../../store/hooks'
import { pollAuditStatus, setPolling, startAudit } from '../../store/slices/auditSlice'
import Card from '../ui/Card'
import Button from '../ui/Button'
import Input from '../ui/Input'
import Loader from '../ui/Loader'

interface ProjectAuditTabProps {
  projectId: string
  projectUrl: string
}

const ProjectAuditTab = ({ projectId, projectUrl }: ProjectAuditTabProps) => {
  const dispatch = useAppDispatch()
  const { currentAudit, loading, polling, error } = useAppSelector((state) => state.audit)
  const [url, setUrl] = useState(projectUrl)
  const [auditUid, setAuditUid] = useState<string | null>(null)

  useEffect(() => {
    setUrl(projectUrl)
  }, [projectUrl])

  useEffect(() => {
    const activeUid = auditUid || currentAudit?.uid
    const status = currentAudit?.status
    const shouldPoll = Boolean(activeUid && polling && status !== 'completed' && status !== 'failed')

    if (!shouldPoll || !activeUid) {
      return undefined
    }

    const timer = window.setInterval(() => {
      void dispatch(pollAuditStatus(activeUid))
    }, 3000)

    return () => {
      window.clearInterval(timer)
    }
  }, [auditUid, currentAudit?.status, currentAudit?.uid, dispatch, polling])

  const activeAudit = useMemo(() => {
    if (!currentAudit || (auditUid && currentAudit.uid !== auditUid)) {
      return null
    }
    return currentAudit
  }, [auditUid, currentAudit])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    try {
      const audit = await dispatch(startAudit({ projectId, url: url.trim() || projectUrl })).unwrap()
      setAuditUid(audit.uid)
      dispatch(setPolling(audit.status !== 'completed' && audit.status !== 'failed'))
    } catch {
    }
  }

  return (
    <div className="space-y-6">
      <Card className="p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-gray-900">Расширенный аудит проекта</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-600">
              Этот режим доступен для сохраненного проекта: обходит больше страниц, использует большую глубину проверки,
              включает JS-rendering и подключает проектные данные, когда они доступны.
            </p>
          </div>
          {activeAudit?.uid && (
            <Link to={`/audit/results/${activeAudit.uid}?project=${projectId}`}>
              <Button type="button" variant="outline">Открыть полный отчет</Button>
            </Link>
          )}
        </div>

        <form onSubmit={handleSubmit} className="mt-6 grid gap-4 lg:grid-cols-[1fr_auto] lg:items-end">
          <Input
            label="URL для проверки"
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder={projectUrl}
          />
          <Button type="submit" disabled={loading}>
            {loading ? 'Запускаем...' : 'Запустить аудит'}
          </Button>
        </form>

        {error && (
          <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}
      </Card>

      {activeAudit && activeAudit.status !== 'completed' && activeAudit.status !== 'failed' && (
        <Card className="p-8 text-center">
          <Loader />
          <h3 className="mt-5 text-lg font-semibold text-gray-900">Аудит выполняется</h3>
          <p className="mt-2 text-sm text-gray-600">Статус: {activeAudit.status}</p>
        </Card>
      )}

      {activeAudit?.status === 'failed' && (
        <Card className="border-red-200 bg-red-50 p-6">
          <h3 className="text-lg font-semibold text-red-900">Аудит завершился с ошибкой</h3>
          <p className="mt-2 text-sm text-red-700">{activeAudit.error || 'Попробуйте запустить проверку еще раз.'}</p>
        </Card>
      )}

      {activeAudit?.status === 'completed' && (
        <Card className="p-6">
          <div className="grid gap-4 md:grid-cols-4">
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-400">Оценка</p>
              <p className="mt-2 text-3xl font-bold text-gray-900">{activeAudit.score}/100</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-400">Ошибки</p>
              <p className="mt-2 text-3xl font-bold text-red-700">{activeAudit.issues.errors}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-400">Предупреждения</p>
              <p className="mt-2 text-3xl font-bold text-amber-700">{activeAudit.issues.warnings}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-400">Страницы</p>
              <p className="mt-2 text-3xl font-bold text-gray-900">{activeAudit.pages?.length ?? 0}</p>
            </div>
          </div>
        </Card>
      )}
    </div>
  )
}

export default ProjectAuditTab
