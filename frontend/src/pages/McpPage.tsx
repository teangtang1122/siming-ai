import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Form,
  Input,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
  Modal,
  Popconfirm,
} from 'antd'
import {
  PlusOutlined,
  DeleteOutlined,
  ApiOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import ExternalAgentPermissionPanel from '../components/ExternalAgentPermissionPanel'

const { Title, Text } = Typography

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface McpServerConfig {
  id: string
  project_id: string
  name: string
  transport: string
  command: string | null
  url: string | null
  enabled: boolean
  status: string
  last_error: string | null
  created_at: string
  updated_at: string | null
}

interface McpPageProps {
  projectId: string
}

const STATUS_COLORS: Record<string, string> = {
  connected: 'green',
  connecting: 'blue',
  disconnected: 'default',
  error: 'red',
}

function McpPage({ projectId }: McpPageProps) {
  const [servers, setServers] = useState<McpServerConfig[]>([])
  const [loading, setLoading] = useState(false)
  const [addModalOpen, setAddModalOpen] = useState(false)
  const [form] = Form.useForm()

  const fetchServers = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await apiClient.get<ApiResponse<{ items: McpServerConfig[] }>>(
        `/projects/${projectId}/mcp-servers`
      )
      setServers(resp.data.data.items)
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    fetchServers()
  }, [fetchServers])

  const handleAdd = async (values: { name: string; transport: string; command?: string; url?: string }) => {
    try {
      await apiClient.post(`/projects/${projectId}/mcp-servers`, values)
      message.success('MCP 服务器已添加')
      setAddModalOpen(false)
      form.resetFields()
      fetchServers()
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '添加失败')
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await apiClient.delete(`/projects/${projectId}/mcp-servers/${id}`)
      message.success('已删除')
      fetchServers()
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '删除失败')
    }
  }

  const handleToggleEnabled = async (id: string, enabled: boolean) => {
    try {
      await apiClient.patch(`/projects/${projectId}/mcp-servers/${id}`, { enabled })
      fetchServers()
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '更新失败')
    }
  }

  const handleTestConnection = async (id: string) => {
    try {
      await apiClient.post(`/projects/${projectId}/mcp-servers/${id}/test`)
      message.success('连接测试完成')
      fetchServers()
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '测试失败')
    }
  }

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string) => <Text strong>{name}</Text>,
    },
    {
      title: '传输方式',
      dataIndex: 'transport',
      key: 'transport',
      render: (transport: string) => <Tag>{transport}</Tag>,
    },
    {
      title: '地址',
      key: 'address',
      render: (_: unknown, record: McpServerConfig) => (
        <Text type="secondary" ellipsis style={{ maxWidth: 300 }}>
          {record.transport === 'stdio' ? record.command : record.url}
        </Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => (
        <Tag color={STATUS_COLORS[status] || 'default'}>{status}</Tag>
      ),
    },
    {
      title: '启用',
      key: 'enabled',
      render: (_: unknown, record: McpServerConfig) => (
        <Switch
          checked={record.enabled}
          onChange={(checked) => handleToggleEnabled(record.id, checked)}
          size="small"
        />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: McpServerConfig) => (
        <Space>
          <Button
            size="small"
            icon={<ApiOutlined />}
            onClick={() => handleTestConnection(record.id)}
          >
            测试
          </Button>
          <Popconfirm title="确认删除？" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={4}>
          <ApiOutlined style={{ marginRight: 8 }} />
          MCP 服务器管理
        </Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchServers}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setAddModalOpen(true)}>
            添加服务器
          </Button>
        </Space>
      </div>

      <ExternalAgentPermissionPanel projectId={projectId} />

      <Card
        title={
          <Space>
            <ApiOutlined />
            <span>MCP 服务配置</span>
          </Space>
        }
      >
        <Table
          dataSource={servers}
          columns={columns}
          rowKey="id"
          loading={loading}
          pagination={false}
          locale={{ emptyText: '暂无 MCP 服务器配置' }}
        />
      </Card>

      <Modal
        title="添加 MCP 服务器"
        open={addModalOpen}
        onCancel={() => setAddModalOpen(false)}
        footer={null}
      >
        <Form form={form} layout="vertical" onFinish={handleAdd} initialValues={{ transport: 'stdio' }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="my-mcp-server" />
          </Form.Item>
          <Form.Item name="transport" label="传输方式">
            <Select>
              <Select.Option value="stdio">stdio</Select.Option>
              <Select.Option value="http">HTTP</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, cur) => prev.transport !== cur.transport}>
            {({ getFieldValue }) =>
              getFieldValue('transport') === 'stdio' ? (
                <Form.Item name="command" label="命令">
                  <Input placeholder="python scripts/mcp-server.py" />
                </Form.Item>
              ) : (
                <Form.Item name="url" label="URL">
                  <Input placeholder="http://localhost:3000" />
                </Form.Item>
              )
            }
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                添加
              </Button>
              <Button onClick={() => setAddModalOpen(false)}>取消</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default McpPage
