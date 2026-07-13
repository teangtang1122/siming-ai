import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Space,
  Spin,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import type { UploadFile } from 'antd'
import {
  BookOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  RobotOutlined,
  SearchOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import SystemNav from '../components/SystemNav'
import PageWrapper from '../components/PageWrapper'
import { apiClient } from '../api/client'
import { useAppStore } from '../stores'
import './DashboardPage.css'

const { Text, Title } = Typography
const { TextArea } = Input

interface ProjectFormValues {
  title: string
  description?: string
  tags?: string
}

interface NovelCreationDraftSummary {
  id: string
  current_stage?: string
  updated_at?: string
  draft?: {
    form?: { brief?: string; genre?: string }
    concepts?: Array<{ title?: string }>
  }
}

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface UploadResult {
  filename: string
  format: string
  text: string
  word_count: number
}

interface SplitItem {
  title: string
  start_char: number
  end_char: number
  preview: string
  needs_review?: boolean
  review_reason?: string
  source?: string
  block_index?: number
}

interface SplitResult {
  splits: SplitItem[]
  total: number
  method: string
  needs_review: boolean
  failed_blocks: number
}

interface ConfirmResult {
  chapters: Array<{ id: string; title: string; word_count: number }>
  total: number
}

function parseTags(value?: string) {
  return (value || '')
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function tagsPayload(value?: string) {
  const tags = parseTags(value)
  return tags.length ? tags : undefined
}

function tagsToFormValue(tagsStr?: string) {
  if (!tagsStr) return ''
  try {
    const tags = JSON.parse(tagsStr)
    return Array.isArray(tags) ? tags.join('，') : ''
  } catch {
    return ''
  }
}

function titleFromFile(file: File) {
  return file.name.replace(/\.(txt|docx)$/i, '').trim()
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
  const [creating, setCreating] = useState(false)
  const [importStatus, setImportStatus] = useState('')
  const [pendingImportFile, setPendingImportFile] = useState<File | null>(null)
  const [creationDrafts, setCreationDrafts] = useState<NovelCreationDraftSummary[]>([])
  const [editingProject, setEditingProject] = useState<{
    id: string
    title: string
    description?: string
    tags?: string
  } | null>(null)
  const [form] = Form.useForm<ProjectFormValues>()
  const [editForm] = Form.useForm<ProjectFormValues>()

  useEffect(() => {
    fetchProjects()
  }, [fetchProjects])

  useEffect(() => {
    const loadCreationDrafts = async () => {
      try {
        const response = await apiClient.get<ApiResponse<{ sessions: NovelCreationDraftSummary[] }>>('/novel-creation/sessions')
        setCreationDrafts(response.data.data.sessions || [])
      } catch {
        setCreationDrafts([])
      }
    }
    void loadCreationDrafts()
  }, [])

  const pendingUploadList = useMemo<UploadFile[]>(() => {
    if (!pendingImportFile) return []
    return [{
      uid: 'pending-import-file',
      name: pendingImportFile.name,
      status: 'done',
    }]
  }, [pendingImportFile])

  const handleSearch = (value: string) => {
    setSearchKeyword(value)
    fetchProjects(value)
  }

  const closeCreateModal = () => {
    if (creating) return
    setIsCreateModalOpen(false)
    setPendingImportFile(null)
    setImportStatus('')
    form.resetFields()
  }

  const openCreateModal = (draft?: Partial<ProjectFormValues>, file?: File) => {
    setIsCreateModalOpen(true)
    setImportStatus('')
    if (file) setPendingImportFile(file)
    if (draft) form.setFieldsValue(draft)
  }

  const openNovelCreation = () => {
    navigate('/novel-creation')
  }

  const deleteCreationDraft = async (sessionId: string) => {
    try {
      await apiClient.delete(`/novel-creation/sessions/${sessionId}`)
      setCreationDrafts((items) => items.filter((item) => item.id !== sessionId))
      message.success('立项草稿已删除')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '删除立项草稿失败')
    }
  }

  const attachImportFile = (file: File) => {
    setPendingImportFile(file)
    const currentTitle = form.getFieldValue('title')
    if (!currentTitle) {
      form.setFieldsValue({ title: titleFromFile(file) })
    }
    return false
  }

  const importFileIntoProject = async (projectId: string, file: File) => {
    setImportStatus('正在解析文件...')
    const formData = new FormData()
    formData.append('file', file)
    const uploadRes = await fetch(`/api/v1/projects/${projectId}/import/file`, {
      method: 'POST',
      body: formData,
    })
    if (!uploadRes.ok) throw new Error('文件解析失败')
    const uploadData = await uploadRes.json() as ApiResponse<UploadResult>
    if (uploadData.code !== 0 || !uploadData.data?.text) {
      throw new Error(uploadData.message || '文件解析失败')
    }

    let splits: SplitItem[] = []
    if (uploadData.data.text.length >= 100) {
      try {
        setImportStatus('正在识别章节...')
        const splitRes = await apiClient.post<ApiResponse<SplitResult>>(`/projects/${projectId}/import/preview`, {
          text: uploadData.data.text,
        })
        splits = splitRes.data.data.splits || []
      } catch {
        splits = []
      }
    }

    setImportStatus('正在写入章节...')
    const confirmRes = await apiClient.post<ApiResponse<ConfirmResult>>(`/projects/${projectId}/import/confirm`, {
      text: uploadData.data.text,
      splits,
    })
    return {
      filename: uploadData.data.filename,
      wordCount: uploadData.data.word_count,
      chapterCount: confirmRes.data.data.total,
    }
  }

  const handleCreate = async (values: ProjectFormValues) => {
    const payload: Record<string, unknown> = {
      title: values.title,
      description: values.description || '',
      tags: tagsPayload(values.tags),
    }

    setCreating(true)
    setImportStatus(pendingImportFile ? '正在创建作品...' : '')
    let createdProjectId: string | null = null
    try {
      const project = await createProject(payload)
      if (!project) return
      createdProjectId = project.id

      if (pendingImportFile) {
        try {
          const imported = await importFileIntoProject(project.id, pendingImportFile)
          message.success(`作品已创建，并导入 ${imported.chapterCount} 章`)
        } catch (error: unknown) {
          const detail = error instanceof Error ? error.message : '未知错误'
          message.warning(`作品已创建，但文件导入失败：${detail}`)
        }
      } else {
        message.success('作品创建成功')
      }

      await fetchProjects(searchKeyword || undefined)
      setIsCreateModalOpen(false)
      setPendingImportFile(null)
      setImportStatus('')
      form.resetFields()
      navigate(`/project/${project.id}`)
    } finally {
      setCreating(false)
      if (!createdProjectId) setImportStatus('')
    }
  }

  const handleEdit = async (values: ProjectFormValues) => {
    if (!editingProject) return
    const payload: Record<string, unknown> = {
      title: values.title,
      description: values.description || '',
      tags: tagsPayload(values.tags),
    }
    const project = await updateProject(editingProject.id, payload)
    if (project) {
      message.success('作品已更新')
      setIsEditModalOpen(false)
      setEditingProject(null)
    }
  }

  const handleDelete = async (id: string) => {
    if (await deleteProject(id)) message.success('作品已删除')
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
      tags: tagsToFormValue(project.tags),
    })
    setIsEditModalOpen(true)
  }

  const renderTags = (tagsStr?: string) => {
    const tags = tagsToFormValue(tagsStr).split('，').filter(Boolean)
    if (!tags.length) return null
    return (
      <Space size={4} style={{ flexWrap: 'wrap' }}>
        {tags.map((tag) => <Tag key={tag} style={{ fontSize: 12 }}>{tag}</Tag>)}
      </Space>
    )
  }

  return (
    <PageWrapper>
      <div className="dashboard-bg-pattern" />
      <SystemNav current="dashboard" />

      <div className="dashboard-hero">
        <h1 className="dashboard-hero-title"><BookOutlined />司命</h1>
        <p className="dashboard-hero-sub">长篇小说的命运织机</p>
      </div>

      <div className="dashboard-actions siming-animate-in siming-stagger-1">
        <Input.Search
          placeholder="搜索作品标题或简介"
          allowClear
          enterButton={<><SearchOutlined /> 搜索</>}
          size="large"
          value={searchKeyword}
          onChange={(event) => setSearchKeyword(event.target.value)}
          onSearch={handleSearch}
        />
        <Space wrap>
          <Button icon={<RobotOutlined />} size="large" onClick={openNovelCreation}>新书立项助手</Button>
          <Button type="primary" icon={<PlusOutlined />} size="large" className="siming-btn-press" onClick={openNovelCreation}>创建新作品</Button>
        </Space>
      </div>

      {creationDrafts.length > 0 && (
        <section className="dashboard-creation-drafts siming-animate-in siming-stagger-1">
          <div className="dashboard-creation-drafts-head">
            <div>
              <Title level={4}>继续未完成立项</Title>
              <Text type="secondary">立项草稿不会创建空作品，可继续生成或直接删除。</Text>
            </div>
            <Button type="link" onClick={openNovelCreation}>新建立项</Button>
          </div>
          <div className="dashboard-creation-drafts-grid">
            {creationDrafts.slice(0, 4).map((draft) => (
              <Card
                key={draft.id}
                size="small"
                hoverable
                onClick={() => navigate(`/novel-creation?session=${draft.id}`)}
                title={draft.draft?.concepts?.[0]?.title || draft.draft?.form?.brief?.slice(0, 28) || '未命名立项'}
                extra={(
                  <Button
                    type="text"
                    danger
                    size="small"
                    icon={<DeleteOutlined />}
                    aria-label="删除立项草稿"
                    onClick={(event) => {
                      event.stopPropagation()
                      void deleteCreationDraft(draft.id)
                    }}
                  />
                )}
              >
                <Space direction="vertical" size={2}>
                  <Text>{draft.draft?.form?.genre || '自由创作'}</Text>
                  <Text type="secondary">{draft.current_stage || '创作约束'} · {draft.updated_at ? new Date(draft.updated_at).toLocaleString('zh-CN') : '刚刚保存'}</Text>
                </Space>
              </Card>
            ))}
          </div>
        </section>
      )}

      {loading ? (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" />
          <div style={{ marginTop: 16, color: 'var(--ant-color-text-secondary)', fontSize: 15 }}>加载中...</div>
        </div>
      ) : projects.length === 0 ? (
        <div className="dashboard-empty siming-animate-fade">
          <Empty
            description={searchKeyword ? '未找到匹配的作品' : '暂无作品。可以创建空项目、上传已有小说，也可以让新书立项助手先生成角色、世界观和大纲。'}
          >
            {!searchKeyword && (
              <Space>
                <Button type="primary" icon={<RobotOutlined />} size="large" onClick={openNovelCreation}>让助手帮我开书</Button>
                <Button icon={<PlusOutlined />} size="large" onClick={() => openCreateModal()}>直接创建</Button>
              </Space>
            )}
          </Empty>
        </div>
      ) : (
        <div className="dashboard-grid">
          {projects.map((project) => (
            <div key={project.id} className="dashboard-card-wrap">
              <Card
                className="dashboard-card"
                hoverable
                onClick={() => navigate(`/project/${project.id}`)}
                title={(
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', minWidth: 0 }}>
                    <span className="dashboard-card-title">{project.title}</span>
                    <Space size={4} onClick={(event) => event.stopPropagation()}>
                      <Button type="text" size="small" aria-label={`编辑 ${project.title}`} icon={<EditOutlined />} onClick={() => openEditModal(project)} />
                      <Button
                        type="text"
                        size="small"
                        danger
                        aria-label={`删除 ${project.title}`}
                        icon={<DeleteOutlined />}
                        onClick={(event) => {
                          event.stopPropagation()
                          Modal.confirm({
                            title: '确认删除作品',
                            content: `确定要删除《${project.title}》吗？删除作品会同时删除关联的角色、大纲、章节等数据，此操作不可恢复。`,
                            okText: '删除',
                            cancelText: '取消',
                            okButtonProps: { danger: true },
                            onOk: () => handleDelete(project.id),
                          })
                        }}
                      />
                    </Space>
                  </div>
                )}
              >
                <p className="dashboard-card-desc">{project.description || '暂无简介'}</p>
                {renderTags(project.tags)}
                <div className="dashboard-card-meta">
                  <Text type="secondary">更新于 {new Date(project.updated_at).toLocaleDateString('zh-CN')}</Text>
                  <Text type="secondary">{new Date(project.created_at).toLocaleDateString('zh-CN')} 创建</Text>
                </div>
              </Card>
            </div>
          ))}
        </div>
      )}

      <Modal
        title="创建作品"
        open={isCreateModalOpen}
        onCancel={closeCreateModal}
        onOk={() => form.submit()}
        okText={pendingImportFile ? '创建并导入' : '创建'}
        cancelText="取消"
        okButtonProps={{ autoInsertSpace: false, loading: creating }}
        cancelButtonProps={{ autoInsertSpace: false, disabled: creating }}
        closable={!creating}
        width={720}
        maskClosable={!creating}
        destroyOnHidden
        transitionName=""
        maskTransitionName=""
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="title" label="作品标题" rules={[{ required: true, message: '请输入作品标题' }]}>
            <Input placeholder="请输入作品标题" maxLength={200} showCount />
          </Form.Item>
          <Form.Item name="description" label="作品简介">
            <TextArea placeholder="写下核心卖点、主角设定或创作方向" rows={4} maxLength={500} showCount />
          </Form.Item>
          <Form.Item name="tags" label="类型标签">
            <Input placeholder="多个标签用逗号分隔，如：玄幻，修仙，热血" />
          </Form.Item>
          <Form.Item label="导入已有小说（可选）">
            <Upload
              accept=".txt,.docx"
              maxCount={1}
              fileList={pendingUploadList}
              beforeUpload={(file) => attachImportFile(file as File)}
              onRemove={() => {
                setPendingImportFile(null)
                return true
              }}
            >
              <Button icon={<UploadOutlined />}>选择 TXT / DOCX 文件</Button>
            </Upload>
            <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
              创建成功后会自动解析章节并写入当前作品。若导入失败，作品仍会保留。
            </Text>
          </Form.Item>
          {importStatus && <Alert type="info" showIcon message={importStatus} style={{ marginTop: 8 }} />}
        </Form>
      </Modal>

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
          <Form.Item name="title" label="作品标题" rules={[{ required: true, message: '请输入作品标题' }]}>
            <Input placeholder="请输入作品标题" maxLength={200} showCount />
          </Form.Item>
          <Form.Item name="description" label="作品简介">
            <TextArea placeholder="请输入作品简介" rows={3} maxLength={500} showCount />
          </Form.Item>
          <Form.Item name="tags" label="类型标签">
            <Input placeholder="多个标签用逗号分隔，如：玄幻，修仙，热血" />
          </Form.Item>
        </Form>
      </Modal>
    </PageWrapper>
  )
}

export default DashboardPage
