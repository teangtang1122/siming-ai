import { type KeyboardEvent, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
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
  ArrowRightOutlined,
  DeleteOutlined,
  EditOutlined,
  FileZipOutlined,
  FolderOpenOutlined,
  PlusOutlined,
  RocketOutlined,
  SearchOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import SystemNav from '../components/SystemNav'
import PageWrapper from '../components/PageWrapper'
import { AuthorEmptyState } from '../components/interaction'
import { apiClient } from '../api/client'
import {
  useCreateProject,
  useDeleteProject,
  useProjects,
  useUpdateProject,
} from '../features/projects'
import type { ProjectCreateDraft, ProjectUpdateInput } from '../features/projects'
import { useGettingStartedSummary } from '../features/onboarding'
import { QueryStateNotice } from '../shared/ui/runtime'
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
  stage_flow?: {
    attention_stage?: string | null
    pending_confirmations?: string[]
    items?: Record<string, { label?: string; status?: string }>
  }
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

interface ExternalImportResult {
  project_id: string
  total: number
}

interface ProjectPackageImportResult {
  project_id: string
  project_title: string
  replayed: boolean
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

function tagsToFormValue(tagsStr?: string | null) {
  if (!tagsStr) return ''
  try {
    const tags = JSON.parse(tagsStr)
    return Array.isArray(tags) ? tags.join('，') : ''
  } catch {
    return ''
  }
}

function titleFromFile(file: File) {
  return file.name.replace(/\.(txt|md|docx)$/i, '').trim()
}

function createIdempotencyKey() {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (token) => {
    const value = Math.floor(Math.random() * 16)
    return (token === 'x' ? value : (value & 0x3) | 0x8).toString(16)
  })
}

function DashboardPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [searchKeyword, setSearchKeyword] = useState('')
  const [appliedSearch, setAppliedSearch] = useState('')
  const projectsQuery = useProjects(appliedSearch || undefined)
  const createProjectMutation = useCreateProject()
  const updateProjectMutation = useUpdateProject()
  const deleteProjectMutation = useDeleteProject()
  const setupQuery = useGettingStartedSummary()
  const projects = projectsQuery.data?.items || []
  const loading = projectsQuery.isLoading || projectsQuery.isFetching
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [isExternalImportOpen, setIsExternalImportOpen] = useState(false)
  const [isPackageImportOpen, setIsPackageImportOpen] = useState(false)
  const [importing, setImporting] = useState(false)
  const [importStatus, setImportStatus] = useState('')
  const [externalImportFile, setExternalImportFile] = useState<File | null>(null)
  const [packageImportFile, setPackageImportFile] = useState<File | null>(null)
  const [packageTitle, setPackageTitle] = useState('')
  const [packageRequestKey, setPackageRequestKey] = useState(createIdempotencyKey)
  const [creationDrafts, setCreationDrafts] = useState<NovelCreationDraftSummary[]>([])
  const [editingProject, setEditingProject] = useState<{
    id: string
    title: string
    description?: string | null
    tags?: string | null
  } | null>(null)
  const [form] = Form.useForm<ProjectFormValues>()
  const [editForm] = Form.useForm<ProjectFormValues>()

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

  const needsModelSetup = Boolean(setupQuery.data?.needs_setup)

  const externalUploadList = useMemo<UploadFile[]>(() => {
    if (!externalImportFile) return []
    return [{
      uid: 'external-import-file',
      name: externalImportFile.name,
      status: 'done',
    }]
  }, [externalImportFile])

  const packageUploadList = useMemo<UploadFile[]>(() => {
    if (!packageImportFile) return []
    return [{
      uid: 'project-package-import-file',
      name: packageImportFile.name,
      status: 'done',
    }]
  }, [packageImportFile])

  const handleSearch = (value: string) => {
    setSearchKeyword(value)
    setAppliedSearch(value.trim())
  }

  const closeCreateModal = () => {
    if (creating) return
    setIsCreateModalOpen(false)
    form.resetFields()
  }

  const openCreateModal = (draft?: Partial<ProjectFormValues>) => {
    setIsCreateModalOpen(true)
    if (draft) form.setFieldsValue(draft)
  }

  const openExternalImport = () => {
    setExternalImportFile(null)
    setImportStatus('')
    setIsExternalImportOpen(true)
  }

  const openPackageImport = () => {
    setPackageImportFile(null)
    setPackageTitle('')
    setPackageRequestKey(createIdempotencyKey())
    setImportStatus('')
    setIsPackageImportOpen(true)
  }

  useEffect(() => {
    if (searchParams.get('create') !== 'import') return
    openExternalImport()
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.delete('create')
      return next
    }, { replace: true })
  // openExternalImport only updates local modal state; URL is the durable route contract.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, setSearchParams])

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

  const attachExternalImportFile = (file: File) => {
    if (!/\.(txt|md|docx)$/i.test(file.name)) {
      message.error('导入外部小说仅支持 TXT、Markdown 和 DOCX')
      return Upload.LIST_IGNORE
    }
    setExternalImportFile(file)
    return false
  }

  const attachPackageImportFile = (file: File) => {
    if (!file.name.toLowerCase().endsWith('.siming-project')) {
      message.error('这里只接受 .siming-project；文稿请使用“导入外部小说”')
      return Upload.LIST_IGNORE
    }
    setPackageImportFile(file)
    setPackageRequestKey(createIdempotencyKey())
    return false
  }

  const handleExternalImport = async () => {
    if (!externalImportFile) {
      message.warning('请先选择要导入的外部小说文件')
      return
    }
    setImporting(true)
    setImportStatus('正在创建作品并识别章节...')
    const formData = new FormData()
    formData.append('file', externalImportFile)
    try {
      const response = await apiClient.postForm<ApiResponse<ExternalImportResult>>(
        '/import/project-file',
        formData,
      )
      const result = response.data.data
      message.success(`已创建作品并导入 ${result.total} 章`)
      setIsExternalImportOpen(false)
      setExternalImportFile(null)
      setImportStatus('')
      navigate(`/project/${result.project_id}`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '外部小说导入失败')
    } finally {
      setImporting(false)
    }
  }

  const handlePackageImport = async () => {
    if (!packageImportFile) {
      message.warning('请先选择司命项目包')
      return
    }
    setImporting(true)
    setImportStatus('正在校验并恢复司命项目包...')
    const formData = new FormData()
    formData.append('file', packageImportFile)
    if (packageTitle.trim()) formData.append('new_title', packageTitle.trim())
    try {
      const response = await apiClient.postForm<ApiResponse<ProjectPackageImportResult>>(
        '/projects/project-package/import',
        formData,
        { headers: { 'Idempotency-Key': packageRequestKey } },
      )
      const result = response.data.data
      message.success(result.replayed ? '已恢复上次导入结果' : `项目包已导入：${result.project_title}`)
      setIsPackageImportOpen(false)
      setPackageImportFile(null)
      setPackageTitle('')
      setImportStatus('')
      navigate(`/project/${result.project_id}`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '司命项目包导入失败')
    } finally {
      setImporting(false)
    }
  }

  const handleCreate = async (values: ProjectFormValues) => {
    const payload: ProjectCreateDraft = {
      title: values.title,
      description: values.description || '',
      tags: tagsPayload(values.tags),
    }

    setCreating(true)
    try {
      const project = await createProjectMutation.mutateAsync(payload)
      message.success('作品创建成功')
      setIsCreateModalOpen(false)
      form.resetFields()
      navigate(`/project/${project.id}`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '创建作品失败')
    } finally {
      setCreating(false)
    }
  }

  const handleEdit = async (values: ProjectFormValues) => {
    if (!editingProject) return
    const payload: ProjectUpdateInput = {
      title: values.title,
      description: values.description || '',
      tags: tagsPayload(values.tags),
    }
    try {
      await updateProjectMutation.mutateAsync({ projectId: editingProject.id, payload })
      message.success('作品已更新')
      setIsEditModalOpen(false)
      setEditingProject(null)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '更新作品失败')
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await deleteProjectMutation.mutateAsync(id)
      message.success('作品已删除')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '删除作品失败')
    }
  }

  const openEditModal = (project: {
    id: string
    title: string
    description?: string | null
    tags?: string | null
  }) => {
    setEditingProject(project)
    editForm.setFieldsValue({
      title: project.title,
      description: project.description || '',
      tags: tagsToFormValue(project.tags),
    })
    setIsEditModalOpen(true)
  }

  const renderTags = (tagsStr?: string | null) => {
    const tags = tagsToFormValue(tagsStr).split('，').filter(Boolean)
    if (!tags.length) return null
    return (
      <Space size={4} style={{ flexWrap: 'wrap' }}>
        {tags.map((tag) => <Tag key={tag} style={{ fontSize: 12 }}>{tag}</Tag>)}
      </Space>
    )
  }

  const openFromKeyboard = (event: KeyboardEvent<HTMLElement>, action: () => void) => {
    if (event.target !== event.currentTarget) return
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    action()
  }

  return (
    <PageWrapper maxWidth={1280} className="dashboard-page">
      <SystemNav current="dashboard" />

      <header className="siming-section-header dashboard-heading">
        <div>
          <span className="siming-section-kicker">创作工作区</span>
          <Title level={2}>作品库</Title>
          <p className="siming-section-description">
            从上次停下的位置继续，或开始一本新书。立项草稿在正式创建前不会污染作品库。
          </p>
        </div>
        <Space wrap>
          <Button icon={<UploadOutlined />} size="large" onClick={openExternalImport}>
            导入外部小说
          </Button>
          <Button icon={<FileZipOutlined />} size="large" onClick={openPackageImport}>
            导入司命项目包
          </Button>
          <Button icon={<PlusOutlined />} size="large" onClick={() => openCreateModal()}>
            直接创建
          </Button>
          <Button type="primary" icon={<RocketOutlined />} size="large" onClick={openNovelCreation}>
            创建新作品
          </Button>
        </Space>
      </header>

      {needsModelSetup && (
        <section className="dashboard-setup-banner" aria-label="首次使用设置">
          <div className="dashboard-setup-icon" aria-hidden="true"><RocketOutlined /></div>
          <div className="dashboard-setup-copy">
            <Text strong>想让 AI 一起创作？先完成一次免费准备</Text>
            <Text type="secondary">不用 API Key，也不用打开命令行。准备完成后，新书立项和项目助手都会自动使用可用模型。</Text>
          </div>
          <Button icon={<RocketOutlined />} onClick={() => navigate('/getting-started')}>
            免费准备 AI <ArrowRightOutlined />
          </Button>
        </section>
      )}

      <div className="dashboard-toolbar">
        <Input.Search
          placeholder="搜索作品标题或简介"
          allowClear
          enterButton={<><SearchOutlined /> 搜索</>}
          size="large"
          value={searchKeyword}
          onChange={(event) => setSearchKeyword(event.target.value)}
          onSearch={handleSearch}
        />
        <Text type="secondary">{projects.length} 部作品{creationDrafts.length > 0 ? ` · ${creationDrafts.length} 个待续立项` : ''}</Text>
      </div>

      {projectsQuery.isError && (
        <QueryStateNotice
          error={projectsQuery.error}
          title="作品列表暂时无法加载"
          onRetry={() => { void projectsQuery.refetch() }}
        />
      )}

      {creationDrafts.length > 0 && (
        <section className="dashboard-creation-drafts" aria-labelledby="creation-drafts-title">
          <div className="dashboard-creation-drafts-head">
            <div>
              <Title level={4} id="creation-drafts-title">继续立项</Title>
              <Text type="secondary">你的回答和生成进度已经保存。</Text>
            </div>
            <Button type="link" icon={<PlusOutlined />} onClick={openNovelCreation}>新建立项</Button>
          </div>
          <div className="dashboard-creation-drafts-grid">
            {creationDrafts.slice(0, 4).map((draft) => (
              <Card
                key={draft.id}
                size="small"
                hoverable
                role="link"
                tabIndex={0}
                aria-label={`继续立项：${draft.draft?.concepts?.[0]?.title || draft.draft?.form?.brief?.slice(0, 28) || '未命名立项'}`}
                onClick={() => navigate(`/novel-creation?session=${draft.id}`)}
                onKeyDown={(event) => openFromKeyboard(event, () => navigate(`/novel-creation?session=${draft.id}`))}
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
                  <Text type="secondary">
                    {(() => {
                      const attention = draft.stage_flow?.attention_stage
                      const item = attention ? draft.stage_flow?.items?.[attention] : undefined
                      const prefix = item?.status === 'generated' || item?.status === 'stale' ? '待你确认：' : ''
                      return `${prefix}${item?.label || draft.current_stage || '创作约束'}`
                    })()} · {draft.updated_at ? new Date(draft.updated_at).toLocaleString('zh-CN') : '刚刚保存'}
                  </Text>
                  <Text className="dashboard-draft-continue">继续完善 <ArrowRightOutlined /></Text>
                </Space>
              </Card>
            ))}
          </div>
        </section>
      )}

      {loading ? (
          <div className="dashboard-loading" role="status" aria-live="polite">
            <Spin size="large" />
            <div>正在载入作品...</div>
          </div>
        ) : projects.length === 0 ? (
        <div className="dashboard-empty siming-surface">
          <AuthorEmptyState
            image={<FolderOpenOutlined className="dashboard-empty-icon" />}
            description={searchKeyword ? '没有找到匹配的作品' : '作品库还是空的。使用上方“创建新作品”开始立项，或直接导入已有小说。'}
          />
        </div>
      ) : (
        <div className="dashboard-grid">
          {projects.map((project) => (
            <div key={project.id} className="dashboard-card-wrap">
              <Card
                className="dashboard-card"
                role="link"
                tabIndex={0}
                aria-label={`打开作品：${project.title}`}
                onClick={() => navigate(`/project/${project.id}`)}
                onKeyDown={(event) => openFromKeyboard(event, () => navigate(`/project/${project.id}`))}
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
                <div className="dashboard-card-footer">
                  <div className="dashboard-card-meta">
                    <Text type="secondary">{new Date(project.updated_at).toLocaleDateString('zh-CN')} 更新</Text>
                    <Text type="secondary">{new Date(project.created_at).toLocaleDateString('zh-CN')} 创建</Text>
                  </div>
                  <Button type="link" size="small" tabIndex={-1}>继续创作 <ArrowRightOutlined /></Button>
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
        okText="创建"
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
        </Form>
      </Modal>

      <Modal
        title="导入外部小说"
        open={isExternalImportOpen}
        onCancel={() => {
          if (importing) return
          setIsExternalImportOpen(false)
          setExternalImportFile(null)
          setImportStatus('')
        }}
        onOk={() => { void handleExternalImport() }}
        okText="创建作品并导入"
        cancelText="取消"
        okButtonProps={{ loading: importing, disabled: !externalImportFile }}
        cancelButtonProps={{ disabled: importing }}
        closable={!importing}
        maskClosable={!importing}
        destroyOnHidden
      >
        <Alert
          type="info"
          showIcon
          message="只导入文稿"
          description="TXT、Markdown 或 DOCX 会创建一个新作品和正式章节，不会猜测角色、世界观、自动任务或其他司命数据。"
          style={{ marginBottom: 16 }}
        />
        <Upload
          accept=".txt,.md,.docx,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          maxCount={1}
          fileList={externalUploadList}
          beforeUpload={(file) => attachExternalImportFile(file as File)}
          onRemove={() => {
            setExternalImportFile(null)
            return true
          }}
        >
          <Button icon={<UploadOutlined />}>选择 TXT / Markdown / DOCX</Button>
        </Upload>
        {externalImportFile && (
          <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
            将创建作品《{titleFromFile(externalImportFile) || '导入作品'}》
          </Text>
        )}
        {importStatus && <Alert type="info" showIcon message={importStatus} style={{ marginTop: 16 }} />}
      </Modal>

      <Modal
        title="导入司命项目包"
        open={isPackageImportOpen}
        onCancel={() => {
          if (importing) return
          setIsPackageImportOpen(false)
          setPackageImportFile(null)
          setPackageTitle('')
          setImportStatus('')
        }}
        onOk={() => { void handlePackageImport() }}
        okText="创建新作品"
        cancelText="取消"
        okButtonProps={{ loading: importing, disabled: !packageImportFile }}
        cancelButtonProps={{ disabled: importing }}
        closable={!importing}
        maskClosable={!importing}
        destroyOnHidden
      >
        <Alert
          type="warning"
          showIcon
          message="仅接受司命项目包"
          description="只接受 .siming-project。普通 ZIP、TXT、Markdown 和 DOCX 请使用“导入外部小说”；项目包始终创建新作品，不覆盖已有作品。"
          style={{ marginBottom: 16 }}
        />
        <Upload
          accept=".siming-project,application/vnd.siming.project+zip"
          maxCount={1}
          fileList={packageUploadList}
          beforeUpload={(file) => attachPackageImportFile(file as File)}
          onRemove={() => {
            setPackageImportFile(null)
            setPackageRequestKey(createIdempotencyKey())
            return true
          }}
        >
          <Button icon={<FileZipOutlined />}>选择 .siming-project</Button>
        </Upload>
        <div style={{ marginTop: 16 }}>
          <Text strong style={{ display: 'block', marginBottom: 8 }}>新作品标题（可选）</Text>
          <Input
            value={packageTitle}
            maxLength={200}
            showCount
            placeholder="留空时使用项目包中的原作品标题"
            onChange={(event) => {
              setPackageTitle(event.target.value)
              setPackageRequestKey(createIdempotencyKey())
            }}
          />
        </div>
        {importStatus && <Alert type="info" showIcon message={importStatus} style={{ marginTop: 16 }} />}
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
