export const formatNumber = (num: number): string => {
  return new Intl.NumberFormat('ru-RU').format(num);
};

export const formatDate = (date: string | Date): string => {
  return new Date(date).toLocaleDateString('ru-RU', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
};

export const formatDateTime = (date: string | Date): string => {
  return new Date(date).toLocaleString('ru-RU', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

export const formatCurrency = (amount: number, currency: string = 'RUB'): string => {
  return new Intl.NumberFormat('ru-RU', {
    style: 'currency',
    currency: currency,
  }).format(amount);
};

export const formatPercentage = (value: number): string => {
  return `${value.toFixed(2)}%`;
};

export const truncateText = (text: string, maxLength: number): string => {
  if (text.length <= maxLength) return text;
  return text.substring(0, maxLength) + '...';
};

const PROJECT_STATUS_LABELS: Record<string, string> = {
  active: 'Активен',
  paused: 'Приостановлен',
  archived: 'В архиве',
  deleted: 'Удалён',
  draft: 'Черновик',
  pending: 'Ожидает настройки',
  inactive: 'Неактивен',
  disabled: 'Отключён',
};

export const formatProjectStatus = (status?: string | null): string => {
  const normalized = String(status || '').trim().toLowerCase();
  if (!normalized) {
    return 'Не указан';
  }

  return PROJECT_STATUS_LABELS[normalized] || status || 'Не указан';
};
