import api from './axiosConfig'
import { AuditRequest, AuditStatus } from '@/types/audit'

export const submitAuditRequest = async (request: AuditRequest): Promise<AuditStatus> => {
  const response = await api.post('/public/quick-audit', request)
  return response.data
}

export const getAuditStatus = async (uid: string): Promise<AuditStatus> => {
  const response = await api.get(`/public/audit-status/${uid}`)
  return response.data
}
