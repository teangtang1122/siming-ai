import { apiClient } from '../../shared/api/client'
import type { ApiEnvelope } from '../../shared/api/contracts'

export interface GovernanceItem {
  id: string
  title?: string
  description?: string
  status: string
  importance?: string
  priority?: string
  cause?: string
  effect?: string
  strength?: number
  target_chapter_number?: number
  evidence?: string
  created_at?: string
}

export interface NarrativeCheckpoint {
  id: string
  sequence: number
  label: string
  trigger_type: string
  chapter_id?: string
  created_at?: string
}

export interface NarrativeDashboard {
  foreshadowings: GovernanceItem[]
  causal_edges: GovernanceItem[]
  narrative_debts: GovernanceItem[]
  character_states: Array<Record<string, unknown>>
  quality_metrics: Array<Record<string, unknown>>
  checkpoints: NarrativeCheckpoint[]
  counts: {
    open_foreshadowings: number
    open_causal_edges: number
    open_debts: number
    high_risk?: number
  }
}

export async function getNarrativeDashboard(projectId: string, view: string) {
  const response = await apiClient.get<ApiEnvelope<NarrativeDashboard>>(
    `/projects/${projectId}/narrative-governance`,
    { view },
  )
  return response.data.data
}

export async function updateNarrativeStatus(
  projectId: string,
  type: string,
  id: string,
  status: string,
) {
  await apiClient.patch(
    `/projects/${projectId}/narrative-governance/items/${type}/${id}`,
    { status },
  )
}

export async function getNarrativeCheckpointDiff(projectId: string, checkpointId: string) {
  const response = await apiClient.get<ApiEnvelope<Record<string, unknown>>>(
    `/projects/${projectId}/narrative-governance/checkpoints/${checkpointId}/diff`,
  )
  return response.data.data
}

export async function restoreNarrativeCheckpoint(projectId: string, checkpointId: string) {
  await apiClient.post(
    `/projects/${projectId}/narrative-governance/checkpoints/${checkpointId}/restore`,
  )
}
