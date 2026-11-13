import type { AuditStatus, AuditWorkflowStatus } from '@/types/audit'

// Статусы, после которых опрос прекращается: результат больше не изменится.
// 'unknown' тоже останавливает опрос: статус, которого фронтенд не понимает, нельзя ждать бесконечно.
const TERMINAL_STATUSES: ReadonlySet<AuditWorkflowStatus> = new Set([
  'completed',
  'partial',
  'failed',
  'cancelled',
  'expired',
  'unknown',
])

export const isTerminalAuditStatus = (status: AuditWorkflowStatus | undefined): boolean =>
  status !== undefined && TERMINAL_STATUSES.has(status)

export const isActiveAuditStatus = (status: AuditWorkflowStatus | undefined): boolean =>
  status === 'queued' || status === 'running'

// Результат с находками есть у завершённого и частичного аудита.
export const hasAuditResults = (status: AuditWorkflowStatus | undefined): boolean =>
  status === 'completed' || status === 'partial'

// Нет ни критериев, ни замечаний, ни страниц: завершённый аудит без данных — отдельное состояние, а не «всё хорошо».
export const isEmptyAuditResult = (audit: AuditStatus): boolean =>
  (audit.summary?.criteria?.length ?? 0) === 0 && audit.details.length === 0 && (audit.pages?.length ?? 0) === 0

export const AUDIT_STATUS_LABELS: Record<AuditWorkflowStatus, string> = {
  queued: 'В очереди',
  running: 'Выполняется',
  completed: 'Завершён',
  partial: 'Завершён частично',
  failed: 'Ошибка',
  cancelled: 'Отменён',
  expired: 'Срок хранения истёк',
  unknown: 'Статус неизвестен',
}

const KNOWN_STATUSES = new Set<string>(Object.keys(AUDIT_STATUS_LABELS))

// История проекта отдаёт статус строкой: показываем русскую подпись, а незнакомое значение — как есть.
export const formatAuditStatus = (status: string): string =>
  KNOWN_STATUSES.has(status) ? AUDIT_STATUS_LABELS[status as AuditWorkflowStatus] : status

// Коды причин частичного результата: services/audit_service/crawler/technical_audit.py и netio.py.
const PARTIAL_REASON_LABELS: Record<string, string> = {
  deadline: 'исчерпано время проверки',
  page_requests: 'исчерпан лимит запросов к страницам',
  aux_requests: 'исчерпан лимит служебных запросов (robots, sitemap, ссылки)',
  sitemap_truncated: 'sitemap прочитан не полностью',
  robots_blocked: 'часть страниц закрыта правилами robots.txt',
  robots_unreachable: 'robots.txt недоступен',
}

export const describePartialReason = (reason: string): string => PARTIAL_REASON_LABELS[reason] ?? reason

// Подпись оценки: оценка аудита — эвристика, а не измеренный показатель, и это должно быть видно.
export const describeAuditScore = (score: number | null | undefined): string =>
  typeof score === 'number' ? `${score}/100 (эвристика)` : 'Оценка недоступна'

export type AuditLoadErrorKind =
  | 'not_found'
  | 'expired'
  | 'timeout'
  | 'network'
  | 'unavailable'
  | 'rate_limited'
  | 'aborted'
  | 'error'

// Эти ошибки не пройдут при повторе: опрос прекращается.
export const isPermanentAuditError = (kind: AuditLoadErrorKind): boolean =>
  kind === 'not_found' || kind === 'expired' || kind === 'error'

const LOAD_ERROR_MESSAGES: Record<Exclude<AuditLoadErrorKind, 'error' | 'aborted'>, string> = {
  not_found: 'Аудит не найден или недоступен для вашего аккаунта.',
  expired: 'Срок хранения результата истёк, отчёт удалён.',
  timeout: 'Сервер не ответил вовремя.',
  network: 'Нет связи с сервером.',
  unavailable: 'Сервис аудита временно недоступен.',
  rate_limited: 'Слишком много запросов, повторим позже.',
}

export const describeAuditLoadError = (kind: AuditLoadErrorKind, fallback: string): string =>
  kind === 'error' || kind === 'aborted' ? fallback : LOAD_ERROR_MESSAGES[kind]
