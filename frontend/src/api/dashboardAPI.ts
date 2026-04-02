import api from './axiosConfig'
import {
  AnalyzeBacklinkPayload,
  AnalyzeContentPayload,
  Backlink,
  ContentAnalysis,
  CreateProjectPayload,
  DashboardStats,
  OptimizedPage,
  Project,
  RecentAudit,
} from '@/types/dashboard'


type JsonObject = Record<string, unknown>


const DEFAULT_STATS: DashboardStats = {
  totalProjects: 0,
  activeAudits: 0,
  totalKeywords: 0,
  totalBacklinks: 0,
  pendingTasks: 0,
  completedTasks: 0,
  avgFFScore: 0,
  recentProjects: [],
  recentAudits: [],
}


const isObject = (value: unknown): value is JsonObject =>
  typeof value === 'object' && value !== null


const toObject = (value: unknown): JsonObject => (isObject(value) ? value : {})


const toNumber = (value: unknown, fallback = 0): number => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }

  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}


const toString = (value: unknown, fallback = ''): string => {
  if (typeof value === 'string') {
    return value
  }

  if (value === null || value === undefined) {
    return fallback
  }

  return String(value)
}


const unwrapList = (payload: unknown, keys: string[]): unknown[] => {
  if (Array.isArray(payload)) {
    return payload
  }

  const objectPayload = toObject(payload)

  for (const key of keys) {
    const value = objectPayload[key]
    if (Array.isArray(value)) {
      return value
    }
  }

  return []
}


const normalizeProject = (projectPayload: unknown): Project => {
  const project = toObject(projectPayload)

  return {
    id: toString(project.id),
    name: toString(project.name),
    url: toString(project.url ?? project.domain),
    description: typeof project.description === 'string' ? project.description : undefined,
    ffScore: project.ffScore as Project['ffScore'],
    tasks: typeof project.tasks === 'number' ? project.tasks : undefined,
    lastAudit: typeof project.lastAudit === 'string' ? project.lastAudit : undefined,
    status:
      project.status === 'active' || project.status === 'paused'
        ? project.status
        : undefined,
    createdAt:
      typeof project.createdAt === 'string'
        ? project.createdAt
        : typeof project.created_at === 'string'
          ? project.created_at
          : undefined,
    stats: project.stats as Project['stats'],
  }
}


const normalizeRecentAudit = (auditPayload: unknown): RecentAudit => {
  const audit = toObject(auditPayload)
  const rawStatus = toString(audit.status, 'pending')

  const status: RecentAudit['status'] =
    rawStatus === 'completed' || rawStatus === 'running' || rawStatus === 'failed'
      ? rawStatus
      : 'pending'

  return {
    id: toString(audit.id),
    url: toString(audit.url),
    score: toNumber(audit.score),
    status,
  }
}


const normalizeBacklink = (itemPayload: unknown): Backlink => {
  const item = toObject(itemPayload)

  return {
    id: toString(item.id),
    sourceUrl: toString(item.sourceUrl ?? item.source_url),
    targetUrl: toString(item.targetUrl ?? item.target_url),
    type: toString(item.type, 'nofollow'),
    domainAuthority:
      item.domainAuthority === undefined && item.domain_authority === undefined
        ? undefined
        : toNumber(item.domainAuthority ?? item.domain_authority),
    anchorText:
      typeof item.anchorText === 'string'
        ? item.anchorText
        : typeof item.anchor_text === 'string'
          ? item.anchor_text
          : undefined,
    discoveredAt: toString(item.discoveredAt ?? item.discovered_at, new Date().toISOString()),
  }
}


const normalizeOptimizedPage = (itemPayload: unknown): OptimizedPage => {
  const item = toObject(itemPayload)

  return {
    id: toString(item.id),
    url: toString(item.url),
    keyword: toString(item.keyword ?? item.targetKeyword),
    score: toNumber(item.score),
    analyzedAt: toString(item.analyzedAt ?? item.analyzed_at, new Date().toISOString()),
  }
}


export const fetchProjects = async (): Promise<Project[]> => {
  const response = await api.get('/projects')
  const projects = unwrapList(response.data, ['projects', 'items', 'data'])
  return projects.map(normalizeProject)
}


export const fetchDashboardStats = async (): Promise<DashboardStats> => {
  const response = await api.get('/dashboard/stats')
  const data = toObject(response.data)

  return {
    ...DEFAULT_STATS,
    ...data,
    recentProjects: unwrapList(data, ['recentProjects', 'recent_projects']).map(normalizeProject),
    recentAudits: unwrapList(data, ['recentAudits', 'recent_audits']).map(normalizeRecentAudit),
  }
}


export const fetchProjectDetails = async (projectId: string | number): Promise<Project> => {
  const response = await api.get(`/projects/${projectId}`)
  return normalizeProject(response.data)
}


export const createProject = async (payload: CreateProjectPayload): Promise<Project> => {
  const response = await api.post('/projects', payload)
  return normalizeProject(response.data)
}


export const deleteProject = async (projectId: string | number): Promise<void> => {
  await api.delete(`/projects/${projectId}`)
}


const mapBacklinks = (data: unknown): Backlink[] =>
  unwrapList(data, ['backlinks', 'items', 'data']).map(normalizeBacklink)


export const fetchBacklinks = async (projectId: string | number): Promise<Backlink[]> => {
  try {
    const response = await api.get(`/projects/${projectId}/backlinks`)
    return mapBacklinks(response.data)
  } catch {
    const response = await api.get('/backlinks', { params: { projectId } })
    return mapBacklinks(response.data)
  }
}


export const analyzeBacklink = async (payload: AnalyzeBacklinkPayload): Promise<Backlink[]> => {
  const response = await api.post('/backlinks/analyze', payload)
  const backlinks = mapBacklinks(response.data)

  if (backlinks.length > 0) {
    return backlinks
  }

  return [
    {
      id: `tmp-${Date.now()}`,
      sourceUrl: payload.url,
      targetUrl: '',
      type: 'nofollow',
      discoveredAt: new Date().toISOString(),
    },
  ]
}


const mapOptimizedPages = (data: unknown): OptimizedPage[] =>
  unwrapList(data, ['pages', 'optimizedPages', 'items', 'data']).map(normalizeOptimizedPage)


export const fetchOptimizedPages = async (projectId: string | number): Promise<OptimizedPage[]> => {
  try {
    const response = await api.get(`/projects/${projectId}/content/optimized`)
    return mapOptimizedPages(response.data)
  } catch {
    const response = await api.get('/content/optimized', { params: { projectId } })
    return mapOptimizedPages(response.data)
  }
}


export const analyzeContent = async (payload: AnalyzeContentPayload): Promise<ContentAnalysis> => {
  const response = await api.post('/content/analyze', payload)
  const data = toObject(response.data)

  return {
    score: toNumber(data.score),
    wordCount: toNumber(data.wordCount ?? data.word_count),
    keywordDensity: toNumber(data.keywordDensity ?? data.keyword_density),
    uniqueness: toNumber(data.uniqueness),
    recommendations: unwrapList(data, ['recommendations']).map((item) => {
      const recommendation = toObject(item)
      return {
        title: toString(recommendation.title),
        description: toString(recommendation.description),
      }
    }),
    issues: unwrapList(data, ['issues']).map((item) => {
      const issue = toObject(item)
      return {
        title: toString(issue.title),
        description: toString(issue.description),
      }
    }),
  }
}
