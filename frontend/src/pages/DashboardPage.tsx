import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Card,
  Button,
  Empty,
  Spin,
  Input,
  Modal,
  Form,
  message,
  Popconfirm,
  Space,
  Typography,
  Tag,
} from 'antd'
import {
  PlusOutlined,
  SearchOutlined,
  EditOutlined,
  DeleteOutlined,
  BookOutlined,
} from '@ant-design/icons'
import SystemNav from '../components/SystemNav'
import { useAppStore } from '../stores'

const { Text } = Typography

interface ProjectFormValues {
  title: string
  description?: string
  tags?: string
}

function DashboardPage() {
  const navigate = useNavigate()
  const {
    projects,
    loading,
    fetchProjects,
    createProject,
    updateProject,
    deleteProject,
  } = useAppStore()

  const [searchKeyword, setSearchKeyword] = useState('')
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [editingProject, setEditingProject] = useState<{
    id: string
    title: string
    description?: string
    tags?: string
  } | null>(null)
  const [form] = Form.useForm()
  const [editForm] = Form.useForm()

  useEffect(() => {
    fetchProjects()
  }, [fetchProjects])

  const handleSearch = (value: string) => {
    setSearchKeyword(value)
    fetchProjects(value)
  }

  const handleCreate = async (values: ProjectFormValues) => {
    const payload: Record<string, unknown> = {
      title: values.title,
      description: values.description || '',
    }
    if (values.tags) {
      payload.tags = values.tags.split(/[,，]/).map((t) => t.trim()).filter(Boolean)
    }
    const project = await createProject(payload)
    if (project) {
      message.success('作品创建成功')
      setIsCreateModalOpen(false)
      form.resetFields()
      navigate(`/project/${project.id}`)
    }
  }

  const handleEdit = async (values: ProjectFormValues) => {
    if (!editingProject) return
    const payload: Record<string, unknown> = {
      title: values.title,
      description: values.description || '',
    }
    if (values.tags) {
      payload.tags = values.tags.split(/[,，]/).map((t) => t.trim()).filter(Boolean)
    }
    const project = await updateProject(editingProject.id, payload)
    if (project) {
      message.success('作品更新成功')
      setIsEditModalOpen(false)
      setEditingProject(null)
    }
  }

  const handleDelete = async (id: string) => {
    const success = await deleteProject(id)
    if (success) {
      message.success('作品已删除')
    }
  }

  const openEditModal = (project: {
    id: string
    title: string
    description?: string
    tags?: string
  }) => {
    setEditingProject(project)
    editForm.setFieldsValue({
      title: project.title,
      description: project.description || '',
      tags: project.tags || '',
    })
    setIsEditModalOpen(true)
  }

  const renderTags = (tagsStr?: string) => {
    if (!tagsStr) return null
    try {
      const tags = JSON.parse(tagsStr) as string[]
      return (
        <Space size={4} style={{ marginTop: 8, flexWrap: 'wrap' }}>
          {tags.map((tag) => (
            <Tag key={tag} color="blue" style={{ fontSize: 12 }}>
              {tag}
            </Tag>
          ))}
        </Space>
      )
    } catch {
      return null
    }
  }

  return (
    <div style={{ padding: 24, maxWidth: 1200, margin: '0 auto' }}>
      <SystemNav current="dashboard" />

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1 style={{ margin: 0 }}>
          <BookOutlined style={{ marginRight: 8 }} />
          作品管理
        </h1>
        <Space size={12}>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            size="large"
            onClick={() => setIsCreateModalOpen(true)}
          >
            创建新作品
          </Button>
        </Space>
      </div>

      <Input.Search
        placeholder="搜索作品标题或简介"
        allowClear
        enterButton={<><SearchOutlined /> 搜索</>}
        size="large"
        style={{ marginBottom: 24 }}
        value={searchKeyword}
        onChange={(e) => setSearchKeyword(e.target.value)}
        onSearch={handleSearch}
      />

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin size="large" />
          <div style={{ marginTop: 12, color: '#666' }}>加载中...</div>
        </div>
      ) : projects.length === 0 ? (
        <Empty
          description={
            searchKeyword
              ? '未找到匹配的作品'
              : '暂无作品，点击上方按钮创建'
          }
          style={{ padding: 60 }}
        >
          {!searchKeyword && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setIsCreateModalOpen(true)}>
              立即创建
            </Button>
          )}
        </Empty>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16 }}>
          {projects.map((project) => (
            <Card
              key={project.id}
              title={
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontWeight: 'bold', fontSize: 16 }}>{project.title}</span>
                  <Space size={4}>
                    <Button
                      type="text"
                      size="small"
                      icon={<EditOutlined />}
                      onClick={(e) => {
                        e.stopPropagation()
                        openEditModal(project)
                      }}
                    />
                    <Popconfirm
                      title="确认删除"
                      description="删除作品将同时删除其所有关联数据（世界观、角色、大纲、章节等），此操作不可恢复。"
                      onConfirm={(e) => {
                        e?.stopPropagation()
                        handleDelete(project.id)
                      }}
                      onCancel={(e) => e?.stopPropagation()}
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true, autoInsertSpace: false }}
                      cancelButtonProps={{ autoInsertSpace: false }}
                    >
                      <Button
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </Popconfirm>
                  </Space>
                </div>
              }
              hoverable
              onClick={() => navigate(`/project/${project.id}`)}
              bodyStyle={{ minHeight: 100 }}
            >
              <p style={{ color: '#666', minHeight: 40, marginBottom: 8 }}>
                {project.description || '暂无简介'}
              </p>
              {renderTags(project.tags)}
              <div style={{ marginTop: 12, fontSize: 12, color: '#999', display: 'flex', justifyContent: 'space-between' }}>
                <Text type="secondary">更新于 {new Date(project.updated_at).toLocaleDateString('zh-CN')}</Text>
                <Text type="secondary">{new Date(project.created_at).toLocaleDateString('zh-CN')} 创建</Text>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Create Project Modal */}
      <Modal
        title="创建作品"
        open={isCreateModalOpen}
        onCancel={() => {
          setIsCreateModalOpen(false)
          form.resetFields()
        }}
        onOk={() => form.submit()}
        okText="创建"
        cancelText="取消"
        okButtonProps={{ autoInsertSpace: false }}
        cancelButtonProps={{ autoInsertSpace: false }}
        transitionName=""
        maskTransitionName=""
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item
            name="title"
            label="作品标题"
            rules={[{ required: true, message: '请输入作品标题' }]}
          >
            <Input placeholder="请输入作品标题" maxLength={200} showCount />
          </Form.Item>
          <Form.Item name="description" label="作品简介">
            <Input.TextArea placeholder="请输入作品简介" rows={3} maxLength={500} showCount />
          </Form.Item>
          <Form.Item name="tags" label="类型标签">
            <Input placeholder="多个标签用逗号分隔，如：玄幻, 修仙, 热血" />
          </Form.Item>
        </Form>
      </Modal>

      {/* Edit Project Modal */}
      <Modal
        title="编辑作品"
        open={isEditModalOpen}
        onCancel={() => {
          setIsEditModalOpen(false)
          setEditingProject(null)
        }}
        onOk={() => editForm.submit()}
        okText="保存"
        cancelText="取消"
        okButtonProps={{ autoInsertSpace: false }}
        cancelButtonProps={{ autoInsertSpace: false }}
        transitionName=""
        maskTransitionName=""
      >
        <Form form={editForm} layout="vertical" onFinish={handleEdit}>
          <Form.Item
            name="title"
            label="作品标题"
            rules={[{ required: true, message: '请输入作品标题' }]}
          >
            <Input placeholder="请输入作品标题" maxLength={200} showCount />
          </Form.Item>
          <Form.Item name="description" label="作品简介">
            <Input.TextArea placeholder="请输入作品简介" rows={3} maxLength={500} showCount />
          </Form.Item>
          <Form.Item name="tags" label="类型标签">
            <Input placeholder="多个标签用逗号分隔，如：玄幻, 修仙, 热血" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default DashboardPage
