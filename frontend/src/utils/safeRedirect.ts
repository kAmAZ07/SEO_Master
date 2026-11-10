// Возвращает путь для редиректа после входа, только если он внутренний.
// «//host» и «/\\host» браузер трактует как внешний адрес (открытый редирект), их отбрасываем.
export const safeRedirectPath = (value: string | null | undefined): string | null => {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\')) {
    return null
  }
  return value
}
