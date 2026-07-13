import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Form, Input, InputNumber, Modal, Space, Switch, Table, Tag, Typography, message } from 'antd'
import { EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { apiClient } from '../api/client'

const { Text } = Typography

interface ApiResponse<T> { code: number; message: string; data: T }

interface ModelProfile {
  id: string
  provider: string
  model_name: string
  context_window_tokens: number
  max_output_tokens?: number | null
  safety_margin_tokens: number
  enabled: boolean
}

interface ProfileFormValues {
  provider: string
  model_name: string
  context_window_tokens: number
  max_output_tokens?: number
  safety_margin_tokens: number
  enabled: boolean
}

export default function ContextGovernanceSettingsPanel() {
  const [profiles, setProfiles] = useState<ModelProfile[]>([])
  const [semantic, setSemantic] = useState<{ available?: boolean; model?: string; reason?: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<ModelProfile | null>(null)
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm<ProfileFormValues>()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const response = await apiClient.get<ApiResponse<{ items: ModelProfile[]; semantic: { available?: boolean; model?: string; reason?: string } }>>('/context-governance/model-profiles')
      setProfiles(response.data.data.items || [])
      setSemantic(response.data.data.semantic || null)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载上下文模型档案失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const showCreate = () => {
    setEditing(null)
    form.setFieldsValue({ context_window_tokens: 16384, safety_margin_tokens: 512, enabled: true, provider: '', model_name: '' })
    setOpen(true)
  }

  const showEdit = (profile: ModelProfile) => {
    setEditing(profile)
    form.setFieldsValue({ ...profile, max_output_tokens: profile.max_output_tokens ?? undefined })
    setOpen(true)
  }

  const save = async (values: ProfileFormValues) => {
    try {
      await apiClient.put('/context-governance/model-profiles', values)
      message.success('模型上下文档案已保存')
      setOpen(false)
      load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '模型上下文档案未保存')
    }
  }

  return (
    <Card
      title="上下文治理设置"
      style={{ marginTop: 16 }}
      extra={<Space><Button icon={<ReloadOutlined />} loading={loading} onClick={load} /><Button type="primary" icon={<PlusOutlined />} onClick={showCreate}>添加档案</Button></Space>}
    >
      <div style={{ marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Tag color={semantic?.available ? 'green' : 'default'}>{semantic?.available ? '本地语义检索可用' : '词法检索模式'}</Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>{semantic?.model || 'multilingual-e5-small'}{semantic?.reason ? ` · ${semantic.reason}` : ''}</Text>
      </div>
      <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
        未配置的模型按 16K 上下文窗口和保守预留执行。这里的档案只决定预算，不会修改 API 或 CLI 凭据。
      </Text>
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={profiles}
        pagination={false}
        scroll={{ x: 760 }}
        columns={[
          { title: '提供商', dataIndex: 'provider', width: 130 },
          { title: '模型', dataIndex: 'model_name', ellipsis: true },
          { title: '上下文窗口', dataIndex: 'context_window_tokens', width: 130, render: (value: number) => `${Math.round(value / 1024)}K` },
          { title: '最大输出', dataIndex: 'max_output_tokens', width: 120, render: (value?: number | null) => value ? value.toLocaleString() : '自动' },
          { title: '余量', dataIndex: 'safety_margin_tokens', width: 90 },
          { title: '状态', dataIndex: 'enabled', width: 90, render: (value: boolean) => <Tag color={value ? 'green' : 'default'}>{value ? '启用' : '停用'}</Tag> },
          { title: '操作', width: 70, render: (_: unknown, item: ModelProfile) => <Button type="text" icon={<EditOutlined />} title="编辑档案" onClick={() => showEdit(item)} /> },
        ]}
      />
      <Modal title={editing ? '编辑上下文档案' : '添加上下文档案'} open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} destroyOnHidden>
        <Form form={form} layout="vertical" onFinish={save}>
          <Form.Item name="provider" label="提供商" rules={[{ required: true, message: '请填写提供商' }]}><Input disabled={Boolean(editing)} placeholder="例如 openai 或 codex_cli" /></Form.Item>
          <Form.Item name="model_name" label="模型" rules={[{ required: true, message: '请填写模型名' }]}><Input disabled={Boolean(editing)} placeholder="例如 gpt-4.1" /></Form.Item>
          <Form.Item name="context_window_tokens" label="上下文窗口 tokens" rules={[{ required: true }]}><InputNumber min={2048} max={10000000} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="max_output_tokens" label="最大输出 tokens"><InputNumber min={1} max={10000000} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="safety_margin_tokens" label="保护余量 tokens" rules={[{ required: true }]}><InputNumber min={0} max={100000} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
