export const validateEmail = (email: string): boolean => {
  const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return regex.test(email);
};

export const validateUrl = (url: string): boolean => {
  try {
    new URL(url);
    return true;
  } catch {
    return false;
  }
};

export const validatePassword = (password: string): { valid: boolean; message?: string } => {
  if (password.length < 6) {
    return { valid: false, message: 'Пароль должен содержать минимум 6 символов' };
  }
  if (!/[A-Za-z]/.test(password)) {
    return { valid: false, message: 'Пароль должен содержать хотя бы одну букву' };
  }
  if (!/[0-9]/.test(password)) {
    return { valid: false, message: 'Пароль должен содержать хотя бы одну цифру' };
  }
  return { valid: true };
};

export const sanitizeInput = (input: string): string => {
  return input.trim().replace(/[<>]/g, '');
};
