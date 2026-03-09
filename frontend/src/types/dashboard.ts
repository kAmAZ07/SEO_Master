export interface FFScore {
  total: number;
  freshness: number;
  familiarity: number;
  quality: number;
  timestamp: string;
}

export interface Project {
  id: string;
  name: string;
  url: string;
  ffScore: FFScore;
  tasks: number;
  lastAudit: string;
  status: 'active' | 'paused';
}

export interface DashboardStats {
  totalProjects: number;
  avgFFScore: number;
  pendingTasks: number;
  completedTasks: number;
}
