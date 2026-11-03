import api from './axiosConfig'
import type { DashboardStats, Project } from '@/types/dashboard'
import { clearAuditClaimToken, readAuditClaimToken } from './auditClaim'

export interface CreateProjectPayload {
  name: string
  url: string
  description?: string
  sourceAuditId?: string
  claimToken?: string
}

export const fetchProjects = async (): Promise<Project[]> => {
  const response = await api.get('/projects')
  return response.data
}

export const fetchDashboardStats = async (): Promise<DashboardStats> => {
  const response = await api.get('/dashboard/stats')
  return response.data
}

export const fetchProjectDetails = async (projectId: string): Promise<Project> => {
  const response = await api.get(`/projects/${projectId}`)
  return response.data
}

export const createProject = async (payload: CreateProjectPayload): Promise<Project> => {
  const claimToken = payload.claimToken ?? (payload.sourceAuditId ? readAuditClaimToken(payload.sourceAuditId) : undefined)
  const response = await api.post('/projects', claimToken ? { ...payload, claimToken } : payload)
  if (payload.sourceAuditId) {
    clearAuditClaimToken(payload.sourceAuditId)
  }
  return response.data
}

export const deleteProject = async (projectId: string): Promise<void> => {
  await api.delete(`/projects/${projectId}`)
}
