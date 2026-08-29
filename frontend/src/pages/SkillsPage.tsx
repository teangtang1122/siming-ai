import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Badge,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  InputNumber,
  List,
  message,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  BulbOutlined,
  DeleteOutlined,
  EditOutlined,
  HistoryOutlined,
  LockOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { createLatestRequestGate } from '../shared/latestRequest'

const { TextArea } = Input
const { Text } = Typography

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface Skill {
  id: string
  project_id: string
  builtin_key: string | null
  name: string
  description: string | null
  trigger_examples: string[]
  system_prompt: string
  recommended_tools: string[]
  forbidden_tools: string[]
  scope: string
  priority: number
  enabled: boolean
  is_builtin: boolean
  created_at: string
  updated_at: string
}

interface SkillTemplate {
  key: string
  name: string
  description: string
  scope: string
  trigger_examples: string[]
  recommended_tools: string[]
}

interface SkillTool {
  name: string
  description: string
  tool_type: string
  estimated_cost: string
  idempotent: boolean
  requires_confirmation: boolean
}

interface SkillVersion {
  id: string
  skill_id: string
  project_id: string
  title: string
  change_summary?: string | null
  snapshot: Partial<Skill>
  created_at: string
}

interface SkillsListResponse {
  items: Skill[]
  total: number
}

interface SkillTemplatesResponse {
  items: SkillTemplate[]
  total: number
}

interface SkillToolsResponse {
  items: SkillTool[]
  total: number
}

interface SkillVersionsResponse {
  items: SkillVersion[]
  total: number
}

interface SkillsPageProps {
  projectId: string
}

const SCOPE_OPTIONS = [
  { value: 'global', label: '全局' },
  { value: 'project', label: '项目' },
  { value: 'writing', label: '写作' },
  { value: 'outline', label: '大纲' },
  { value: 'characters', label: '角色' },
  { value: 'worldbuilding', label: '世界观' },
  { value: 'cataloging', label: '建档' },
  { value: 'research', label: '资料搜索' },
]

const SCOPE_COLOR: Record<string, string> = {
  global: 'purple',
  project: 'blue',
  writing: 'green',
  outline: 'orange',
  characters: 'cyan',
  worldbuilding: 'gold',
  cataloging: 'lime',
  research: 'magenta',
}

interface SkillFormValues {
  name: string
  description?: string
  trigger_examples: string[]
  system_prompt: string
  recommended_tools: string[]
  forbidden_tools: string[]
  scope: string
  priority: number
  enabled: boolean
}

function scopeLabel(scope: string) {
  return SCOPE_OPTIONS.find((option) => option.value === scope)?.label || scope
}

function skillPayload(values: Partial<SkillFormValues>) {
  return {
    name: values.name,
    description: values.description || null,
    trigger_examples: values.trigger_examples || [],
    system_prompt: values.system_prompt,
    recommended_tools: values.recommended_tools || [],
    forbidden_tools: values.forbidden_tools || [],
    scope: values.scope || 'global',
    priority: values.priority ?? 0,
    enabled: values.enabled ?? true,
  }
}

function SkillsPage({ projectId }: SkillsPageProps) {
  const [skills, setSkills] = useState<Skill[]>([])
  const [templates, setTemplates] = useState<SkillTemplate[]>([])
  const [tools, setTools] = useState<SkillTool[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null)
  const [saving, setSaving] = useState(false)
  const [draftRequirements, setDraftRequirements] = useState('')
  const [selectedTemplateKey, setSelectedTemplateKey] = useState<string>()
  const [drafting, setDrafting] = useState(false)
  const [versionDrawerOpen, setVersionDrawerOpen] = useState(false)
  const [versionLoading, setVersionLoading] = useState(false)
  const [versions, setVersions] = useState<SkillVersion[]>([])
  const [versionSkillName, setVersionSkillName] = useState('')
  const [form] = Form.useForm<SkillFormValues>()
  const versionRequestGate = useRef(createLatestRequestGate<string>())
  const versionSkillIdRef = useRef<string | null>(null)

  const fetchSkills = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<SkillsListResponse>>(
        `/projects/${projectId}/skills`
      )
      setSkills(res.data.data.items)
    } catch (err: any) {
      message.error(err.message || '获取技能列表失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  const fetchSkillMeta = useCallback(async () => {
    try {
      const [templateRes, toolRes] = await Promise.all([
        apiClient.get<ApiResponse<SkillTemplatesResponse>>(
          `/projects/${projectId}/skills/templates`
        ),
        apiClient.get<ApiResponse<SkillToolsResponse>>(`/projects/${projectId}/skills/tools`),
      ])
      setTemplates(templateRes.data.data.items)
      setTools(toolRes.data.data.items)
    } catch (err: any) {
      message.warning(err.message || '获取技能模板或工具目录失败')
    }
  }, [projectId])

  useEffect(() => {
    const versionGate = versionRequestGate.current
    versionGate.invalidate()
    versionSkillIdRef.current = null
    setVersionDrawerOpen(false)
    setVersionLoading(false)
    setVersions([])
    fetchSkills()
    fetchSkillMeta()
    return () => versionGate.invalidate()
  }, [fetchSkills, fetchSkillMeta])

  const resetAssistantState = () => {
    setDraftRequirements('')
    setSelectedTemplateKey(undefined)
  }

  const openCreateModal = () => {
    setEditingSkill(null)
    resetAssistantState()
    form.resetFields()
    form.setFieldsValue({
      trigger_examples: [],
      recommended_tools: [],
      forbidden_tools: [],
      scope: 'global',
      priority: 0,
      enabled: true,
    })
    setModalOpen(true)
  }

  const openEditModal = (skill: Skill) => {
    setEditingSkill(skill)
    resetAssistantState()
    form.setFieldsValue({
      name: skill.name,
      description: skill.description || undefined,
      trigger_examples: skill.trigger_examples,
      system_prompt: skill.system_prompt,
      recommended_tools: skill.recommended_tools,
      forbidden_tools: skill.forbidden_tools,
      scope: skill.scope,
      priority: skill.priority,
      enabled: skill.enabled,
    })
    setModalOpen(true)
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      const payload = skillPayload(values)

      if (editingSkill) {
        await apiClient.put(`/projects/${projectId}/skills/${editingSkill.id}`, payload)
        message.success('技能已更新')
      } else {
        await apiClient.post(`/projects/${projectId}/skills`, payload)
        message.success('技能已创建')
      }

      setModalOpen(false)
      fetchSkills()
    } catch (err: any) {
      if (err.message) {
        message.error(err.message)
      }
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (skillId: string) => {
    try {
      await apiClient.delete(`/projects/${projectId}/skills/${skillId}`)
      message.success('技能已删除')
      fetchSkills()
    } catch (err: any) {
      message.error(err.message || '删除技能失败')
    }
  }

  const handleToggleEnabled = async (skill: Skill, enabled: boolean) => {
    try {
      await apiClient.put(`/projects/${projectId}/skills/${skill.id}`, { enabled })
      message.success(enabled ? '技能已启用' : '技能已禁用')
      fetchSkills()
    } catch (err: any) {
      message.error(err.message || '更新技能状态失败')
    }
  }

  const handleResetToBuiltin = async (skill: Skill) => {
    try {
      await apiClient.post(`/projects/${projectId}/skills/${skill.id}/reset`)
      message.success('已恢复默认值')
      fetchSkills()
      // If the modal is open for this skill, refresh its form values
      if (editingSkill?.id === skill.id) {
        const res = await apiClient.get<ApiResponse<SkillsListResponse>>(
          `/projects/${projectId}/skills`
        )
        const refreshed = res.data.data.items.find((s) => s.id === skill.id)
        if (refreshed) {
          setEditingSkill(refreshed)
          form.setFieldsValue({
            name: refreshed.name,
            description: refreshed.description || undefined,
            trigger_examples: refreshed.trigger_examples,
            system_prompt: refreshed.system_prompt,
            recommended_tools: refreshed.recommended_tools,
            forbidden_tools: refreshed.forbidden_tools,
            scope: refreshed.scope,
            priority: refreshed.priority,
            enabled: refreshed.enabled,
          })
        }
      }
    } catch (err: any) {
      message.error(err.message || '恢复默认值失败')
    }
  }

  const applyDraft = async () => {
    if (!draftRequirements.trim()) {
      message.warning('先写下你希望这个技能做什么')
      return
    }
    setDrafting(true)
    try {
      const res = await apiClient.post<ApiResponse<Partial<SkillFormValues> & {
        template_key?: string
      }>>(`/projects/${projectId}/skills/draft`, {
        requirements: draftRequirements,
        template_key: selectedTemplateKey || null,
        scope: form.getFieldValue('scope') || 'global',
      })
      const draft = res.data.data
      form.setFieldsValue({
        name: draft.name,
        description: draft.description || undefined,
        trigger_examples: draft.trigger_examples || [],
        system_prompt: draft.system_prompt,
        recommended_tools: draft.recommended_tools || [],
        scope: draft.scope || 'global',
        priority: draft.priority ?? 50,
        enabled: draft.enabled ?? true,
      })
      if (draft.template_key) {
        setSelectedTemplateKey(draft.template_key)
      }
      message.success('已生成技能草案')
    } catch (err: any) {
      message.error(err.message || '生成技能草案失败')
    } finally {
      setDrafting(false)
    }
  }

  const openVersions = async (skill: Skill) => {
    const request = versionRequestGate.current.begin(skill.id)
    versionSkillIdRef.current = skill.id
    setVersionSkillName(skill.name)
    setVersionDrawerOpen(true)
    setVersions([])
    setVersionLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<SkillVersionsResponse>>(
        `/projects/${projectId}/skills/${skill.id}/versions`
      )
      if (versionRequestGate.current.isCurrent(request) && versionSkillIdRef.current === skill.id) {
        setVersions(res.data.data.items)
      }
    } catch (err: any) {
      if (versionRequestGate.current.isCurrent(request) && versionSkillIdRef.current === skill.id) {
        message.error(err.message || '获取版本历史失败')
      }
    } finally {
      if (versionRequestGate.current.isCurrent(request) && versionSkillIdRef.current === skill.id) {
        setVersionLoading(false)
      }
    }
  }

  const closeVersions = () => {
    versionRequestGate.current.invalidate()
    versionSkillIdRef.current = null
    setVersionDrawerOpen(false)
    setVersionLoading(false)
    setVersions([])
  }

  const columns: ColumnsType<Skill> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 180,
      ellipsis: true,
      render: (name: string, record: Skill) => (
        <Space>
          {record.is_builtin && (
            <Tooltip title="内置技能">
              <LockOutlined style={{ color: '#999', fontSize: 12 }} />
            </Tooltip>
          )}
          <Text strong>{name}</Text>
        </Space>
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      width: 240,
      ellipsis: true,
      render: (desc: string | null) => desc || <Text type="secondary">-</Text>,
    },
    {
      title: '使用示例',
      dataIndex: 'trigger_examples',
      key: 'trigger_examples',
      width: 240,
      render: (examples: string[]) => (
        <Space size={[0, 4]} wrap>
          {examples.slice(0, 3).map((example) => (
            <Tag key={example}>{example}</Tag>
          ))}
          {examples.length > 3 && <Tag>+{examples.length - 3}</Tag>}
          {examples.length === 0 && <Text type="secondary">-</Text>}
        </Space>
      ),
    },
    {
      title: '范围',
      dataIndex: 'scope',
      key: 'scope',
      width: 96,
      render: (scope: string) => (
        <Tag color={SCOPE_COLOR[scope] || 'default'}>{scopeLabel(scope)}</Tag>
      ),
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      key: 'priority',
      width: 82,
      sorter: (a, b) => a.priority - b.priority,
      render: (priority: number) => <Text>{priority}</Text>,
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 76,
      render: (enabled: boolean, record: Skill) => (
        <Switch
          size="small"
          checked={enabled}
          onChange={(checked) => handleToggleEnabled(record, checked)}
        />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 136,
      render: (_: unknown, record: Skill) => (
        <Space size="small">
          <Tooltip title="版本历史">
            <Button
              type="text"
              size="small"
              icon={<HistoryOutlined />}
              onClick={() => openVersions(record)}
            />
          </Tooltip>
          <Tooltip title="编辑">
            <Button
              type="text"
              size="small"
              icon={<EditOutlined />}
              onClick={() => openEditModal(record)}
            />
          </Tooltip>
          {record.is_builtin ? (
            <Tooltip title="内置技能不能删除">
              <Button type="text" size="small" icon={<DeleteOutlined />} disabled />
            </Tooltip>
          ) : (
            <Popconfirm
              title="确定删除此技能？"
              onConfirm={() => handleDelete(record.id)}
              okText="删除"
              cancelText="取消"
            >
              <Button type="text" size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <BulbOutlined style={{ fontSize: 20 }} />
          <Text strong style={{ fontSize: 16 }}>技能管理</Text>
          <Badge count={skills.length} style={{ backgroundColor: '#52c41a' }} />
        </Space>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchSkills} loading={loading}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>
            新建技能
          </Button>
        </Space>
      </div>

      <Table
        dataSource={skills}
        columns={columns}
        rowKey="id"
        loading={loading}
        size="small"
        pagination={false}
        scroll={{ x: 1000 }}
      />

      <Modal
        title={editingSkill ? '编辑技能' : '新建技能'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        width={820}
        okText="保存"
        cancelText="取消"
      >
        <Card size="small" title={<Space><RobotOutlined />辅助创建</Space>} style={{ marginBottom: 16 }}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <TextArea
              value={draftRequirements}
              onChange={(event) => setDraftRequirements(event.target.value)}
              placeholder="描述这个技能要做什么，例如：续写章节时严格检查角色当前位置、身体状态和禁用句式。"
              autoSize={{ minRows: 2, maxRows: 4 }}
            />
            <Space wrap>
              <Select
                allowClear
                style={{ minWidth: 220 }}
                placeholder="选择模板"
                value={selectedTemplateKey}
                onChange={setSelectedTemplateKey}
                options={templates.map((template) => ({
                  value: template.key,
                  label: `${template.name} · ${scopeLabel(template.scope)}`,
                }))}
              />
              <Button icon={<RobotOutlined />} onClick={applyDraft} loading={drafting}>
                生成草案
              </Button>
              <Text type="secondary">草案会填充使用示例、提示词和推荐工具，保存前仍可手动修改。</Text>
            </Space>
          </Space>
        </Card>

        <Form
          form={form}
          layout="vertical"
          initialValues={{
            trigger_examples: [],
            recommended_tools: [],
            forbidden_tools: [],
            scope: 'global',
            priority: 0,
            enabled: true,
          }}
        >
          <Form.Item
            name="name"
            label="技能名称"
            rules={[{ required: true, message: '请输入技能名称' }]}
          >
            <Input placeholder="例如：暗黑仙侠风格审校" maxLength={100} />
          </Form.Item>

          <Form.Item name="description" label="描述">
            <Input placeholder="技能的简要描述" />
          </Form.Item>

          <Form.Item name="trigger_examples" label="使用示例">
            <Select
              mode="tags"
              placeholder="输入典型使用请求后回车添加，供 Agent 理解"
              tokenSeparators={[',', '，']}
            />
          </Form.Item>

          <Form.Item
            name="system_prompt"
            label="系统提示词"
            rules={[{ required: true, message: '请输入系统提示词' }]}
          >
            <TextArea
              placeholder="Agent 选中该技能后应遵循的内容..."
              autoSize={{ minRows: 5, maxRows: 12 }}
              maxLength={4000}
              showCount
            />
          </Form.Item>

          <Form.Item name="recommended_tools" label="推荐工具">
            <Select
              mode="tags"
              placeholder="选择或输入工具名称"
              tokenSeparators={[',', '，']}
              options={tools.map((tool) => ({
                value: tool.name,
                label: `${tool.name} · ${tool.tool_type}`,
              }))}
            />
          </Form.Item>

          <Form.Item name="forbidden_tools" label="禁用工具">
            <Select
              mode="tags"
              placeholder="选择或输入禁用的工具名称"
              tokenSeparators={[',', '，']}
              options={tools.map((tool) => ({
                value: tool.name,
                label: `${tool.name} · ${tool.tool_type}`,
              }))}
            />
          </Form.Item>

          <div style={{ display: 'flex', gap: 16 }}>
            <Form.Item name="scope" label="适用范围" style={{ flex: 1 }}>
              <Select options={SCOPE_OPTIONS} />
            </Form.Item>

            <Form.Item name="priority" label="优先级" style={{ flex: 1 }}>
              <InputNumber min={0} max={100} style={{ width: '100%' }} />
            </Form.Item>

            <Form.Item name="enabled" label="启用" valuePropName="checked" style={{ flex: 1 }}>
              <Switch />
            </Form.Item>
          </div>
        </Form>

        {editingSkill?.is_builtin && (
          <div style={{ marginBottom: 16 }}>
            <Popconfirm
              title="确定恢复此内置技能的默认值？"
              description="所有自定义修改将被覆盖，操作不可撤销。"
              onConfirm={() => handleResetToBuiltin(editingSkill)}
              okText="恢复"
              cancelText="取消"
            >
              <Button icon={<ReloadOutlined />} danger>
                恢复默认
              </Button>
            </Popconfirm>
            <Text type="secondary" style={{ marginLeft: 8 }}>
              将系统提示词、使用示例、描述等恢复为内置默认值
            </Text>
          </div>
        )}

      </Modal>

      <Drawer
        title={`版本历史：${versionSkillName || '-'}`}
        open={versionDrawerOpen}
        onClose={closeVersions}
        width={520}
      >
        <List
          loading={versionLoading}
          dataSource={versions}
          locale={{ emptyText: '暂无版本记录' }}
          renderItem={(version) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <Space>
                    <Text strong>{version.title}</Text>
                    <Text type="secondary">{new Date(version.created_at).toLocaleString()}</Text>
                  </Space>
                }
                description={
                  <Space direction="vertical" size={4}>
                    {version.change_summary && <Text>{version.change_summary}</Text>}
                    <Text type="secondary">
                      {version.snapshot?.description || version.snapshot?.system_prompt || '已记录配置快照'}
                    </Text>
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Drawer>
    </div>
  )
}

export default SkillsPage
