export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api';

export const ROUTES = {
  HOME: '/',
  LOGIN: '/login',
  REGISTER: '/register',
  DASHBOARD: '/app',
  PROJECTS: '/app/projects',
  PROJECT_DETAIL: '/app/projects/:id',
  AUDIT: '/app/audit',
  KEYWORDS: '/app/keywords',
  CONTENT: '/app/content',
  BACKLINKS: '/app/backlinks',
  SETTINGS: '/app/settings',
};

export const SEO_SCORE_THRESHOLDS = {
  EXCELLENT: 80,
  GOOD: 60,
  FAIR: 40,
  POOR: 0,
};

export const KEYWORD_DIFFICULTY = {
  VERY_EASY: { min: 0, max: 20, label: 'Very easy' },
  EASY: { min: 21, max: 40, label: 'Easy' },
  MEDIUM: { min: 41, max: 60, label: 'Medium' },
  HARD: { min: 61, max: 80, label: 'Hard' },
  VERY_HARD: { min: 81, max: 100, label: 'Very hard' },
};

export const CHART_COLORS = {
  PRIMARY: '#3B82F6',
  SUCCESS: '#10B981',
  WARNING: '#F59E0B',
  DANGER: '#EF4444',
  INFO: '#8B5CF6',
};

export const PAGINATION_LIMIT = 20;
