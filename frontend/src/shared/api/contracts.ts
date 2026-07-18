import type { components } from '../../api/generated/schema'

export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

export type GettingStartedStatus = components['schemas']['GettingStartedStatus']
export type OperationRun = components['schemas']['OperationResponse']
export type OperationListData = components['schemas']['OperationListData']
export type Project = components['schemas']['ProjectResponse']
export type ProjectSummary = components['schemas']['ProjectListItem']
export type ProjectListData = components['schemas']['ProjectListData']
export type ProjectCreateInput = components['schemas']['ProjectCreate']
export type ProjectUpdateInput = components['schemas']['ProjectUpdate']
export type ProjectCreateDraft = Pick<ProjectCreateInput, 'title'>
  & Partial<Omit<ProjectCreateInput, 'title'>>

export function requireData<T>(envelope: { data?: T | null }, label: string): T {
  if (envelope.data == null) {
    throw new Error(`${label}未返回数据`)
  }
  return envelope.data
}
