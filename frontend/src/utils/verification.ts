import type { VerificationState, VerificationSummary } from '@/types/dashboard'

export const VERIFICATION_STATE_LABELS: Record<VerificationState, string> = {
  unverified: 'Не подтверждён',
  pending: 'Ожидает проверки файла',
  verified: 'Подтверждён',
  stale: 'Подтверждение устарело',
}

// Коды errorCode проверки: contracts/projects.md, «Подтверждение: HTTP-challenge».
const CHECK_ERROR_MESSAGES: Record<string, string> = {
  redirected: 'Сайт перенаправил запрос на файл. Файл должен отдаваться по указанному адресу без редиректов.',
  bad_content_type: 'Файл отдаётся как HTML. Он должен быть обычным текстовым файлом (text/plain).',
  malformed_body: 'Содержимое файла не распознано. Скопируйте строку целиком, без дополнительных символов.',
  token_mismatch: 'В файле другой токен. Возможно, вы разместили файл от прежнего запроса: выдайте новый и разместите его.',
  challenge_expired: 'Срок действия файла истёк. Выдайте новый файл подтверждения.',
  attempts_exhausted: 'Исчерпано число попыток проверки. Выдайте новый файл подтверждения.',
  verification_conflict: 'Проверка пересеклась с другим запросом. Повторите её.',
  challenge_no_longer_pending: 'Файл подтверждения уже не актуален. Выдайте новый.',
  fetch_address_blocked: 'Адрес сайта недоступен для проверки (закрытый или непубличный адрес).',
  fetch_dns_failure: 'Не удалось найти сайт по домену. Проверьте DNS.',
  fetch_timeout: 'Сайт не ответил вовремя. Проверьте доступность и повторите.',
  fetch_tls_error: 'Не удалось установить защищённое соединение с сайтом. Проверьте сертификат.',
}

export const describeVerificationError = (code: string | null | undefined): string => {
  if (!code) {
    return 'Проверка не удалась. Повторите попытку позже.'
  }
  if (code.startsWith('http_')) {
    return `Сайт ответил статусом ${code.slice(5)}: файл подтверждения по указанному адресу не найден.`
  }
  return CHECK_ERROR_MESSAGES[code] ?? `Проверка не удалась (код: ${code}).`
}

export const formatVerificationDate = (value: string | null | undefined): string | null => {
  if (!value) {
    return null
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('ru-RU')
}

export const describeVerificationSummary = (summary: VerificationSummary): string => {
  switch (summary.state) {
    case 'verified': {
      const until = formatVerificationDate(summary.freshUntil)
      return until ? `Подтверждён, действует до ${until}` : 'Подтверждён'
    }
    case 'stale': {
      const at = formatVerificationDate(summary.verifiedAt)
      return at ? `Подтверждение от ${at} устарело` : VERIFICATION_STATE_LABELS.stale
    }
    default:
      return VERIFICATION_STATE_LABELS[summary.state] ?? 'Состояние неизвестно'
  }
}
