import type { ParsedApiError } from './apiError'

// Сообщения для кодов, по которым пользователю нужно понять, что делать дальше.
// Коды описаны в contracts/projects.md.
const DOMAIN_NOT_VERIFIED_MESSAGES: Record<string, string> = {
  not_verified: 'Права на сайт не подтверждены. Подтвердите владение сайтом, чтобы запустить расширенный аудит.',
  stale: 'Подтверждение владения сайтом устарело. Пройдите проверку заново.',
  scope_changed: 'Область проекта изменилась после подтверждения. Подтвердите владение заново.',
}

export const describeProjectError = (error: ParsedApiError, fallback: string): string => {
  switch (error.code) {
    case 'domain_not_verified':
      return DOMAIN_NOT_VERIFIED_MESSAGES[error.reason ?? ''] ?? DOMAIN_NOT_VERIFIED_MESSAGES.not_verified
    case 'url_out_of_scope':
      return 'Адрес находится вне области проекта: проверить можно только страницы внутри неё.'
    case 'project_scope_unresolved':
      return 'У проекта нет корректной области сайта. Проект требует проверки, расширенный аудит недоступен.'
    case 'scope_invalid':
      return 'Адрес проекта не удалось разобрать: укажите корректный публичный URL сайта.'
    case 'insufficient_role':
      return 'Недостаточно прав для этого действия в рабочем пространстве.'
    case 'project_not_found':
      return 'Проект не найден.'
    default:
      return error.message ?? fallback
  }
}

// Ошибки, после которых пользователя нужно вести к подтверждению владения.
export const needsOwnership = (error: ParsedApiError): boolean =>
  error.code === 'domain_not_verified'
