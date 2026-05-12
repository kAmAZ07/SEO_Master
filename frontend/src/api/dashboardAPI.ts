import api from './axiosConfig'
import type { DashboardStats, Project } from '@/types/dashboard'

export interface CreateProjectPayload {
  name: string
  url: string
  description?: string
  sourceAuditId?: string
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
  const response = await api.post('/projects', payload)
  return response.data
}

export const deleteProject = async (projectId: string): Promise<void> => {
  await api.delete(`/projects/${projectId}`)
}
