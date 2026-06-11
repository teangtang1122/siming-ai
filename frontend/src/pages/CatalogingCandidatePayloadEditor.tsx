import { Alert, Collapse, Input, Select, Space, Typography } from 'antd'
import type { ReactNode } from 'react'
import type { CatalogingCandidate } from './catalogingTypes'
import { safeStringify } from './catalogingTypes'

const { Text } = Typography
const { TextArea } = Input

interface CatalogingCandidatePayloadEditorProps {
  candidate: CatalogingCandidate
  draft: string
  disabled?: boolean
  onDraftChange: (candidateId: string, value: string) => void
}

const ROLE_OPTIONS = [
  { value: 'protagonist', label: '主角' },
  { value: 'supporting', label: '配角' },
  { value: 'antagonist', label: '反派' },
  { value: 'mentor', label: '导师' },
  { value: 'other', label: '其他' },
]

const OUTLINE_TYPE_OPTIONS = [
  { value: 'chapter', label: '章节' },
  { value: 'section', label: '场景/小节' },
  { value: 'volume', label: '卷' },
]

const STATUS_OPTIONS = [
  { value: 'planned', label: '计划中' },
  { value: 'completed', label: '已完成' },
]

const WORLD_DIMENSION_OPTIONS = [
  { value: 'geography', label: '地点/地理' },
  { value: 'history', label: '历史/传说' },
  { value: 'factions', label: '势力/组织' },
  { value: 'power_system', label: '修炼/规则' },
  { value: 'races', label: '种族' },
  { value: 'culture', label: '文化/制度' },
]

function CatalogingCandidatePayloadEditor({
  candidate,
  draft,
  disabled,
  onDraftChange,
}: CatalogingCandidatePayloadEditorProps) {
  const { payload, parseError } = parsePayload(draft, candidate.payload)

  const updatePayload = (key: string, value: unknown) => {
    onDraftChange(candidate.id, safeStringify({ ...payload, [key]: value }))
  }

  const rawEditor = (
    <TextArea
      value={draft}
      autoSize={{ minRows: 4, maxRows: 12 }}
      onChange={(event) => onDraftChange(candidate.id, event.target.value)}
      disabled={disabled}
    />
  )

  if (parseError) {
    return (
      <Space direction="vertical" style={{ width: '100%' }}>
        <Alert type="warning" showIcon message="当前候选项 JSON 暂时无法解析，请先修正格式。" />
        {rawEditor}
      </Space>
    )
  }

  const editor = renderEditor(candidate.item_type, payload, updatePayload, disabled)

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      {editor || <Alert type="info" showIcon message="这个候选类型暂未配置专用表单，可以在高级 JSON 中修改。" />}
      <Collapse
        size="small"
        items={[{
          key: 'json',
          label: '高级 JSON',
          children: rawEditor,
        }]}
      />
    </Space>
  )
}

function renderEditor(
  type: string,
  payload: Record<string, unknown>,
  updatePayload: (key: string, value: unknown) => void,
  disabled?: boolean,
) {
  if (['character_create', 'character_update'].includes(type)) {
    return (
      <Space direction="vertical" style={{ width: '100%' }}>
        <Field label="角色名"><Input value={str(payload.name)} disabled={disabled} onChange={(event) => updatePayload('name', event.target.value)} /></Field>
        <Field label="别名/称呼"><Select mode="tags" value={arr(payload.aliases)} disabled={disabled} tokenSeparators={[',', '，', '/', '、']} onChange={(value) => updatePayload('aliases', value)} /></Field>
        <Field label="角色类型"><Select allowClear options={ROLE_OPTIONS} value={optionalStr(payload.role_type)} disabled={disabled} onChange={(value) => updatePayload('role_type', value)} /></Field>
        <Field label="年龄/时间状态"><Input value={str(payload.age)} disabled={disabled} onChange={(event) => updatePayload('age', event.target.value)} /></Field>
        <Field label="外貌"><TextArea rows={3} value={str(payload.appearance)} disabled={disabled} onChange={(event) => updatePayload('appearance', event.target.value)} /></Field>
        <Field label="性格"><TextArea rows={3} value={str(payload.personality)} disabled={disabled} onChange={(event) => updatePayload('personality', event.target.value)} /></Field>
        <Field label="背景故事"><TextArea rows={4} value={str(payload.background)} disabled={disabled} onChange={(event) => updatePayload('background', event.target.value)} /></Field>
        <Field label="能力"><Select mode="tags" value={arr(payload.abilities)} disabled={disabled} tokenSeparators={[',', '，']} onChange={(value) => updatePayload('abilities', value)} /></Field>
        <Field label="角色扮演提示词"><TextArea rows={4} value={str(payload.custom_system_prompt)} disabled={disabled} onChange={(event) => updatePayload('custom_system_prompt', event.target.value)} /></Field>
      </Space>
    )
  }

  if (type === 'character_state_update') {
    return (
      <Space direction="vertical" style={{ width: '100%' }}>
        <Field label="角色名"><Input value={str(payload.name)} disabled={disabled} onChange={(event) => updatePayload('name', event.target.value)} /></Field>
        <Field label="别名/称呼"><Select mode="tags" value={arr(payload.aliases)} disabled={disabled} tokenSeparators={[',', '，', '/', '、']} onChange={(value) => updatePayload('aliases', value)} /></Field>
        <Field label="年龄/时间状态"><Input value={str(payload.age)} disabled={disabled} onChange={(event) => updatePayload('age', event.target.value)} /></Field>
        <Field label="生死状态"><Input value={str(payload.life_status)} disabled={disabled} onChange={(event) => updatePayload('life_status', event.target.value)} /></Field>
        <Field label="当前位置"><Input value={str(payload.current_location)} disabled={disabled} onChange={(event) => updatePayload('current_location', event.target.value)} /></Field>
        <Field label="境界/等级"><Input value={str(payload.realm_or_level)} disabled={disabled} onChange={(event) => updatePayload('realm_or_level', event.target.value)} /></Field>
        <Field label="身体状态"><TextArea rows={3} value={str(payload.physical_state)} disabled={disabled} onChange={(event) => updatePayload('physical_state', event.target.value)} /></Field>
        <Field label="心理状态"><TextArea rows={3} value={str(payload.mental_state)} disabled={disabled} onChange={(event) => updatePayload('mental_state', event.target.value)} /></Field>
        <Field label="当前目标"><TextArea rows={3} value={str(payload.current_goal)} disabled={disabled} onChange={(event) => updatePayload('current_goal', event.target.value)} /></Field>
        <Field label="当前冲突"><TextArea rows={3} value={str(payload.active_conflict)} disabled={disabled} onChange={(event) => updatePayload('active_conflict', event.target.value)} /></Field>
        <Field label="能力状态"><TextArea rows={3} value={str(payload.abilities_state)} disabled={disabled} onChange={(event) => updatePayload('abilities_state', event.target.value)} /></Field>
        <Field label="持有物/资源"><TextArea rows={3} value={str(payload.items_or_assets)} disabled={disabled} onChange={(event) => updatePayload('items_or_assets', event.target.value)} /></Field>
      </Space>
    )
  }

  if (['outline_create', 'outline_update'].includes(type)) {
    return (
      <Space direction="vertical" style={{ width: '100%' }}>
        <Field label="标题"><Input value={str(payload.title)} disabled={disabled} onChange={(event) => updatePayload('title', event.target.value)} /></Field>
        <Field label="节点类型"><Select options={OUTLINE_TYPE_OPTIONS} value={optionalStr(payload.node_type)} disabled={disabled} onChange={(value) => updatePayload('node_type', value)} /></Field>
        <Field label="父节点标题"><Input value={str(payload.parent_title)} disabled={disabled} onChange={(event) => updatePayload('parent_title', event.target.value)} /></Field>
        <Field label="状态"><Select allowClear options={STATUS_OPTIONS} value={optionalStr(payload.status)} disabled={disabled} onChange={(value) => updatePayload('status', value)} /></Field>
        <Field label="摘要"><TextArea rows={4} value={str(payload.summary)} disabled={disabled} onChange={(event) => updatePayload('summary', event.target.value)} /></Field>
        <Field label="实际内容"><TextArea rows={3} value={str(payload.actual_summary)} disabled={disabled} onChange={(event) => updatePayload('actual_summary', event.target.value)} /></Field>
        <Field label="关联角色"><Select mode="tags" value={arr(payload.related_characters)} disabled={disabled} tokenSeparators={[',', '，', '、']} onChange={(value) => updatePayload('related_characters', value)} /></Field>
      </Space>
    )
  }

  if (['worldbuilding_create', 'worldbuilding_update'].includes(type)) {
    return (
      <Space direction="vertical" style={{ width: '100%' }}>
        <Field label="标题"><Input value={str(payload.title)} disabled={disabled} onChange={(event) => updatePayload('title', event.target.value)} /></Field>
        <Field label="分类"><Select options={WORLD_DIMENSION_OPTIONS} value={optionalStr(payload.dimension)} disabled={disabled} onChange={(value) => updatePayload('dimension', value)} /></Field>
        <Field label="内容"><TextArea rows={5} value={str(payload.content)} disabled={disabled} onChange={(event) => updatePayload('content', event.target.value)} /></Field>
      </Space>
    )
  }

  if (type === 'chapter_summary') {
    return (
      <Space direction="vertical" style={{ width: '100%' }}>
        <Field label="章节摘要"><TextArea rows={5} value={str(payload.summary_text)} disabled={disabled} onChange={(event) => updatePayload('summary_text', event.target.value)} /></Field>
        <Field label="关键事件"><Select mode="tags" value={arr(payload.key_events)} disabled={disabled} tokenSeparators={[',', '，']} onChange={(value) => updatePayload('key_events', value)} /></Field>
        <Field label="涉及角色"><Select mode="tags" value={arr(payload.characters)} disabled={disabled} tokenSeparators={[',', '，', '、']} onChange={(value) => updatePayload('characters', value)} /></Field>
        <Field label="涉及设定"><Select mode="tags" value={arr(payload.worldbuilding)} disabled={disabled} tokenSeparators={[',', '，', '、']} onChange={(value) => updatePayload('worldbuilding', value)} /></Field>
        <Field label="大纲提示"><TextArea rows={3} value={str(payload.outline_hint)} disabled={disabled} onChange={(event) => updatePayload('outline_hint', event.target.value)} /></Field>
      </Space>
    )
  }

  if (type === 'character_relationship') {
    return (
      <Space direction="vertical" style={{ width: '100%' }}>
        <Field label="角色 A"><Input value={str(payload.source_name)} disabled={disabled} onChange={(event) => updatePayload('source_name', event.target.value)} /></Field>
        <Field label="角色 B"><Input value={str(payload.target_name)} disabled={disabled} onChange={(event) => updatePayload('target_name', event.target.value)} /></Field>
        <Field label="关系类型"><Input value={str(payload.relationship_type)} disabled={disabled} onChange={(event) => updatePayload('relationship_type', event.target.value)} /></Field>
        <Field label="关系描述"><TextArea rows={3} value={str(payload.description)} disabled={disabled} onChange={(event) => updatePayload('description', event.target.value)} /></Field>
      </Space>
    )
  }

  if (type === 'character_timeline') {
    return (
      <Space direction="vertical" style={{ width: '100%' }}>
        <Field label="角色名"><Input value={str(payload.name)} disabled={disabled} onChange={(event) => updatePayload('name', event.target.value)} /></Field>
        <Field label="事件类型"><Input value={str(payload.event_type)} disabled={disabled} onChange={(event) => updatePayload('event_type', event.target.value)} /></Field>
        <Field label="事件描述"><TextArea rows={4} value={str(payload.event_description)} disabled={disabled} onChange={(event) => updatePayload('event_description', event.target.value)} /></Field>
        <Field label="情绪/状态变化"><TextArea rows={3} value={str(payload.emotional_state_change)} disabled={disabled} onChange={(event) => updatePayload('emotional_state_change', event.target.value)} /></Field>
      </Space>
    )
  }

  if (type === 'chapter_link') {
    return (
      <Space direction="vertical" style={{ width: '100%' }}>
        <Field label="角色"><Select mode="tags" value={arr(payload.character_names)} disabled={disabled} tokenSeparators={[',', '，', '、']} onChange={(value) => updatePayload('character_names', value)} /></Field>
        <Field label="世界观"><Select mode="tags" value={arr(payload.worldbuilding_titles)} disabled={disabled} tokenSeparators={[',', '，', '、']} onChange={(value) => updatePayload('worldbuilding_titles', value)} /></Field>
        <Field label="大纲标题"><Input value={str(payload.outline_title)} disabled={disabled} onChange={(event) => updatePayload('outline_title', event.target.value)} /></Field>
        <Field label="说明"><TextArea rows={3} value={str(payload.description)} disabled={disabled} onChange={(event) => updatePayload('description', event.target.value)} /></Field>
      </Space>
    )
  }

  return null
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Text type="secondary">{label}</Text>
      <div style={{ marginTop: 4 }}>{children}</div>
    </div>
  )
}

function parsePayload(draft: string, fallback: Record<string, unknown>) {
  try {
    const parsed = JSON.parse(draft || '{}')
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return { payload: parsed as Record<string, unknown>, parseError: false }
    }
    return { payload: fallback || {}, parseError: true }
  } catch {
    return { payload: fallback || {}, parseError: true }
  }
}

function str(value: unknown) {
  return value == null ? '' : String(value)
}

function optionalStr(value: unknown) {
  return value == null || value === '' ? undefined : String(value)
}

function arr(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => String(item))
  if (typeof value === 'string' && value.trim()) return value.split(/[,，、/]/).map((item) => item.trim()).filter(Boolean)
  return []
}

export default CatalogingCandidatePayloadEditor
