import { formatApiDateTime } from '../utils/dateTime'
/* Scheduled tasks management page. */
import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ClockCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'

const { Text } = Typography
const { TextArea } = Input

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

function unwrapApiData<T>(payload: ApiResponse<T> | T): T {
  if (
    payload &&
    typeof payload === 'object' &&
    'data' in payload &&
    'code' in payload
  ) {
    return (payload as ApiResponse<T>).data
  }
  return payload as T
}

interface ScheduledTask {
  id: string
  project_id: string
  name: string
  prompt: string
  cron_expr: string | null
  interval_minutes: number | null
  tool_policy: string[]
  status: string
  last_run_at: string | null
  last_run_status: string | null
  last_run_output: string | null
  next_run_at: string | null
  created_at: string | null
  updated_at: string | null
}

interface ScheduledTasksPageProps {
  projectId: string
}

export function ScheduledTasksPage({ projectId }: ScheduledTasksPageProps) {
  const [tasks, setTasks] = useState<ScheduledTask[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingTask, setEditingTask] = useState<ScheduledTask | null>(null)
  const [logsModalOpen, setLogsModalOpen] = useState(false)
  const [selectedTask, setSelectedTask] = useState<ScheduledTask | null>(null)
  const [runningTaskId, setRunningTaskId] = useState<string | null>(null)
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null)
  const [form] = Form.useForm()

  const fetchTasks = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<{ items: ScheduledTask[]; total: number }> | { items: ScheduledTask[]; total: number }>(
        `/projects/${projectId}/scheduled-tasks`,
      )
      const data = unwrapApiData(res.data)
      setTasks(data?.items || [])
    } catch (err: any) {
      message.error(err.message || '获取定时任务失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    fetchTasks()
  }, [fetchTasks])

  const handleCreate = () => {
    setEditingTask(null)
    form.resetFields()
    setModalOpen(true)
  }

  const handleEdit = (task: ScheduledTask) => {
    setEditingTask(task)
    form.setFieldsValue({
      name: task.name,
      prompt: task.prompt,
      cron_expr: task.cron_expr,
      interval_minutes: task.interval_minutes,
    })
    setModalOpen(true)
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      const payload = {
        name: values.name,
        prompt: values.prompt,
        cron_expr: values.cron_expr || null,
        interval_minutes: values.interval_minutes || null,
      }

      if (editingTask) {
        await apiClient.put(`/projects/${projectId}/scheduled-tasks/${editingTask.id}`, payload)
        message.success('任务已更新')
      } else {
        await apiClient.post(`/projects/${projectId}/scheduled-tasks`, payload)
        message.success('任务已创建')
      }

      setModalOpen(false)
      fetchTasks()
    } catch (err: any) {
      if (err.message) {
        message.error(err.message)
      }
    }
  }

  const handleDelete = async (task: ScheduledTask) => {
    setDeletingTaskId(task.id)
    try {
      await apiClient.delete(`/projects/${projectId}/scheduled-tasks/${task.id}`)
      setTasks((current) => current.filter((item) => item.id !== task.id))
      message.success(`任务“${task.name}”已删除`)
    } catch (err: any) {
      message.error(err.message || '删除失败')
    } finally {
      setDeletingTaskId(null)
    }
  }

  const handleToggleStatus = async (task: ScheduledTask) => {
    try {
      const newStatus = task.status === 'active' ? 'paused' : 'active'
      await apiClient.put(`/projects/${projectId}/scheduled-tasks/${task.id}`, {
        status: newStatus,
      })
      message.success(newStatus === 'active' ? '任务已启用' : '任务已暂停')
      fetchTasks()
    } catch (err: any) {
      message.error(err.message || '操作失败')
    }
  }

  const handleRunNow = async (taskId: string) => {
    setRunningTaskId(taskId)
    try {
      const res = await apiClient.post<ApiResponse<{ status: string; output: string }> | { status: string; output: string }>(
        `/projects/${projectId}/scheduled-tasks/${taskId}/run-now`,
      )
      const data = unwrapApiData(res.data)
      message.success(`任务执行完成：${data?.status || 'completed'}`)
      fetchTasks()
    } catch (err: any) {
      message.error(err.message || '执行失败')
    } finally {
      setRunningTaskId(null)
    }
  }

  const handleShowLogs = (task: ScheduledTask) => {
    setSelectedTask(task)
    setLogsModalOpen(true)
  }

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string) => (
        <Space>
          <ClockCircleOutlined />
          <Text strong>{name}</Text>
        </Space>
      ),
    },
    {
      title: '触发方式',
      key: 'trigger',
      render: (_: unknown, record: ScheduledTask) => {
        if (record.cron_expr) {
          return <Tag color="blue">Cron: {record.cron_expr}</Tag>
        }
        if (record.interval_minutes) {
          return <Tag color="green">每 {record.interval_minutes} 分钟</Tag>
        }
        return <Tag>手动</Tag>
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => (
        <Tag color={status === 'active' ? 'green' : status === 'paused' ? 'orange' : 'red'}>
          {status === 'active' ? '运行中' : status === 'paused' ? '已暂停' : status}
        </Tag>
      ),
    },
    {
      title: '上次运行',
      key: 'last_run',
      render: (_: unknown, record: ScheduledTask) => {
        if (!record.last_run_at) return <Text type="secondary">从未运行</Text>
        return (
          <Space direction="vertical" size={0}>
            <Text>{(formatApiDateTime(record.last_run_at) || '时间未记录')}</Text>
            {record.last_run_status && (
              <Tag color={record.last_run_status === 'completed' ? 'green' : 'red'} style={{ fontSize: 11 }}>
                {record.last_run_status}
              </Tag>
            )}
          </Space>
        )
      },
    },
    {
      title: '下次运行',
      dataIndex: 'next_run_at',
      key: 'next_run',
      render: (nextRun: string | null) =>
        nextRun ? (formatApiDateTime(nextRun) || '时间未记录') : <Text type="secondary">-</Text>,
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: ScheduledTask) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={record.status === 'active' ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
            onClick={() => handleToggleStatus(record)}
          >
            {record.status === 'active' ? '暂停' : '启用'}
          </Button>
          <Button
            type="link"
            size="small"
            icon={<ReloadOutlined />}
            loading={runningTaskId === record.id}
            onClick={() => handleRunNow(record.id)}
          >
            立即运行
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => handleShowLogs(record)}
          >
            日志
          </Button>
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            aria-label={`编辑任务${record.name}`}
            onClick={() => handleEdit(record)}
          >
            编辑
          </Button>
          <Popconfirm
            title={`删除任务“${record.name}”？`}
            description="删除后不会再自动运行；已有运行日志不会被用于恢复任务。"
            onConfirm={() => handleDelete(record)}
            okText="确认删除"
            cancelText="取消"
          >
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              aria-label={`删除任务${record.name}`}
              loading={deletingTaskId === record.id}
              disabled={Boolean(deletingTaskId && deletingTaskId !== record.id)}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <Card
        title="自动任务"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
            创建任务
          </Button>
        }
      >
        <Table
          dataSource={tasks}
          columns={columns}
          rowKey="id"
          loading={loading}
          pagination={false}
          scroll={{ x: 1040 }}
          locale={{ emptyText: <Empty description={'还没有自动任务。点击"创建任务"开始。'} /> }}
        />
      </Card>

      {/* Create/Edit Modal */}
      <Modal
        title={editingTask ? '编辑任务' : '创建任务'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        width={600}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="任务名称"
            rules={[{ required: true, message: '请输入任务名称' }]}
          >
            <Input placeholder="例如：每日灵感搜集" />
          </Form.Item>
          <Form.Item
            name="prompt"
            label="执行提示词"
            rules={[{ required: true, message: '请输入执行提示词' }]}
          >
            <TextArea
              placeholder="告诉AI要做什么，例如：搜索仙侠网文榜单并整理灵感"
              autoSize={{ minRows: 3, maxRows: 6 }}
            />
          </Form.Item>
          <Form.Item
            name="cron_expr"
            label="Cron 表达式（可选）"
            extra="例如：0 22 * * * 表示每天22点执行"
          >
            <Input placeholder="分 时 日 月 周" />
          </Form.Item>
          <Form.Item
            name="interval_minutes"
            label="间隔分钟（可选）"
            extra="如果设置了 Cron 表达式，此项会被忽略"
          >
            <InputNumber min={1} placeholder="例如：60" style={{ width: '100%' }} />
          </Form.Item>
          <Alert
            type="info"
            showIcon
            message="触发方式"
            description="Cron 表达式优先级高于间隔分钟。如果两者都不设置，任务只能手动运行。"
          />
        </Form>
      </Modal>

      {/* Logs Modal */}
      <Modal
        title={`运行日志 - ${selectedTask?.name || ''}`}
        open={logsModalOpen}
        onCancel={() => setLogsModalOpen(false)}
        footer={null}
        width={600}
      >
        {selectedTask ? (
          <div>
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              <div>
                <Text strong>上次运行时间：</Text>
                <Text>{selectedTask.last_run_at ? (formatApiDateTime(selectedTask.last_run_at) || '时间未记录') : '从未运行'}</Text>
              </div>
              <div>
                <Text strong>运行状态：</Text>
                <Tag color={selectedTask.last_run_status === 'completed' ? 'green' : 'red'}>
                  {selectedTask.last_run_status || '-'}
                </Tag>
              </div>
              <div>
                <Text strong>输出：</Text>
                <pre style={{
                  background: '#f5f5f5',
                  padding: 12,
                  borderRadius: 4,
                  maxHeight: 400,
                  overflow: 'auto',
                  fontSize: 12,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                }}>
                  {selectedTask.last_run_output || '无输出'}
                </pre>
              </div>
            </Space>
          </div>
        ) : (
          <Empty description="选择一个任务查看日志" />
        )}
      </Modal>
    </div>
  )
}
