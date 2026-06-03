import { useCallback, useEffect, useState } from 'react'
import { Button, Empty, Popconfirm, Select, Space, Table, Tag, Typography, message } from 'antd'
import { DeleteOutlined, ReloadOutlined } from '@ant-design/icons'
import { apiClient } from '../api/client'

const { Text } = Typography

interface MemoryItem {
  id: string
  category: string
  key: string
  value: string
  source: string
  importance: number
  created_at?: string
  updated_at?: string
}

const CATEGORY_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'user_preference', label: '用户偏好' },
  { value: 'writing_style', label: '写作风格' },
  { value: 'workflow_preference', label: '工作流偏好' },
  { value: 'project_fact', label: '项目事实' },
  { value: 'research_note', label: '研究笔记' },
]

const CATEGORY_LABELS: Record<string, string> = {
  user_preference: '用户偏好',
  writing_style: '写作风格',
  workflow_preference: '工作流偏好',
  project_fact: '项目事实',
  research_note: '研究笔记',
  preference: '用户偏好',
  fact: '项目事实',
  search_result: '研究笔记',
  note: '笔记',
}

const CATEGORY_COLORS: Record<string, string> = {
  user_preference: 'blue',
  writing_style: 'purple',
  workflow_preference: 'cyan',
  project_fact: 'green',
  research_note: 'orange',
  preference: 'blue',
  fact: 'green',
  search_result: 'orange',
  note: 'default',
}

interface AssistantMemoryListProps {
  projectId: string
}

export function AssistantMemoryList({ projectId }: AssistantMemoryListProps) {
  const [memories, setMemories] = useState<MemoryItem[]>([])
  const [loading, setLoading] = useState(false)
  const [category, setCategory] = useState('')

  const fetchMemories = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await apiClient.get<{ data: MemoryItem[] }>(
        `/projects/${projectId}/ai/assistant/memories`,
        { category: category || undefined, limit: 100 },
      )
      setMemories(resp.data.data || [])
    } catch {
      message.error('加载记忆失败')
    } finally {
      setLoading(false)
    }
  }, [projectId, category])

  useEffect(() => {
    fetchMemories()
  }, [fetchMemories])

  const handleDelete = async (id: string) => {
    try {
      await apiClient.delete(`/projects/${projectId}/ai/assistant/memories/${id}`)
      message.success('已删除')
      fetchMemories()
    } catch {
      message.error('删除失败')
    }
  }

  const columns = [
    {
      title: '分类',
      dataIndex: 'category',
      key: 'category',
      width: 100,
      render: (cat: string) => (
        <Tag color={CATEGORY_COLORS[cat] || 'default'}>
          {CATEGORY_LABELS[cat] || cat}
        </Tag>
      ),
    },
    {
      title: '标识',
      dataIndex: 'key',
      key: 'key',
      width: 150,
      ellipsis: true,
    },
    {
      title: '内容',
      dataIndex: 'value',
      key: 'value',
      ellipsis: true,
    },
    {
      title: '重要性',
      dataIndex: 'importance',
      key: 'importance',
      width: 70,
      sorter: (a: MemoryItem, b: MemoryItem) => a.importance - b.importance,
      render: (v: number) => <Text type={v >= 7 ? 'danger' : undefined}>{v}</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 60,
      render: (_: unknown, record: MemoryItem) => (
        <Popconfirm title="确认删除此记忆？" onConfirm={() => handleDelete(record.id)}>
          <Button type="text" danger icon={<DeleteOutlined />} size="small" />
        </Popconfirm>
      ),
    },
  ]

  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <Select
          value={category}
          onChange={setCategory}
          options={CATEGORY_OPTIONS}
          style={{ width: 140 }}
          size="small"
        />
        <Button icon={<ReloadOutlined />} size="small" onClick={fetchMemories}>
          刷新
        </Button>
      </Space>
      <Table
        dataSource={memories}
        columns={columns}
        rowKey="id"
        loading={loading}
        size="small"
        pagination={{ pageSize: 20, showSizeChanger: false }}
        locale={{ emptyText: <Empty description="暂无记忆" /> }}
      />
    </div>
  )
}
