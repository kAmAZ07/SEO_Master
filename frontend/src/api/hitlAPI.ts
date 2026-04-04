import api from './axiosConfig'
import { HITLTask, HITLApproval } from '@/types/hitl'

export const fetchHITLTasks = async (): Promise<HITLTask[]> => {
  const response = await api.get('/hitl/tasks')
  return response.data
}

export const approveTask = async (approval: HITLApproval): Promise<void> => {
  await api.post(`/hitl/tasks/${approval.taskId}/approve`, {
    comment: approval.comment,
  })
}

export const rejectTask = async (approval: HITLApproval): Promise<void> => {
  await api.post(`/hitl/tasks/${approval.taskId}/reject`, {
    comment: approval.comment,
  })
}

export const fetchTaskDetails = async (taskId: string): Promise<HITLTask> => {
  const response = await api.get(`/hitl/tasks/${taskId}`)
  return response.data
}
