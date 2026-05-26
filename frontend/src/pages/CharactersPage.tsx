import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Descriptions, Divider, Empty, Form, Input, List, Modal,
  Popconfirm, Select, Space, Tag, Timeline, Typography, message,
} from 'antd'
import {
  DeleteOutlined, HistoryOutlined, PlusOutlined,
  SaveOutlined, SettingOutlined, TeamOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { useAiPanelContext } from '../contexts/AiPanelContext'
import { useModelOptions } from '../hooks/useModelOptions'
import './CharactersPage.css'

const { Text, Title } = Typography

interface ApiResponse<T> { code: number; message: string; data: T }

interface Character {
  id: string; project_id: string; name: string; appearance?: string
  personality?: string; background?: string; abilities: string[]
  role_type?: string; current_version: number; is_evolution_tracked: boolean
  life_status?: string; current_location?: string; realm_or_level?: string
  physical_state?: string; mental_state?: string; current_goal?: string
  active_conflict?: string; abilities_state?: string; items_or_assets?: string
  created_at: string; updated_at: string
  appearances?: {
    outline_nodes: Array<{ id: string; title: string; node_type: string; role_in_scene?: string }>
    chapters: Array<{ id: string; title: string; appearance_type: string; description?: string }>
  }
}

interface RelationshipEdge { id: string; from: string; to: string; relationship_type: string; description?: string }
interface RelationshipNetwork { nodes: Array<{ id: string; name: string; role_type?: string }>; edges: RelationshipEdge[]; total: number }
interface VersionItem { id: string; character_id: string; version_number: number; change_summary?: string; created_at: string }

interface AIConfig {
  id?: string; character_id?: string; tone_style: string; catchphrases: string[]
  verbosity: string; emotion_tendency: string; model_override?: string
  custom_system_prompt?: string; created_at?: string; updated_at?: string
}

interface CharacterFormValues {
  name: string; role_type?: string; appearance?: string; personality?: string
  background?: string; abilities?: string[]; is_evolution_tracked?: boolean
  life_status?: string; current_location?: string; realm_or_level?: string
  physical_state?: string; mental_state?: string; current_goal?: string
  active_conflict?: string; abilities_state?: string; items_or_assets?: string
}

interface CharactersPageProps { projectId: string }

const ROLE_OPTIONS = [
  { value: 'protagonist', label: '主角' }, { value: 'supporting', label: '配角' },
  { value: 'antagonist', label: '反派' }, { value: 'mentor', label: '导师' }, { value: 'other', label: '其他' },
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
  const { setAiContext, refreshKey } = useAiPanelContext()
  const { modelOptions, loading: modelsLoading } = useModelOptions()

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
        name: res.data.data.name, role_type: res.data.data.role_type,
        appearance: res.data.data.appearance, personality: res.data.data.personality,
        background: res.data.data.background, abilities: res.data.data.abilities || [],
        life_status: res.data.data.life_status,
        current_location: res.data.data.current_location,
        realm_or_level: res.data.data.realm_or_level,
        physical_state: res.data.data.physical_state,
        mental_state: res.data.data.mental_state,
        current_goal: res.data.data.current_goal,
        active_conflict: res.data.data.active_conflict,
        abilities_state: res.data.data.abilities_state,
        items_or_assets: res.data.data.items_or_assets,
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
    setSelectedId(null); setSelectedDetail(null); setVersions([]); setAiConfig(null)
    form.resetFields(); aiConfigForm.resetFields()
    form.setFieldsValue({ abilities: [], is_evolution_tracked: true, role_type: 'supporting' })
  }

  const saveCharacter = async (values: CharacterFormValues) => {
    setSaving(true)
    try {
      const payload = { ...values, abilities: values.abilities || [], is_evolution_tracked: values.is_evolution_tracked ?? true }
      if (selectedId) {
        const res = await apiClient.put<ApiResponse<Character>>(`/projects/${projectId}/characters/${selectedId}`, { ...payload, change_summary: '前端手动保存角色档案' })
        setSelectedId(res.data.data.id); message.success('角色已保存')
      } else {
        const res = await apiClient.post<ApiResponse<Character>>(`/projects/${projectId}/characters`, payload)
        setSelectedId(res.data.data.id); message.success('角色已创建')
      }
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

  return (
    <div className="characters-page">
      <div className="characters-shell">
        <aside className="characters-sidebar">
          <div className="characters-sidebar-toolbar">
            <div className="characters-sidebar-head">
              <Title level={4} style={{ margin: 0 }}><TeamOutlined /> 角色</Title>
              <Button type="primary" icon={<PlusOutlined />} onClick={startCreate} />
            </div>
            <Input.Search placeholder="搜索角色" allowClear value={keyword}
              onChange={(event) => setKeyword(event.target.value)} onSearch={(value) => fetchCharacters(value)} />
          </div>
          <div className="characters-list-scroll">
            <List loading={loading} dataSource={characters}
              locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无角色" /> }}
              renderItem={(item) => (
                <List.Item className={`characters-list-item${item.id === selectedId ? ' characters-list-item-active' : ''}`}
                  onClick={() => setSelectedId(item.id)}>
                  <List.Item.Meta title={<span className="characters-name-text" title={item.name}>{item.name}</span>}
                    description={<Space size={6} wrap><Tag>{item.role_type || '未分类'}</Tag><Text type="secondary">v{item.current_version}</Text></Space>} />
                </List.Item>
              )} />
          </div>
        </aside>

        <main className="characters-editor">
          <div className="characters-editor-head">
            <div>
              <Title level={4} style={{ margin: 0 }}>{selectedDetail ? selectedDetail.name : '新角色'}</Title>
              {selectedDetail && <Text type="secondary">当前版本 v{selectedDetail.current_version}</Text>}
            </div>
            <Space>
              {selectedId && (
                <Popconfirm title="删除角色" description="该角色的关系、版本和出场记录也会被删除。" okText="删除" cancelText="取消"
                  okButtonProps={{ danger: true, autoInsertSpace: false }} cancelButtonProps={{ autoInsertSpace: false }} onConfirm={deleteCharacter}>
                  <Button danger icon={<DeleteOutlined />}>删除</Button>
                </Popconfirm>
              )}
              <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => form.submit()}>保存角色</Button>
            </Space>
          </div>

          <Form form={form} layout="vertical" onFinish={saveCharacter}>
            <div className="characters-two-col">
              <Form.Item name="name" label="姓名" rules={[{ required: true, message: '请输入角色姓名' }]}>
                <Input placeholder="角色姓名" maxLength={100} />
              </Form.Item>
              <Form.Item name="role_type" label="角色类型">
                <Select options={ROLE_OPTIONS} placeholder="选择角色类型" />
              </Form.Item>
            </div>
            <Form.Item name="appearance" label="外貌"><Input.TextArea rows={3} placeholder="外貌、衣着、气质、辨识特征" /></Form.Item>
            <Form.Item name="personality" label="性格"><Input.TextArea rows={3} placeholder="核心性格、弱点、行为模式" /></Form.Item>
            <Form.Item name="background" label="背景故事"><Input.TextArea rows={4} placeholder="出身、经历、秘密、目标" /></Form.Item>
            <Form.Item name="abilities" label="能力">
              <Select mode="tags" tokenSeparators={[',', '，']} placeholder="输入能力后回车" />
            </Form.Item>
            <Divider orientation="left" plain>当前状态</Divider>
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
                    <List.Item actions={[<Button key="remove" type="text" danger onClick={() => removeRelationship(edge.id)}>移除</Button>]}>
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
    </div>
  )
}

export default CharactersPage
