export interface PluginHealthStatus {
  healthUrl?: string | null
  status?: string | null
  plugin?: string | null
  version?: string | null
}

export interface ProjectIntegrationStatus {
  platform: 'tilda' | 'wordpress' | string
  connected: boolean
  status: string
  hint?: string | null
  connectedAt?: string | null
  updatedAt?: string | null
  projectIdentifier?: string | null
  siteUrl?: string | null
  pageMappingsCount?: number | null
  pluginHealth?: PluginHealthStatus | null
}

export interface ProjectIntegrationsResponse {
  projectId: string
  items: ProjectIntegrationStatus[]
}
