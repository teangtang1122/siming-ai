import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Collapse, Descriptions, Divider, Empty, Form, Input, InputNumber, List, Modal,
  Popconfirm, Select, Space, Tag, Timeline, Typography, message,
} from 'antd'
import {
  DeleteOutlined, HistoryOutlined, PlusOutlined,
  SaveOutlined, SettingOutlined, TeamOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { useAiPanelContext } from '../contexts/AiPanelContext'
import { useModelOptions } from '../hooks/useModelOptions'
import { useUnsavedGuard } from '../hooks/useUnsavedGuard'
import './CharactersPage.css'

const { Text, Title } = Typography

interface ApiResponse<T> { code: number; message: string; data: T }

interface Character {
  id: string; project_id: string; name: string; appearance?: string
  personality?: string; background?: string; abilities: string[]; aliases?: string[]
  role_type?: string; age?: string; current_version: number; is_evolution_tracked: boolean
  life_status?: string; current_location?: string; realm_or_level?: string
  physical_state?: string; mental_state?: string; current_goal?: string
  active_conflict?: string; abilities_state?: string; items_or_assets?: string
  profile?: CharacterWritingProfile
  created_at: string; updated_at: string
  appearances?: {
    outline_nodes: Array<{ id: string; title: string; node_type: string; role_in_scene?: string }>
    chapters: Array<{ id: string; title: string; appearance_type: string; description?: string }>
  }
}

interface RelationshipEdge { id: string; from: string; to: string; relationship_type: string; description?: string }
interface RelationshipNetwork { nodes: Array<{ id: string; name: string; role_type?: string }>; edges: RelationshipEdge[]; total: number }
interface VersionItem { id: string; character_id: string; version_number: number; change_summary?: string; created_at: string }
interface DuplicateCandidate {
  primary: { id: string; name: string; aliases?: string[]; role_type?: string; current_version?: number }
  secondary: { id: string; name: string; aliases?: string[]; role_type?: string; current_version?: number }
  canonical_name: string
  aliases: string[]
  score: number
  reasons: string[]
}
interface MergePreview {
  primary: Character
  secondary: Character
  canonical_name: string
  aliases: string[]
  reason: string
  stats: Record<string, number>
  merged_preview: { name: string; aliases: string[]; background?: string; appearance?: string; personality?: string; abilities?: string[] }
}

interface AIConfig {
  id?: string; character_id?: string; tone_style: string; catchphrases: string[]
  verbosity: string; emotion_tendency: string; model_override?: string
  custom_system_prompt?: string; created_at?: string; updated_at?: string
}

interface CharacterFormValues {
  name: string; role_type?: string; age?: string; appearance?: string; personality?: string
  background?: string; abilities?: string[]; aliases?: string[]; is_evolution_tracked?: boolean
  life_status?: string; current_location?: string; realm_or_level?: string
  physical_state?: string; mental_state?: string; current_goal?: string
  active_conflict?: string; abilities_state?: string; items_or_assets?: string
  profile?: CharacterWritingProfile
}

interface CharacterWritingProfile {
  core_motivation?: string; inner_lack?: string; core_belief?: string
  public_persona?: string; hidden_persona?: string; reveal_chapter?: number
  moral_taboo?: string; voice?: string; action_habit?: string; trauma_trigger?: string
}

interface CharacterMergeFormValues {
  primary_id: string
  secondary_id: string
  canonical_name?: string
  aliases?: string[]
  confidence_reason?: string
  background_append?: string
}

interface CharactersPageProps { projectId: string }

const ROLE_OPTIONS = [
  { value: 'protagonist', label: '主角', desc: '故事核心人物，视角承载者' },
  { value: 'supporting', label: '配角', desc: '协助或衬托主角的重要角色' },
  { value: 'antagonist', label: '反派', desc: '制造冲突与阻碍的对立面' },
  { value: 'mentor', label: '导师', desc: '引导主角成长的师长或前辈' },
  { value: 'other', label: '其他', desc: '路人、工具人、背景角色等' },
]

const TONE_OPTIONS = [
  { value: 'neutral', label: '中性' }, { value: 'arrogant', label: '傲慢' },
  { value: 'gentle', label: '温和' }, { value: 'cold', label: '冷酷' },
  { value: 'enthusiastic', label: '热情' }, { value: 'mysterious', label: '神秘' },
  { value: 'sarcastic', label: '讽刺' }, { value: 'formal', label: '正式' },
  { value: 'casual', label: '随意' }, { value: 'aggressive', label: '好斗' },
]

const VERBOSITY_OPTIONS = [
  { value: 'brief', label: '简短' }, { value: 'moderate', label: '适中' }, { value: 'verbose', label: '话多' },
]

const EMOTION_OPTIONS = [
  { value: 'neutral', label: '中性' }, { value: 'optimistic', label: '乐观' },
  { value: 'pessimistic', label: '悲观' }, { value: 'angry', label: '愤怒' },
  { value: 'calm', label: '冷静' }, { value: 'anxious', label: '焦虑' },
  { value: 'sad', label: '悲伤' }, { value: 'excited', label: '兴奋' },
]

function CharactersPage({ projectId }: CharactersPageProps) {
  const [form] = Form.useForm<CharacterFormValues>()
  const [relationshipForm] = Form.useForm<{ target_character_id: string; relationship_type: string; description?: string }>()
  const [aiConfigForm] = Form.useForm<AIConfig>()
  const [mergeForm] = Form.useForm<CharacterMergeFormValues>()

  const [characters, setCharacters] = useState<Character[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedDetail, setSelectedDetail] = useState<Character | null>(null)
  const [keyword, setKeyword] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [network, setNetwork] = useState<RelationshipNetwork>({ nodes: [], edges: [], total: 0 })
  const [versions, setVersions] = useState<VersionItem[]>([])
  const [versionModalOpen, setVersionModalOpen] = useState(false)
  const [versionSnapshot, setVersionSnapshot] = useState<Record<string, unknown> | null>(null)
  const [, setAiConfig] = useState<AIConfig | null>(null)
  const [aiConfigSaving, setAiConfigSaving] = useState(false)
  const [duplicateModalOpen, setDuplicateModalOpen] = useState(false)
  const [duplicates, setDuplicates] = useState<DuplicateCandidate[]>([])
  const [duplicatesLoading, setDuplicatesLoading] = useState(false)
  const [mergeModalOpen, setMergeModalOpen] = useState(false)
  const [mergePreview, setMergePreview] = useState<MergePreview | null>(null)
  const [mergePreviewLoading, setMergePreviewLoading] = useState(false)
  const [mergeApplying, setMergeApplying] = useState(false)
  const { setAiContext, refreshKey } = useAiPanelContext()
  const { modelOptions, loading: modelsLoading } = useModelOptions()
  const { markDirty, markSaved, confirmLeave } = useUnsavedGuard()

  const fetchCharacters = useCallback(async (q?: string) => {
    setLoading(true)
    try {
      const params = q ? { q } : undefined
      const res = await apiClient.get<ApiResponse<{ items: Character[]; total: number }>>(`/projects/${projectId}/characters`, params)
      setCharacters(res.data.data.items)
      if (!selectedId && res.data.data.items.length > 0) setSelectedId(res.data.data.items[0].id)
    } catch (err: any) { message.error(err.message || '获取角色列表失败') }
    finally { setLoading(false) }
  }, [projectId, selectedId])

  const fetchNetwork = useCallback(async () => {
    try {
      const res = await apiClient.get<ApiResponse<RelationshipNetwork>>(`/projects/${projectId}/characters/relationships`)
      setNetwork(res.data.data)
    } catch { /* ignore */ }
  }, [projectId])

  const fetchDetail = useCallback(async (characterId: string) => {
    try {
      const res = await apiClient.get<ApiResponse<Character>>(`/projects/${projectId}/characters/${characterId}`)
      setSelectedDetail(res.data.data)
      form.setFieldsValue({
        name: res.data.data.name, role_type: res.data.data.role_type, age: res.data.data.age,
        appearance: res.data.data.appearance, personality: res.data.data.personality,
        background: res.data.data.background, abilities: res.data.data.abilities || [],
        aliases: res.data.data.aliases || [],
        life_status: res.data.data.life_status,
        current_location: res.data.data.current_location,
        realm_or_level: res.data.data.realm_or_level,
        physical_state: res.data.data.physical_state,
        mental_state: res.data.data.mental_state,
        current_goal: res.data.data.current_goal,
        active_conflict: res.data.data.active_conflict,
        abilities_state: res.data.data.abilities_state,
        items_or_assets: res.data.data.items_or_assets,
        profile: res.data.data.profile,
        is_evolution_tracked: res.data.data.is_evolution_tracked,
      })
    } catch (err: any) { message.error(err.message || '获取角色详情失败') }
  }, [form, projectId])

  const fetchVersions = useCallback(async (characterId: string) => {
    try {
      const res = await apiClient.get<ApiResponse<{ items: VersionItem[]; total: number }>>(`/projects/${projectId}/characters/${characterId}/versions`)
      setVersions(res.data.data.items)
    } catch { /* ignore */ }
  }, [projectId])

  const fetchAIConfig = useCallback(async (characterId: string) => {
    try {
      const res = await apiClient.get<ApiResponse<AIConfig>>(`/projects/${projectId}/characters/${characterId}/ai-config`)
      const cfg = res.data.data
      setAiConfig(cfg)
      aiConfigForm.setFieldsValue(cfg)
    } catch { setAiConfig(null); aiConfigForm.resetFields() }
  }, [aiConfigForm, projectId])

  useEffect(() => { fetchCharacters(); fetchNetwork() }, [fetchCharacters, fetchNetwork])

  // Sync character selection to AI context
  useEffect(() => {
    setAiContext({ selectedCharacterId: selectedId })
  }, [selectedId, setAiContext])

  // Refresh data when AI applies changes
  useEffect(() => {
    if (refreshKey > 0) {
      fetchCharacters(keyword)
      fetchNetwork()
      if (selectedId) {
        fetchDetail(selectedId)
        fetchVersions(selectedId)
        fetchAIConfig(selectedId)
      }
    }
  }, [refreshKey])

  useEffect(() => {
    if (selectedId) { fetchDetail(selectedId); fetchVersions(selectedId); fetchAIConfig(selectedId) }
    else { setSelectedDetail(null); setVersions([]); setAiConfig(null); form.resetFields(); aiConfigForm.resetFields() }
  }, [fetchDetail, fetchVersions, fetchAIConfig, form, aiConfigForm, selectedId])

  const selectedRelationships = useMemo(() => {
    if (!selectedId) return []
    return network.edges.filter((edge) => edge.from === selectedId || edge.to === selectedId)
  }, [network.edges, selectedId])

  const startCreate = () => {
    confirmLeave(() => {
      setSelectedId(null); setSelectedDetail(null); setVersions([]); setAiConfig(null)
      form.resetFields(); aiConfigForm.resetFields()
      form.setFieldsValue({ abilities: [], is_evolution_tracked: true, role_type: 'supporting' })
    })
  }

  const saveCharacter = async (values: CharacterFormValues) => {
    setSaving(true)
    try {
      const payload = {
        ...values,
        abilities: values.abilities || [],
        aliases: values.aliases || [],
        is_evolution_tracked: values.is_evolution_tracked ?? true,
      }
      if (selectedId) {
        const res = await apiClient.put<ApiResponse<Character>>(`/projects/${projectId}/characters/${selectedId}`, { ...payload, change_summary: '前端手动保存角色档案' })
        setSelectedId(res.data.data.id); message.success('角色已保存')
      } else {
        const res = await apiClient.post<ApiResponse<Character>>(`/projects/${projectId}/characters`, payload)
        setSelectedId(res.data.data.id); message.success('角色已创建')
      }
      markSaved()
      fetchCharacters(keyword); fetchNetwork()
    } catch (err: any) { message.error(err.message || '保存角色失败') }
    finally { setSaving(false) }
  }

  const saveAIConfig = async () => {
    if (!selectedId) return
    setAiConfigSaving(true)
    try {
      const values = aiConfigForm.getFieldsValue()
      const payload = {
        tone_style: values.tone_style || 'neutral',
        catchphrases: values.catchphrases || [],
        verbosity: values.verbosity || 'moderate',
        emotion_tendency: values.emotion_tendency || 'neutral',
        model_override: values.model_override || null,
        custom_system_prompt: values.custom_system_prompt || null,
      }
      const res = await apiClient.put<ApiResponse<AIConfig>>(`/projects/${projectId}/characters/${selectedId}/ai-config`, payload)
      setAiConfig(res.data.data)
      message.success('AI对话配置已保存')
    } catch (err: any) { message.error(err.message || '保存AI配置失败') }
    finally { setAiConfigSaving(false) }
  }

  const deleteCharacter = async () => {
    if (!selectedId) return
    try {
      await apiClient.delete(`/projects/${projectId}/characters/${selectedId}`)
      message.success('角色已删除'); setSelectedId(null); setSelectedDetail(null)
      fetchCharacters(keyword); fetchNetwork()
    } catch (err: any) { message.error(err.message || '删除角色失败') }
  }

  const addRelationship = async (values: { target_character_id: string; relationship_type: string; description?: string }) => {
    if (!selectedId) return
    const existing = selectedRelationships.filter((edge) => edge.from !== values.target_character_id && edge.to !== values.target_character_id)
      .map((edge) => ({ target_character_id: edge.from === selectedId ? edge.to : edge.from, relationship_type: edge.relationship_type, description: edge.description }))
    try {
      const res = await apiClient.put<ApiResponse<RelationshipNetwork>>(`/projects/${projectId}/characters/${selectedId}/relationships`, {
        relationships: [...existing, { target_character_id: values.target_character_id, relationship_type: values.relationship_type, description: values.description }],
      })
      setNetwork(res.data.data); relationshipForm.resetFields(); message.success('关系已保存')
    } catch (err: any) { message.error(err.message || '保存关系失败') }
  }

  const removeRelationship = async (edgeId: string) => {
    if (!selectedId) return
    const remaining = selectedRelationships.filter((edge) => edge.id !== edgeId)
      .map((edge) => ({ target_character_id: edge.from === selectedId ? edge.to : edge.from, relationship_type: edge.relationship_type, description: edge.description }))
    try {
      const res = await apiClient.put<ApiResponse<RelationshipNetwork>>(`/projects/${projectId}/characters/${selectedId}/relationships`, { relationships: remaining })
      setNetwork(res.data.data); message.success('关系已移除')
    } catch (err: any) { message.error(err.message || '移除关系失败') }
  }

  const openVersion = async (version: VersionItem) => {
    if (!selectedId) return
    try {
      const res = await apiClient.get<ApiResponse<VersionItem & { snapshot_data: Record<string, unknown> }>>(`/projects/${projectId}/characters/${selectedId}/versions/${version.id}`)
      setVersionSnapshot(res.data.data.snapshot_data); setVersionModalOpen(true)
    } catch (err: any) { message.error(err.message || '获取版本详情失败') }
  }

  const characterName = (id: string) => characters.find((item) => item.id === id)?.name || id.slice(0, 8)

  const fetchDuplicates = async () => {
    setDuplicatesLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<{ items: DuplicateCandidate[] }>>(`/projects/${projectId}/characters/duplicates`)
      setDuplicates(res.data.data.items)
      setDuplicateModalOpen(true)
    } catch (err: any) { message.error(err.message || '查找重复角色失败') }
    finally { setDuplicatesLoading(false) }
  }

  const openMergeModal = async (values?: Partial<CharacterMergeFormValues>) => {
    const nextValues = {
      primary_id: values?.primary_id || selectedId || characters[0]?.id,
      secondary_id: values?.secondary_id || characters.find((item) => item.id !== (values?.primary_id || selectedId))?.id,
      canonical_name: values?.canonical_name,
      aliases: values?.aliases || [],
      confidence_reason: values?.confidence_reason || '用户确认的重复角色合并',
      background_append: values?.background_append,
    }
    mergeForm.setFieldsValue(nextValues)
    setMergeModalOpen(true)
    if (nextValues.primary_id && nextValues.secondary_id) await refreshMergePreview(nextValues as CharacterMergeFormValues)
  }

  const openMergeFromDuplicate = async (item: DuplicateCandidate) => {
    await openMergeModal({
      primary_id: item.primary.id,
      secondary_id: item.secondary.id,
      canonical_name: item.canonical_name,
      aliases: item.aliases,
      confidence_reason: item.reasons.join('；') || '疑似重复角色',
    })
  }

  const refreshMergePreview = async (values?: CharacterMergeFormValues) => {
    const payload = values || mergeForm.getFieldsValue()
    if (!payload.primary_id || !payload.secondary_id) {
      message.warning('请选择主角色和被合并角色')
      return
    }
    if (payload.primary_id === payload.secondary_id) {
      message.warning('主角色和被合并角色不能相同')
      return
    }
    setMergePreviewLoading(true)
    try {
      const res = await apiClient.post<ApiResponse<MergePreview>>(`/projects/${projectId}/characters/merge-preview`, {
        ...payload,
        aliases: payload.aliases || [],
      })
      setMergePreview(res.data.data)
      if (!payload.canonical_name) {
        mergeForm.setFieldValue('canonical_name', res.data.data.canonical_name)
      }
      if (!payload.aliases || payload.aliases.length === 0) {
        mergeForm.setFieldValue('aliases', res.data.data.aliases)
      }
    } catch (err: any) { message.error(err.message || '生成合并预览失败') }
    finally { setMergePreviewLoading(false) }
  }

  const applyMerge = async () => {
    const payload = mergeForm.getFieldsValue()
    if (!payload.primary_id || !payload.secondary_id || payload.primary_id === payload.secondary_id) {
      message.warning('请选择两张不同的角色卡')
      return
    }
    setMergeApplying(true)
    try {
      await apiClient.post<ApiResponse<unknown>>(`/projects/${projectId}/characters/merge`, {
        ...payload,
        aliases: payload.aliases || [],
      })
      message.success('角色已合并')
      setMergeModalOpen(false)
      setDuplicateModalOpen(false)
      setMergePreview(null)
      setSelectedId(payload.primary_id)
      fetchCharacters(keyword)
      fetchNetwork()
      fetchVersions(payload.primary_id)
      fetchDetail(payload.primary_id)
    } catch (err: any) { message.error(err.message || '合并角色失败') }
    finally { setMergeApplying(false) }
  }

  return (
    <div className="characters-page">
      <div className="characters-shell">
        <aside className="characters-sidebar">
          <div className="characters-sidebar-toolbar">
            <div className="characters-sidebar-head">
              <Title level={4} style={{ margin: 0 }}><TeamOutlined /> 角色</Title>
              <Space>
                <Button onClick={fetchDuplicates} loading={duplicatesLoading}>查重</Button>
                <Button type="primary" icon={<PlusOutlined />} onClick={startCreate} />
              </Space>
            </div>
            <Input.Search placeholder="搜索角色" allowClear value={keyword}
              onChange={(event) => setKeyword(event.target.value)} onSearch={(value) => fetchCharacters(value)} />
          </div>
          <div className="characters-list-scroll">
            <List loading={loading} dataSource={characters}
              locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无角色" /> }}
              renderItem={(item) => (
                <List.Item className={`characters-list-item${item.id === selectedId ? ' characters-list-item-active' : ''}`}
                  onClick={() => confirmLeave(() => setSelectedId(item.id))}>
                  <List.Item.Meta title={<span className="characters-name-text" title={item.name}>{item.name}</span>}
                    description={<Space size={6} wrap>
                      <Tag>{item.role_type || '未分类'}</Tag>
                      <Text type="secondary">v{item.current_version}</Text>
                      {(item.aliases || []).slice(0, 2).map((alias) => <Tag key={alias} color="blue">{alias}</Tag>)}
                    </Space>} />
                </List.Item>
              )} />
          </div>
        </aside>

        <main className="characters-editor">
          <div className="characters-editor-head">
            <div>
              <Title level={4} style={{ margin: 0 }}>{selectedDetail ? selectedDetail.name : '新角色'}</Title>
              {selectedDetail && <Text type="secondary">当前版本 v{selectedDetail.current_version}</Text>}
              {selectedDetail && selectedDetail.aliases && selectedDetail.aliases.length > 0 && (
                <div className="characters-alias-row">
                  {selectedDetail.aliases.map((alias) => <Tag key={alias} color="blue">{alias}</Tag>)}
                </div>
              )}
            </div>
            <Space>
              {selectedId && (
                <Button danger icon={<DeleteOutlined />} onClick={() => {
                  Modal.confirm({
                    title: '确认删除角色',
                    content: '该角色的关系、版本和出场记录也会被删除，此操作不可恢复。',
                    okText: '删除',
                    cancelText: '取消',
                    okButtonProps: { danger: true },
                    onOk: deleteCharacter,
                  })
                }}>删除</Button>
              )}
              <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => form.submit()}>保存角色</Button>
            </Space>
          </div>

          <Form form={form} layout="vertical" onFinish={saveCharacter} onValuesChange={markDirty}>
            <div className="characters-two-col">
              <Form.Item name="name" label="姓名" rules={[{ required: true, message: '请输入角色姓名' }]}>
                <Input placeholder="角色姓名" maxLength={100} />
              </Form.Item>
              <Form.Item name="role_type" label="角色类型">
                <Select
                  options={ROLE_OPTIONS}
                  placeholder="选择角色类型"
                  optionRender={(option) => (
                    <div>
                      <div>{option.label}</div>
                      {option.data.desc && <Text type="secondary" style={{ fontSize: 12 }}>{option.data.desc}</Text>}
                    </div>
                  )}
                />
              </Form.Item>
            </div>
            <Form.Item name="aliases" label="别名/称呼">
              <Select mode="tags" tokenSeparators={[',', '，', '/', '、']} placeholder="输入别名、昵称、尊称或隐藏身份后回车" />
            </Form.Item>
            <Form.Item name="age" label="年龄"><Input placeholder="如：3岁、约16岁、外表16岁实际200岁、成年" /></Form.Item>
            <Form.Item name="appearance" label="外貌"><Input.TextArea rows={3} placeholder="外貌、衣着、气质、辨识特征" /></Form.Item>
            <Form.Item name="personality" label="性格"><Input.TextArea rows={3} placeholder="核心性格、弱点、行为模式" /></Form.Item>
            <Form.Item name="background" label="背景故事"><Input.TextArea rows={4} placeholder="出身、经历、秘密、目标" /></Form.Item>
            <Form.Item name="abilities" label="能力">
              <Select mode="tags" tokenSeparators={[',', '，']} placeholder="输入能力后回车" />
            </Form.Item>
            <Collapse
              ghost
              items={[{
                key: 'writing-locks',
                label: <span style={{ fontWeight: 500 }}>写作锁 <span style={{ fontSize: 12, color: 'var(--ant-color-text-tertiary)', fontWeight: 400 }}>（确保跨章节的人设和声线稳定）</span></span>,
                children: (
                  <>
                    <div className="characters-two-col">
                      <Form.Item name={['profile', 'core_motivation']} label="核心动机"><Input.TextArea rows={3} placeholder="最根本的行动驱动力" /></Form.Item>
                      <Form.Item name={['profile', 'inner_lack']} label="内在缺口"><Input.TextArea rows={3} placeholder="角色尚未承认或无法满足的需要" /></Form.Item>
                    </div>
                    <div className="characters-two-col">
                      <Form.Item name={['profile', 'core_belief']} label="核心信念"><Input.TextArea rows={3} placeholder="角色相信世界如何运转" /></Form.Item>
                      <Form.Item name={['profile', 'moral_taboo']} label="道德禁忌"><Input.TextArea rows={3} placeholder="无论如何都不愿跨过的边界" /></Form.Item>
                    </div>
                    <div className="characters-two-col">
                      <Form.Item name={['profile', 'public_persona']} label="公开人设"><Input.TextArea rows={3} placeholder="他人通常看到的一面" /></Form.Item>
                      <Form.Item name={['profile', 'hidden_persona']} label="隐藏人设"><Input.TextArea rows={3} placeholder="在压力或亲密关系中才显露的一面" /></Form.Item>
                    </div>
                    <div className="characters-three-col">
                      <Form.Item name={['profile', 'reveal_chapter']} label="建议揭示章节"><InputNumber min={1} style={{ width: '100%' }} /></Form.Item>
                      <Form.Item name={['profile', 'voice']} label="声线"><Input placeholder="用词、句长、语气特点" /></Form.Item>
                      <Form.Item name={['profile', 'action_habit']} label="动作习惯"><Input placeholder="高压下反复出现的行为" /></Form.Item>
                    </div>
                    <Form.Item name={['profile', 'trauma_trigger']} label="创伤触发器"><Input.TextArea rows={3} placeholder="会改变判断或行为的具体情境" /></Form.Item>
                  </>
                ),
              }]}
            />
            <Collapse
              ghost
              items={[{
                key: 'state',
                label: <span style={{ fontWeight: 500 }}>当前状态 <span style={{ fontSize: 12, color: 'var(--ant-color-text-tertiary)', fontWeight: 400 }}>（可折叠，追踪角色实时变化）</span></span>,
                children: (
                  <>
                    <div className="characters-three-col">
                      <Form.Item name="life_status" label="生死状态"><Input placeholder="alive / dead / unknown，或中文描述" /></Form.Item>
                      <Form.Item name="current_location" label="当前位置"><Input placeholder="角色当前所处地点" /></Form.Item>
                      <Form.Item name="realm_or_level" label="境界/等级"><Input placeholder="修为、等级、身份层级" /></Form.Item>
                    </div>
                    <div className="characters-two-col">
                      <Form.Item name="physical_state" label="身体状况"><Input.TextArea rows={3} placeholder="伤势、疾病、体力、异常状态" /></Form.Item>
                      <Form.Item name="mental_state" label="心理状态"><Input.TextArea rows={3} placeholder="情绪、压力、执念、恐惧、决心" /></Form.Item>
                    </div>
                    <div className="characters-two-col">
                      <Form.Item name="current_goal" label="当前目标"><Input.TextArea rows={3} placeholder="本阶段想达成什么" /></Form.Item>
                      <Form.Item name="active_conflict" label="当前冲突"><Input.TextArea rows={3} placeholder="正在面对的矛盾、敌人、阻碍" /></Form.Item>
                    </div>
                    <div className="characters-two-col">
                      <Form.Item name="abilities_state" label="能力当前状态"><Input.TextArea rows={3} placeholder="能力是否受限、增强、失控、冷却" /></Form.Item>
                      <Form.Item name="items_or_assets" label="持有物/资源"><Input.TextArea rows={3} placeholder="法器、线索、资源、伤药、人脉" /></Form.Item>
                    </div>
                    <Form.Item name="is_evolution_tracked" label="动态演进追踪">
                      <Select options={[{ value: true, label: '开启' }, { value: false, label: '关闭' }]} />
                    </Form.Item>
                  </>
                ),
              }]}
            />
          </Form>

          {/* ── AI Dialogue Config ── */}
          <Divider orientation="left" plain>
            <SettingOutlined /> AI对话参数
          </Divider>
          {!selectedId ? (
            <Alert type="info" showIcon message="保存角色后可配置AI对话参数" />
          ) : (
            <Form form={aiConfigForm} layout="vertical" onFinish={saveAIConfig}>
              <div className="characters-ai-grid">
                <Form.Item name="tone_style" label="语气风格">
                  <Select options={TONE_OPTIONS} placeholder="选择语气风格" allowClear />
                </Form.Item>
                <Form.Item name="verbosity" label="话量偏好">
                  <Select options={VERBOSITY_OPTIONS} placeholder="选择话量偏好" />
                </Form.Item>
                <Form.Item name="emotion_tendency" label="情感倾向">
                  <Select options={EMOTION_OPTIONS} placeholder="选择情感倾向" allowClear />
                </Form.Item>
                <Form.Item name="model_override" label="角色专用模型">
                  <Select
                    allowClear
                    showSearch
                    options={modelOptions}
                    loading={modelsLoading}
                    optionFilterProp="label"
                    placeholder={modelOptions.length ? '选择角色专用模型（留空用全局默认）' : '请先在系统设置配置模型'}
                    notFoundContent={modelsLoading ? '加载模型中...' : '暂无已配置模型'}
                  />
                </Form.Item>
              </div>
              <Form.Item name="catchphrases" label="口头禅">
                <Select mode="tags" tokenSeparators={[',', '，']} placeholder="输入口头禅后回车" />
              </Form.Item>
              <Form.Item name="custom_system_prompt" label="额外系统提示词">
                <Input.TextArea rows={3} placeholder="为角色AI添加额外的行为指令，如：说话时经常岔开话题、喜欢引用古籍..." />
              </Form.Item>
              <Form.Item>
                <Button type="primary" icon={<SaveOutlined />} loading={aiConfigSaving} htmlType="submit">保存AI配置</Button>
              </Form.Item>
            </Form>
          )}

          {/* ── Relationships ── */}
          <div className="characters-section">
            <Title level={5}>关系编辑</Title>
            {!selectedId ? <Alert type="info" showIcon message="保存角色后可编辑关系" /> : (
              <>
                <Form form={relationshipForm} layout="inline" onFinish={addRelationship}>
                  <Form.Item name="target_character_id" rules={[{ required: true, message: '请选择角色' }]}>
                    <Select style={{ width: 180 }} placeholder="目标角色"
                      options={characters.filter((item) => item.id !== selectedId).map((item) => ({ value: item.id, label: item.name }))} />
                  </Form.Item>
                  <Form.Item name="relationship_type" rules={[{ required: true, message: '请输入关系类型' }]}>
                    <Input placeholder="师徒/敌对/同盟" style={{ width: 150 }} />
                  </Form.Item>
                  <Form.Item name="description"><Input placeholder="关系描述" style={{ width: 260 }} /></Form.Item>
                  <Form.Item><Button htmlType="submit">保存关系</Button></Form.Item>
                </Form>
                <List size="small" style={{ marginTop: 12 }} dataSource={selectedRelationships} locale={{ emptyText: '暂无关系' }}
                  renderItem={(edge) => (
                    <List.Item actions={[<Popconfirm key="remove" title="确定移除此关系？" okText="移除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => removeRelationship(edge.id)}><Button type="text" danger>移除</Button></Popconfirm>]}>
                      <List.Item.Meta
                        title={`${characterName(edge.from)} → ${characterName(edge.to)}：${edge.relationship_type}`}
                        description={edge.description || '无描述'} />
                    </List.Item>
                  )} />
              </>
            )}
          </div>

          {/* ── Versions ── */}
          <div className="characters-section">
            <Title level={5}><HistoryOutlined /> 版本历史</Title>
            {versions.length === 0 ? <Text type="secondary">暂无版本快照，保存一次修改后会自动生成。</Text> : (
              <Timeline items={versions.map((version) => ({
                children: <Button type="link" onClick={() => openVersion(version)}>v{version.version_number} · {version.change_summary || '角色更新'} · {new Date(version.created_at).toLocaleString('zh-CN')}</Button>,
              }))} />
            )}
          </div>

          <div className="characters-section">
            <Title level={5}>出场记录</Title>
            {!selectedDetail ? <Text type="secondary">选择角色后显示出场记录。</Text> : (
              <Descriptions size="small" column={2} bordered>
                <Descriptions.Item label="大纲节点">{selectedDetail.appearances?.outline_nodes.length || 0}</Descriptions.Item>
                <Descriptions.Item label="章节出场">{selectedDetail.appearances?.chapters.length || 0}</Descriptions.Item>
              </Descriptions>
            )}
          </div>

        </main>

      </div>

      <Modal title="角色版本快照" open={versionModalOpen} onCancel={() => setVersionModalOpen(false)} footer={null} width={720}>
        <pre className="characters-ai-result">{versionSnapshot ? JSON.stringify(versionSnapshot, null, 2) : ''}</pre>
      </Modal>

      <Modal title="疑似重复角色" open={duplicateModalOpen} onCancel={() => setDuplicateModalOpen(false)} footer={[
        <Button key="manual" onClick={() => openMergeModal()}>手动选择合并</Button>,
        <Button key="close" onClick={() => setDuplicateModalOpen(false)}>关闭</Button>,
      ]} width={820}>
        <List
          loading={duplicatesLoading}
          dataSource={duplicates}
          locale={{ emptyText: '暂未发现明显重复角色。仍可手动选择两张角色卡合并。' }}
          renderItem={(item) => (
            <List.Item actions={[<Button key="merge" type="primary" onClick={() => openMergeFromDuplicate(item)}>预览合并</Button>]}>
              <List.Item.Meta
                title={<Space wrap>
                  <Text strong>{item.primary.name}</Text>
                  <Text type="secondary">←</Text>
                  <Text>{item.secondary.name}</Text>
                  <Tag color="blue">{Math.round(item.score * 100)}%</Tag>
                </Space>}
                description={<Space direction="vertical" size={4}>
                  <Text type="secondary">依据：{item.reasons.join('；') || '名称/别名相似'}</Text>
                  <Text type="secondary">合并后别名：{item.aliases.join('、') || '无'}</Text>
                </Space>} />
            </List.Item>
          )}
        />
      </Modal>

      <Modal
        title="合并重复角色"
        open={mergeModalOpen}
        onCancel={() => setMergeModalOpen(false)}
        onOk={applyMerge}
        okText="确认合并"
        confirmLoading={mergeApplying}
        width={920}
      >
        <Form form={mergeForm} layout="vertical">
          <div className="characters-two-col">
            <Form.Item name="primary_id" label="保留主角色" rules={[{ required: true, message: '请选择主角色' }]}>
              <Select
                showSearch
                optionFilterProp="label"
                options={characters.map((item) => ({ value: item.id, label: `${item.name}${item.aliases?.length ? `（${item.aliases.join('、')}）` : ''}` }))}
                onChange={() => setMergePreview(null)}
              />
            </Form.Item>
            <Form.Item name="secondary_id" label="被合并角色" rules={[{ required: true, message: '请选择被合并角色' }]}>
              <Select
                showSearch
                optionFilterProp="label"
                options={characters.map((item) => ({ value: item.id, label: `${item.name}${item.aliases?.length ? `（${item.aliases.join('、')}）` : ''}` }))}
                onChange={() => setMergePreview(null)}
              />
            </Form.Item>
          </div>
          <Form.Item name="canonical_name" label="合并后主名称">
            <Input placeholder="默认使用主角色名称" />
          </Form.Item>
          <Form.Item name="aliases" label="合并后别名/称呼">
            <Select mode="tags" tokenSeparators={[',', '，', '/', '、']} placeholder="被合并角色名会自动加入别名" />
          </Form.Item>
          <Form.Item name="confidence_reason" label="合并依据">
            <Input.TextArea rows={2} placeholder="例如：同一家庭关系、同一出场身份、名字/称呼互相对应" />
          </Form.Item>
          <Button loading={mergePreviewLoading} onClick={() => refreshMergePreview()} style={{ marginBottom: 12 }}>刷新预览</Button>
        </Form>

        {mergePreview && (
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            <Descriptions size="small" bordered column={2}>
              <Descriptions.Item label="主角色">{mergePreview.primary.name}</Descriptions.Item>
              <Descriptions.Item label="被合并角色">{mergePreview.secondary.name}</Descriptions.Item>
              <Descriptions.Item label="迁移章节出场">{mergePreview.stats.secondary_chapter_appearances || 0}</Descriptions.Item>
              <Descriptions.Item label="迁移大纲关联">{mergePreview.stats.secondary_outline_links || 0}</Descriptions.Item>
              <Descriptions.Item label="迁移时间线">{mergePreview.stats.secondary_timeline_events || 0}</Descriptions.Item>
              <Descriptions.Item label="迁移关系">{mergePreview.stats.secondary_relationships || 0}</Descriptions.Item>
            </Descriptions>
            <Descriptions title="合并后预览" size="small" bordered column={1}>
              <Descriptions.Item label="名称">{mergePreview.merged_preview.name}</Descriptions.Item>
              <Descriptions.Item label="别名">{mergePreview.merged_preview.aliases?.join('、') || '无'}</Descriptions.Item>
              <Descriptions.Item label="能力">{mergePreview.merged_preview.abilities?.join('、') || '无'}</Descriptions.Item>
              <Descriptions.Item label="外貌">{mergePreview.merged_preview.appearance || '无'}</Descriptions.Item>
              <Descriptions.Item label="性格">{mergePreview.merged_preview.personality || '无'}</Descriptions.Item>
              <Descriptions.Item label="背景">{mergePreview.merged_preview.background || '无'}</Descriptions.Item>
            </Descriptions>
          </Space>
        )}
      </Modal>
    </div>
  )
}

export default CharactersPage
