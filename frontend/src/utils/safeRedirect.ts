// Возвращает путь для редиректа после входа, только если он внутренний.
// «//host» и «/\\host» браузер трактует как внешний адрес (открытый редирект), их отбрасываем.
export const safeRedirectPath = (value: string | null | undefined): string | null => {
  if (!value || !value.startsWith('/') || value.startsWith('//') ||
      [...value].some((char) => char === '\\' || char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) {
    return null
  }
  return value
}
