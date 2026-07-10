import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  List,
  Row,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  BookOutlined,
  CopyOutlined,
  ExportOutlined,
  GithubOutlined,
  ReloadOutlined,
  RollbackOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'

const { Title, Text, Paragraph } = Typography
const { TextArea } = Input

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface PromptPack {
  id: string
  project_id: string | null
  pack_id: string
  version: string
  scope: string
  title: string
  summary: string | null
  is_builtin: boolean
  enabled: boolean
  updated_at: string | null
}

interface PromptPackDetail {
  pack_id: string
  version: string
  scope: string
  title: string
  summary: string | null
  system_prompt: string
  workflow: Array<{ step: number; name: string; description: string }> | null
  quality_rubric: {
    dimensions?: Array<{ name: string; description: string; max_score: number }>
    passing_score?: number
    max_score?: number
  } | null
  forbidden_patterns: string[] | null
}

interface ContributionResult {
  markdown: string
  markdown_path: string
  json_path: string
  github_issue_url: string
  github_issue_title: string
  github_issue_body: string
  package: {
    diff_stats: {
      added_lines: number
      removed_lines: number
      changed_lines: number
    }
  }
}

interface PromptPacksPageProps {
  projectId: string
}

interface ContributionFormValues {
  change_summary: string
  expected_effect: string
  test_notes?: string
  contributor_name?: string
  contact?: string
}

const SCOPE_LABELS: Record<string, string> = {
  new_project: '新书创建',
  chapter_writing: '章节写作',
  chapter_review: '章节评审',
  character_design: '角色设计',
  worldbuilding: '世界观',
  outline_planning: '大纲规划',
  anti_ai_review: 'AI 味审查',
  cataloging: '作品建档',
  character_change_detection: '角色变化',
  worldbuilding_detection: '世界观变化',
  chapter_evaluation: '章节评估',
  conflict_suggestion: '冲突建议',
}

function scopeLabel(scope: string) {
  return SCOPE_LABELS[scope] || scope
}

function PromptPacksPage({ projectId }: PromptPacksPageProps) {
  const [packs, setPacks] = useState<PromptPack[]>([])
  const [selectedPackId, setSelectedPackId] = useState<string>('')
  const [selectedPack, setSelectedPack] = useState<PromptPackDetail | null>(null)
  const [editedPrompt, setEditedPrompt] = useState('')
  const [loading, setLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [contribution, setContribution] = useState<ContributionResult | null>(null)
  const [form] = Form.useForm<ContributionFormValues>()

  const selectedListItem = useMemo(
    () => packs.find((pack) => pack.pack_id === selectedPackId) || null,
    [packs, selectedPackId],
  )

  const fetchPacks = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiClient.get<ApiResponse<{ items: PromptPack[]; total: number }>>(
        `/projects/${projectId}/prompt-packs`,
      )
      const items = res.data.data.items
      setPacks(items)
      if (!selectedPackId && items.length > 0) {
        setSelectedPackId(items[0].pack_id)
      }
    } catch (err: any) {
      message.error(err.message || '获取提示词包失败')
    } finally {
      setLoading(false)
    }
  }, [projectId, selectedPackId])

  const fetchPackDetail = useCallback(async (packId: string) => {
    if (!packId) return
    setDetailLoading(true)
    setContribution(null)
    try {
      const res = await apiClient.get<ApiResponse<PromptPackDetail>>(
        `/projects/${projectId}/prompt-packs/${packId}`,
      )
      setSelectedPack(res.data.data)
      setEditedPrompt(res.data.data.system_prompt || '')
    } catch (err: any) {
      message.error(err.message || '获取提示词详情失败')
    } finally {
      setDetailLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    fetchPacks()
  }, [fetchPacks])

  useEffect(() => {
    if (selectedPackId) {
      fetchPackDetail(selectedPackId)
    }
  }, [fetchPackDetail, selectedPackId])

  const resetPrompt = () => {
    if (!selectedPack) return
    setEditedPrompt(selectedPack.system_prompt || '')
    setContribution(null)
  }

  const copyText = async (text: string, success: string) => {
    try {
      await navigator.clipboard.writeText(text)
      message.success(success)
    } catch {
      message.warning('复制失败，请手动选择文本复制')
    }
  }

  const exportContribution = async () => {
    if (!selectedPack) {
      message.warning('请先选择一个提示词包')
      return
    }
    if (!editedPrompt.trim()) {
      message.warning('修改后的提示词不能为空')
      return
    }
    try {
      const values = await form.validateFields()
      setExporting(true)
      const res = await apiClient.post<ApiResponse<ContributionResult>>(
        `/projects/${projectId}/prompt-contributions/export`,
        {
          pack_id: selectedPack.pack_id,
          base_version: selectedPack.version,
          edited_system_prompt: editedPrompt,
          change_summary: values.change_summary,
          expected_effect: values.expected_effect,
          test_notes: values.test_notes || null,
          contributor_name: values.contributor_name || null,
          contact: values.contact || null,
        },
      )
      setContribution(res.data.data)
      message.success('已生成提示词投稿包')
    } catch (err: any) {
      if (err.message) message.error(err.message)
    } finally {
      setExporting(false)
    }
  }

  const openGithubIssue = () => {
    if (!contribution) return
    window.open(contribution.github_issue_url, '_blank', 'noopener,noreferrer')
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>
          <BookOutlined style={{ marginRight: 8 }} />
          提示词投稿
        </Title>
        <Button icon={<ReloadOutlined />} onClick={fetchPacks} loading={loading}>
          刷新
        </Button>
      </div>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="提示词投稿流程"
        description={(
          <Space direction="vertical" size={2}>
            <Text>左侧选择提示词包，在右侧直接修改内容。</Text>
            <Text>填写「做了哪些修改」「预期效果」和测试记录后，点击「生成投稿包」。</Text>
            <Text>生成后可以复制投稿 Markdown，也可以点击「打开 GitHub 提交页面」提交给项目维护者。</Text>
          </Space>
        )}
      />

      <Row gutter={16} align="stretch">
        <Col xs={24} lg={8} xl={7}>
          <Card title="选择提示词包" loading={loading} bodyStyle={{ padding: 0 }}>
            <List
              dataSource={packs}
              locale={{ emptyText: '暂无提示词包' }}
              renderItem={(pack) => (
                <List.Item
                  style={{
                    cursor: 'pointer',
                    padding: '12px 16px',
                    background: pack.pack_id === selectedPackId ? 'var(--ant-color-fill-tertiary)' : undefined,
                  }}
                  onClick={() => setSelectedPackId(pack.pack_id)}
                >
                  <List.Item.Meta
                    title={
                      <Space wrap>
                        <Text strong>{pack.title}</Text>
                        <Tag>{scopeLabel(pack.scope)}</Tag>
                        {pack.is_builtin && <Tag color="blue">内置</Tag>}
                      </Space>
                    }
                    description={
                      <Space direction="vertical" size={2}>
                        <Text type="secondary">pack_id: {pack.pack_id}</Text>
                        {pack.summary && <Text type="secondary">{pack.summary}</Text>}
                      </Space>
                    }
                  />
                </List.Item>
              )}
            />
          </Card>
        </Col>

        <Col xs={24} lg={16} xl={17}>
          <Card
            title={selectedPack ? selectedPack.title : '提示词详情'}
            loading={detailLoading}
            extra={selectedPack && (
              <Space>
                <Tag>{scopeLabel(selectedPack.scope)}</Tag>
                <Tag>v{selectedPack.version}</Tag>
                {selectedListItem?.is_builtin && <Tag color="blue">内置</Tag>}
              </Space>
            )}
          >
            {!selectedPack ? (
              <Text type="secondary">请选择一个提示词包。</Text>
            ) : (
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                {selectedPack.summary && (
                  <Paragraph style={{ marginBottom: 0 }}>{selectedPack.summary}</Paragraph>
                )}

                <div>
                  <Space style={{ marginBottom: 8 }}>
                    <Text strong>修改后的提示词</Text>
                    <Text type="secondary">{editedPrompt.length.toLocaleString()} 字符</Text>
                  </Space>
                  <TextArea
                    value={editedPrompt}
                    onChange={(event) => {
                      setEditedPrompt(event.target.value)
                      setContribution(null)
                    }}
                    autoSize={{ minRows: 16, maxRows: 28 }}
                    style={{ fontFamily: 'Consolas, "Microsoft YaHei", monospace' }}
                  />
                </div>

                <Form form={form} layout="vertical">
                  <Row gutter={12}>
                    <Col xs={24} md={12}>
                      <Form.Item
                        name="change_summary"
                        label="做了哪些修改"
                        rules={[{ required: true, message: '请说明改了什么' }]}
                      >
                        <TextArea rows={4} placeholder="例如：减少空泛自检，增加角色目标变化检查，强化章节收束钩子。" />
                      </Form.Item>
                    </Col>
                    <Col xs={24} md={12}>
                      <Form.Item
                        name="expected_effect"
                        label="预期有什么更好的效果"
                        rules={[{ required: true, message: '请说明预期效果' }]}
                      >
                        <TextArea rows={4} placeholder="例如：生成结果更少跑偏，角色状态更新更稳定，章节结尾更有追读感。" />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Form.Item name="test_notes" label="本地测试或对比记录">
                    <TextArea rows={3} placeholder="可以写：用哪本书/哪一章测试，改前问题是什么，改后改善在哪里。" />
                  </Form.Item>
                  <Row gutter={12}>
                    <Col xs={24} md={12}>
                      <Form.Item name="contributor_name" label="投稿署名">
                        <Input placeholder="可选，用于致谢" />
                      </Form.Item>
                    </Col>
                    <Col xs={24} md={12}>
                      <Form.Item name="contact" label="联系方式">
                        <Input placeholder="可选，GitHub 用户名、邮箱或社群昵称" />
                      </Form.Item>
                    </Col>
                  </Row>
                </Form>

                <Space wrap>
                  <Button icon={<RollbackOutlined />} onClick={resetPrompt}>
                    恢复原提示词
                  </Button>
                  <Button icon={<CopyOutlined />} onClick={() => copyText(editedPrompt, '提示词已复制')}>
                    复制提示词
                  </Button>
                  <Button type="primary" icon={<ExportOutlined />} loading={exporting} onClick={exportContribution}>
                    生成投稿包
                  </Button>
                </Space>

                {contribution && (
                  <Card size="small" title="投稿包已生成">
                    <Space direction="vertical" size={8} style={{ width: '100%' }}>
                      <Text>Markdown：<Text code copyable>{contribution.markdown_path}</Text></Text>
                      <Text>JSON：<Text code copyable>{contribution.json_path}</Text></Text>
                      <Text type="secondary">
                        变更统计：新增 {contribution.package.diff_stats.added_lines} 行，
                        删除 {contribution.package.diff_stats.removed_lines} 行。
                      </Text>
                      <Space wrap>
                        <Button icon={<CopyOutlined />} onClick={() => copyText(contribution.markdown, '投稿 Markdown 已复制')}>
                          复制完整投稿 Markdown
                        </Button>
                        <Button icon={<CopyOutlined />} onClick={() => copyText(contribution.github_issue_body, 'Issue 内容已复制')}>
                          复制 Issue 内容
                        </Button>
                        <Button type="primary" icon={<GithubOutlined />} onClick={openGithubIssue}>
                          打开 GitHub 提交页面
                        </Button>
                      </Space>
                    </Space>
                  </Card>
                )}
              </Space>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}

export default PromptPacksPage
