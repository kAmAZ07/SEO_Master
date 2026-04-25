export interface PluginHealthStatus {
  healthUrl?: string | null
  status?: string | null
  plugin?: string | null
  version?: string | null
}

export type SupportedIntegrationPlatform = 'tilda' | 'wordpress' | 'gsc' | 'ga4' | 'yandex'
export type IntegrationPlatform = SupportedIntegrationPlatform | string

export interface ProjectIntegrationStatus {
  platform: IntegrationPlatform
  connected: boolean
  status: string
  hint?: string | null
  connectedAt?: string | null
  updatedAt?: string | null
  projectIdentifier?: string | null
  siteUrl?: string | null
  pageMappingsCount?: number | null
  pluginHealth?: PluginHealthStatus | null
  accountIdentifier?: string | null
  authMode?: string | null
}

export interface ProjectIntegrationsResponse {
  projectId: string
  items: ProjectIntegrationStatus[]
}
