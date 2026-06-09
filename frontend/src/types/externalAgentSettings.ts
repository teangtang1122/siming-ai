/** Types for external Agent permission settings. */

export interface ExternalAgentSettings {
  id?: string
  project_id: string
  enabled_packs: string[]
  trusted_local_enabled: boolean
  trusted_local_clients: string[]
  require_confirmation_for_writes: boolean
  require_confirmation_for_destructive: boolean
}

export interface PermissionPack {
  name: string
  label: string
  description: string
  tools: string[]
  riskLevel: 'safe' | 'low' | 'medium' | 'high' | 'destructive'
}

/** Available permission packs with display info. */
export const PERMISSION_PACKS: PermissionPack[] = [
  {
    name: 'readonly_collaboration',
    label: '只读协作',
    description: '允许外部 Agent 读取项目数据（搜索章节、角色、世界观等）。安全，不修改任何数据。',
    tools: ['list_projects', 'search_chapters', 'search_characters', 'search_worldbuilding', 'search_outline', 'search_context', 'preview_writing_context', 'detect_character_changes', 'evaluate_chapter'],
    riskLevel: 'safe',
  },
  {
    name: 'draft_generation',
    label: '草稿生成',
    description: '允许外部 Agent 使用 AI 生成工具（章节写作、大纲生成等）。生成内容在内存中，不会自动写入数据库。',
    tools: ['chapter_writer', 'outline_writer', 'character_writer', 'worldbuilding_writer', 'rewrite_text', 'expand_text', 'continue_text'],
    riskLevel: 'low',
  },
  {
    name: 'project_writing',
    label: '项目写入',
    description: '允许外部 Agent 创建和更新项目内容（章节、角色、大纲、世界观）。需要用户确认。',
    tools: ['create_chapter', 'update_chapter', 'create_character', 'update_character', 'create_outline_node', 'update_outline_node', 'create_worldbuilding_entry', 'update_worldbuilding_entry'],
    riskLevel: 'medium',
  },
  {
    name: 'project_management',
    label: '项目管理',
    description: '允许外部 Agent 管理项目设置、导入导出、定时任务、技能等。高风险操作。',
    tools: ['create_project', 'update_project_info', 'import_text_as_chapters', 'export_project', 'create_scheduled_task', 'create_skill'],
    riskLevel: 'high',
  },
  {
    name: 'trusted_local_maintenance',
    label: '可信本地维护',
    description: '允许外部 Agent 执行危险的删除和合并操作。仅在可信本地模式下可用。',
    tools: ['delete_project', 'delete_chapter', 'delete_character', 'merge_duplicate_characters'],
    riskLevel: 'destructive',
  },
]
