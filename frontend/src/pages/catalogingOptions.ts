export const catalogingCandidateTypeOptions = [
  { value: 'chapter_summary', label: '章节摘要' },
  { value: 'outline_create', label: '新增大纲' },
  { value: 'outline_update', label: '更新大纲' },
  { value: 'character_create', label: '新增角色' },
  { value: 'character_update', label: '更新角色' },
  { value: 'character_state_update', label: '角色当前状态' },
  { value: 'character_timeline', label: '角色时间线' },
  { value: 'character_relationship', label: '角色关系' },
  { value: 'character_merge_candidate', label: '角色合并候选' },
  { value: 'worldbuilding_create', label: '新增世界观' },
  { value: 'worldbuilding_update', label: '更新世界观' },
  { value: 'worldbuilding_timeline', label: '世界观时间线' },
  { value: 'chapter_link', label: '章节关联' },
]

export const catalogingCandidateStatusOptions = [
  { value: 'all', label: '全部状态' },
  { value: 'pending', label: '待处理' },
  { value: 'edited', label: '已修改' },
  { value: 'approved', label: '已确认' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'apply_failed', label: '写入失败' },
  { value: 'applied', label: '已写入' },
]
