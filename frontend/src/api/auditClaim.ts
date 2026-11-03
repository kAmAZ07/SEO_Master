// Одноразовый токен присоединения публичного аудита к проекту (AL-4).
// Сервер отдаёт его только при запуске аудита; без токена аудит к проекту не присоединяется.
const KEY_PREFIX = 'seoMaster.auditClaim.'

export const storeAuditClaimToken = (auditId: string, token: string): void => {
  if (!auditId || !token) {
    return
  }
  try {
    localStorage.setItem(`${KEY_PREFIX}${auditId}`, token)
  } catch {
    // Хранилище недоступно: проект создастся, но без присоединения аудита.
  }
}

export const readAuditClaimToken = (auditId: string): string | undefined => {
  try {
    return localStorage.getItem(`${KEY_PREFIX}${auditId}`) || undefined
  } catch {
    return undefined
  }
}

export const clearAuditClaimToken = (auditId: string): void => {
  try {
    localStorage.removeItem(`${KEY_PREFIX}${auditId}`)
  } catch {
    // нечего очищать
  }
}
