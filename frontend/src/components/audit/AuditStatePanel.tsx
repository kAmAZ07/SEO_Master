import type { AuditStatus } from '../../types/audit'
import {
  AUDIT_STATUS_LABELS,
  describePartialReason,
  isActiveAuditStatus,
  isEmptyAuditResult,
  type AuditLoadErrorKind,
} from '../../utils/auditStatus'
import Button from '../ui/Button'
import Card from '../ui/Card'

interface AuditStatePanelProps {
  audit: AuditStatus | null
  error: string | null
  errorKind: AuditLoadErrorKind | null
  // Опрос остановлен после серии неудач: показываем «Повторить».
  gaveUp: boolean
  onRetry: () => void
  // Отмена доступна только для публичного аудита, который ещё не завершён.
  onCancel?: () => void
  cancelling?: boolean
}

const TRANSIENT_KINDS: AuditLoadErrorKind[] = ['timeout', 'network', 'unavailable', 'rate_limited']

// Состояния аудита, не связанные с отображением результатов: ожидание, ошибки, отмена, истечение, частичный результат.
// Все они различимы по тексту и роли, чтобы пользователь не принимал «нет связи» за «аудит не найден».
const AuditStatePanel = ({ audit, error, errorKind, gaveUp, onRetry, onCancel, cancelling = false }: AuditStatePanelProps) => {
  const transient = errorKind !== null && TRANSIENT_KINDS.includes(errorKind)
  const permanentLoadError = errorKind === 'not_found' || errorKind === 'expired'
  const status = audit?.status

  return (
    <div className="space-y-4">
      {error && permanentLoadError && (
        <Card className="border-amber-200 bg-amber-50 p-6">
          <div role="alert">
            <h2 className="text-lg font-semibold text-amber-900">
              {errorKind === 'expired' ? 'Срок хранения отчёта истёк' : 'Аудит не найден'}
            </h2>
            <p className="mt-2 text-sm text-amber-800">{error}</p>
          </div>
        </Card>
      )}

      {error && !permanentLoadError && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <p>{error}</p>
          {transient && !gaveUp && <p className="mt-1 text-red-600">Повторяем попытку автоматически…</p>}
          {transient && gaveUp && (
            <Button type="button" size="sm" variant="outline" className="mt-3" onClick={onRetry}>
              Повторить
            </Button>
          )}
        </div>
      )}

      {audit && isActiveAuditStatus(status) && (
        <Card className="rounded-lg p-8 text-center">
          <div role="status" aria-live="polite">
            <h2 className="text-xl font-semibold text-gray-950">
              {status === 'queued' ? 'Аудит в очереди' : 'Аудит выполняется'}
            </h2>
            <p className="mt-2 text-gray-600">
              Статус: {AUDIT_STATUS_LABELS[audit.status]}. Проверяем страницы, метаданные, ссылки, структурированные данные и
              доступность для crawler.
            </p>
          </div>
          <div
            role="progressbar"
            aria-label="Ход выполнения аудита"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.max(0, Math.min(100, Math.round(audit.progress)))}
            className="mx-auto mt-5 h-3 max-w-md rounded-full bg-gray-200"
          >
            <div
              className="h-3 rounded-full bg-blue-600 transition-all"
              style={{ width: `${Math.max(0, Math.min(100, Math.round(audit.progress)))}%` }}
            />
          </div>
          {onCancel && (
            <Button type="button" variant="outline" className="mt-6" onClick={onCancel} disabled={cancelling}>
              {cancelling ? 'Отменяем...' : 'Отменить аудит'}
            </Button>
          )}
        </Card>
      )}

      {status === 'failed' && (
        <Card className="border-red-200 bg-red-50 p-6">
          <div role="alert">
            <h2 className="text-xl font-semibold text-red-900">Аудит завершился с ошибкой</h2>
            <p className="mt-2 text-sm text-red-700">{audit?.error || 'Попробуйте запустить проверку ещё раз.'}</p>
            {audit?.errorCode && <p className="mt-1 text-xs text-red-600">Код ошибки: {audit.errorCode}</p>}
          </div>
        </Card>
      )}

      {status === 'cancelled' && (
        <Card className="border-gray-200 bg-gray-50 p-6">
          <div role="status">
            <h2 className="text-xl font-semibold text-gray-900">Аудит отменён</h2>
            <p className="mt-2 text-sm text-gray-700">Проверка остановлена до завершения. Запустите аудит заново, чтобы получить отчёт.</p>
          </div>
        </Card>
      )}

      {status === 'expired' && (
        <Card className="border-amber-200 bg-amber-50 p-6">
          <div role="status">
            <h2 className="text-xl font-semibold text-amber-900">Срок хранения результата истёк</h2>
            <p className="mt-2 text-sm text-amber-800">Отчёт удалён с сервера. Запустите аудит заново.</p>
          </div>
        </Card>
      )}

      {status === 'unknown' && (
        <Card className="border-amber-200 bg-amber-50 p-6">
          <div role="alert">
            <h2 className="text-xl font-semibold text-amber-900">Состояние аудита не удалось определить</h2>
            <p className="mt-2 text-sm text-amber-800">
              Сервер вернул статус, который приложение не распознаёт. Обновите страницу или запустите проверку заново.
            </p>
          </div>
        </Card>
      )}

      {audit && status === 'partial' && (
        <div role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <p className="font-semibold">Аудит завершён частично</p>
          <p className="mt-1">
            Покрытие ограничено, поэтому оценка и список замечаний могут быть неполными.
            {audit.partialReasons && audit.partialReasons.length > 0
              ? ` Причины: ${audit.partialReasons.map(describePartialReason).join('; ')}.`
              : ''}
          </p>
        </div>
      )}

      {audit && status === 'completed' && isEmptyAuditResult(audit) && (
        <div role="status" className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900">
          Аудит завершён, но данных для отчёта нет: сервер не вернул ни критериев, ни замечаний, ни страниц.
        </div>
      )}
    </div>
  )
}

export default AuditStatePanel
