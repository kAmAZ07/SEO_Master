import api from './axiosConfig'
import type { IntegrationPlatform, ProjectIntegrationStatus, ProjectIntegrationsResponse } from '@/types/integrations'

export interface SaveTildaIntegrationPayload {
  publicKey: string
  secretKey: string
  projectId: string
  pageMappings?: Record<string, string>
}

export interface SaveWordpressIntegrationPayload {
  baseUrl: string
  hmacSecret: string
}

export interface SaveGSCIntegrationPayload {
  propertyUrl: string
  credentialsJson: string
  tokenJson?: string
}

export interface SaveGA4IntegrationPayload {
  propertyId: string
  credentialsJson: string
  tokenJson?: string
}

export interface SaveYandexIntegrationPayload {
  token: string
  userId: string
  hostId: string
}

interface RawIntegrationStatus {
  platform: string
  connected: boolean
  status: string
  hint?: string | null
  connected_at?: string | null
  updated_at?: string | null
  project_identifier?: string | null
  site_url?: string | null
  page_mappings_count?: number | null
  account_identifier?: string | null
  auth_mode?: string | null
  plugin_health?: {
    health_url?: string | null
    status?: string | null
    plugin?: string | null
    version?: string | null
  } | null
}

interface RawIntegrationsResponse {
  project_id: string
  items: RawIntegrationStatus[]
}

const mapIntegrationStatus = (item: RawIntegrationStatus): ProjectIntegrationStatus => ({
  platform: item.platform,
  connected: item.connected,
  status: item.status,
  hint: item.hint ?? null,
  connectedAt: item.connected_at ?? null,
  updatedAt: item.updated_at ?? null,
  projectIdentifier: item.project_identifier ?? null,
  siteUrl: item.site_url ?? null,
  pageMappingsCount: item.page_mappings_count ?? null,
  accountIdentifier: item.account_identifier ?? null,
  authMode: item.auth_mode ?? null,
  pluginHealth: item.plugin_health
    ? {
        healthUrl: item.plugin_health.health_url ?? null,
        status: item.plugin_health.status ?? null,
        plugin: item.plugin_health.plugin ?? null,
        version: item.plugin_health.version ?? null,
      }
    : null,
})

export const fetchProjectIntegrations = async (projectId: string): Promise<ProjectIntegrationsResponse> => {
  const response = await api.get<RawIntegrationsResponse>(`/projects/${projectId}/integrations`)
  return {
    projectId: response.data.project_id,
    items: response.data.items.map(mapIntegrationStatus),
  }
}

export const saveTildaIntegration = async (
  projectId: string,
  payload: SaveTildaIntegrationPayload,
): Promise<ProjectIntegrationStatus> => {
  const response = await api.post<RawIntegrationStatus>(`/projects/${projectId}/integrations/tilda`, payload)
  return mapIntegrationStatus(response.data)
}

export const saveWordpressIntegration = async (
  projectId: string,
  payload: SaveWordpressIntegrationPayload,
): Promise<ProjectIntegrationStatus> => {
  const response = await api.post<RawIntegrationStatus>(`/projects/${projectId}/integrations/wordpress`, payload)
  return mapIntegrationStatus(response.data)
}

export const saveGSCIntegration = async (
  projectId: string,
  payload: SaveGSCIntegrationPayload,
): Promise<ProjectIntegrationStatus> => {
  const response = await api.post<RawIntegrationStatus>(`/projects/${projectId}/integrations/gsc`, payload)
  return mapIntegrationStatus(response.data)
}

export const saveGA4Integration = async (
  projectId: string,
  payload: SaveGA4IntegrationPayload,
): Promise<ProjectIntegrationStatus> => {
  const response = await api.post<RawIntegrationStatus>(`/projects/${projectId}/integrations/ga4`, payload)
  return mapIntegrationStatus(response.data)
}

export const saveYandexIntegration = async (
  projectId: string,
  payload: SaveYandexIntegrationPayload,
): Promise<ProjectIntegrationStatus> => {
  const response = await api.post<RawIntegrationStatus>(`/projects/${projectId}/integrations/yandex`, payload)
  return mapIntegrationStatus(response.data)
}

export const revokeIntegration = async (projectId: string, platform: IntegrationPlatform): Promise<void> => {
  await api.delete(`/projects/${projectId}/integrations/${platform}`)
}
