/** Types for external Agent permission settings. */

export interface ExternalAgentSettings {
  id?: string
  project_id: string
  enabled_packs: string[]
  trusted_local_enabled: boolean
  trusted_local_clients: string[]
  require_confirmation_for_writes: boolean
  require_confirmation_for_destructive: boolean
  updated_at?: string | null
}

export interface PermissionPack {
  name: string
  label: string
  description: string
  tools: string[]
  riskLevel: 'safe' | 'low' | 'medium' | 'high' | 'destructive'
  dependsOn?: string[]
}

/** Available permission packs with display info. */
export const PERMISSION_PACKS: PermissionPack[] = [
  {
    name: 'readonly_collaboration',
    label: '只读协作',
    description: '允许外部 Agent 读取作品数据和提示词方法卡，不修改任何数据。',
    tools: ['list_projects', 'get_project_info', 'search_chapters', 'search_characters', 'search_worldbuilding', 'search_outline', 'search_context', 'prepare_external_writing_context'],
    riskLevel: 'safe',
  },
  {
    name: 'project_writing',
    label: '项目写入',
    description: '允许外部 Agent 写入章节、角色、大纲、世界观、外部草稿和外部建档候选。不会调用墨枢内部模型。',
    tools: ['create_chapter', 'update_chapter', 'create_character', 'update_character', 'create_outline_node', 'update_outline_node', 'create_worldbuilding_entry', 'update_worldbuilding_entry', 'save_external_chapter_draft', 'apply_external_story_updates'],
    riskLevel: 'medium',
    dependsOn: ['readonly_collaboration'],
  },
  {
    name: 'project_management',
    label: '项目管理',
    description: '允许外部 Agent 创建作品、导入导出、管理技能和自动任务。默认仍不暴露内部模型工具。',
    tools: ['create_project', 'update_project_info', 'import_file_as_project', 'import_text_as_chapters', 'export_project', 'create_scheduled_task', 'create_skill', 'start_external_cataloging_job'],
    riskLevel: 'high',
    dependsOn: ['readonly_collaboration', 'project_writing'],
  },
  {
    name: 'internal_llm',
    label: '内部模型',
    description: '允许外部 Agent 调用 chapter_writer、start_cataloging_job 等会消耗墨枢内置 API 额度的工具。只有明确需要使用墨枢内部模型时才开启。',
    tools: ['chapter_writer', 'outline_writer', 'character_writer', 'worldbuilding_writer', 'design_plot', 'evaluate_chapter', 'detect_character_changes', 'start_cataloging_job'],
    riskLevel: 'high',
    dependsOn: ['readonly_collaboration', 'project_writing', 'project_management'],
  },
  {
    name: 'trusted_local_maintenance',
    label: '可信本地维护',
    description: '允许外部 Agent 执行删除、合并、重置等危险维护操作。它不自动包含内部模型权限。',
    tools: ['delete_project', 'delete_chapter', 'delete_character', 'merge_duplicate_characters', 'delete_skill'],
    riskLevel: 'destructive',
    dependsOn: ['readonly_collaboration', 'project_writing', 'project_management'],
  },
]

export function getPermissionPackDependencies(packName: string): string[] {
  const pack = PERMISSION_PACKS.find(item => item.name === packName)
  return pack?.dependsOn ?? []
}
