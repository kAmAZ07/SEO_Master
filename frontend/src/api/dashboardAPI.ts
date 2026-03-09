import api from './axiosConfig'
import { Project, DashboardStats } from '@/types/dashboard'

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
