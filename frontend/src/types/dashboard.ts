export interface FFScore {
  total: number;
  freshness: number;
  familiarity: number;
  quality: number;
  timestamp: string;
}

export interface ProjectStats {
  audits: number;
  keywords: number;
  pages: number;
  backlinks: number;
}

export interface Project {
  id: string;
  name: string;
  url: string;
  description?: string;
  ffScore?: FFScore;
  tasks?: number;
  lastAudit?: string;
  status?: 'active' | 'paused';
  createdAt?: string;
  stats?: ProjectStats;
}

export interface RecentAudit {
  id: string;
  url: string;
  score: number;
  status: 'completed' | 'pending' | 'running' | 'failed';
}

export interface DashboardStats {
  totalProjects: number;
  activeAudits: number;
  totalKeywords: number;
  totalBacklinks: number;
  pendingTasks: number;
  completedTasks: number;
  avgFFScore: number;
  recentProjects: Project[];
  recentAudits: RecentAudit[];
}

export interface Backlink {
  id: string;
  sourceUrl: string;
  targetUrl: string;
  type: 'dofollow' | 'nofollow' | string;
  domainAuthority?: number;
  anchorText?: string;
  discoveredAt: string;
}

export interface ContentRecommendation {
  title: string;
  description: string;
}

export interface ContentIssue {
  title: string;
  description: string;
}

export interface ContentAnalysis {
  score: number;
  wordCount: number;
  keywordDensity: number;
  uniqueness: number;
  recommendations: ContentRecommendation[];
  issues: ContentIssue[];
}

export interface OptimizedPage {
  id: string;
  url: string;
  keyword: string;
  score: number;
  analyzedAt: string;
}

export interface CreateProjectPayload {
  name: string;
  url: string;
  description?: string;
}

export interface AnalyzeBacklinkPayload {
  url: string;
  projectId?: number | string;
}

export interface AnalyzeContentPayload {
  url: string;
  targetKeyword: string;
  content: string;
  projectId?: number | string;
}
