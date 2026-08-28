import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Button,
  Card,
  Empty,
  Input,
  List,
  Select,
  Space,
  Tag,
  Typography,
  Upload,
  message,
  Row,
  Col,
  Statistic,
  Divider,
} from 'antd'
import {
  UploadOutlined,
  FileAddOutlined,
  CheckOutlined,
} from '@ant-design/icons'
import type { UploadFile } from 'antd'
import { apiClient } from '../api/client'

const { Title, Text } = Typography
const { TextArea } = Input

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
  preview: string
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
  model?: string
}

interface ConfirmResult {
  chapters: Array<{ id: string; title: string; word_count: number }>
  total: number
}

interface OutlineOpt {
  value: string
  label: string
}

interface ImportPageProps {
  projectId: string
}

function ImportPage({ projectId }: ImportPageProps) {
  const [uploading, setUploading] = useState(false)
  const [splitting, setSplitting] = useState(false)
  const [importing, setImporting] = useState(false)
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null)
  const [splits, setSplits] = useState<SplitItem[]>([])
  const [manualText, setManualText] = useState('')
  const [outlineNodeId, setOutlineNodeId] = useState<string | undefined>()
  const [outlineOptions, setOutlineOptions] = useState<OutlineOpt[]>([])

  const fetchOutline = useCallback(async () => {
    try {
      const res = await apiClient.get<ApiResponse<{ items: Array<{ id: string; title: string; children: Array<unknown> }> }>>(`/projects/${projectId}/outline`)
      interface TreeNode { id: string; title: string; children: TreeNode[] }
      const flattened: OutlineOpt[] = []
      function walk(nodes: TreeNode[], depth: number) {
        for (const n of nodes) {
          flattened.push({ value: n.id, label: `${'　'.repeat(depth)}${n.title}` })
          if (n.children?.length) walk(n.children, depth + 1)
        }
      }
      walk(res.data.data.items as TreeNode[], 0)
      setOutlineOptions(flattened)
    } catch {
      // ignore
    }
  }, [projectId])

  useEffect(() => { fetchOutline() }, [fetchOutline])

  const activeText = uploadResult?.text || manualText || ''
  const activeWordCount = activeText.length

  const handleDetectSplits = useCallback(async (text: string) => {
    if (!text || text.length < 100) return
    setSplitting(true)
    try {
      const res = await apiClient.post<ApiResponse<SplitResult>>(`/projects/${projectId}/import/preview`, {
        text,
      })
      setSplits(res.data.data.splits || [])
    } catch {
      // silently ignore detection errors
    } finally {
      setSplitting(false)
    }
  }, [projectId])

  const handleUpload = async (info: { file: UploadFile }) => {
    const file = info.file as unknown as File
    if (!file) return
    setUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await fetch(`/api/v1/projects/${projectId}/import/file`, {
        method: 'POST',
        body: formData,
      })
      if (!res.ok) throw new Error('上传失败')
      const data = await res.json()
      if (data.code === 0 && data.data) {
        setUploadResult(data.data)
        setManualText('')
        setSplits([])
        message.success(`文件解析成功：${data.data.word_count} 字符，正在识别章节边界...`)
        handleDetectSplits(data.data.text)
      }
    } catch (err: any) {
      message.error(err.message || '文件上传解析失败')
    } finally {
      setUploading(false)
    }
  }

  const handleConfirmImport = async () => {
    if (!activeText) { message.warning('没有可导入的内容'); return }
    setImporting(true)
    try {
      const res = await apiClient.post<ApiResponse<ConfirmResult>>(`/projects/${projectId}/import/confirm`, {
        text: activeText,
        splits: splits.length > 0 ? splits : undefined,
        outline_node_id: outlineNodeId || undefined,
      })
      message.success(`成功导入 ${res.data.data.total} 章`)
      setUploadResult(null)
      setManualText('')
      setSplits([])
    } catch (err: any) {
      message.error(err.message || '导入失败')
    } finally {
      setImporting(false)
    }
  }

  const clearAll = () => {
    setUploadResult(null)
    setManualText('')
    setSplits([])
  }

  const splitPreviews = useMemo(() => {
    if (splits.length === 0 && activeText) {
      return [{ title: '单章导入', start_char: 0, end_char: activeText.length, preview: activeText.slice(0, 100) }]
    }
    return splits
  }, [splits, activeText])

  return (
    <div style={{ padding: 16, maxWidth: 1000, margin: '0 auto' }}>
      <Title level={4} style={{ marginTop: 0 }}>
        <FileAddOutlined /> 内容导入
      </Title>

      <Row gutter={16} style={{ marginBottom: 20 }}>
        <Col span={8}>
          <Card size="small">
            <Statistic title="当前文本长度" value={activeWordCount.toLocaleString()} suffix="字符" />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic title="识别章节数" value={splits.length > 0 ? splits.length : (activeText ? 1 : 0)} suffix="章" />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic
              title="状态"
              value={uploadResult ? `已上传 ${uploadResult.format.toUpperCase()}` : activeText ? '手动输入' : '等待导入'}
              valueStyle={{ fontSize: 14 }}
            />
          </Card>
        </Col>
      </Row>

      <Card title="1. 上传文件或粘贴文本" style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Space wrap>
            <Upload
              accept=".txt,.md,.docx"
              showUploadList={false}
              customRequest={({ file, onSuccess }: any) => {
                handleUpload({ file })
                onSuccess?.({})
              }}
            >
              <Button icon={<UploadOutlined />} loading={uploading} size="large">
                上传 TXT / Markdown / DOCX 文件
              </Button>
            </Upload>
            <Text type="secondary">支持 .txt、.md 和 .docx 文件，最大 10MB；Markdown 标题可用于章节识别</Text>
          </Space>

          <Divider plain>或直接粘贴文本</Divider>

          <TextArea
            value={manualText}
            onChange={(e) => { setManualText(e.target.value); setUploadResult(null); setSplits([]) }}
            placeholder="粘贴小说文本到这里..."
            autoSize={{ minRows: 6, maxRows: 14 }}
            showCount
            disabled={!!uploadResult}
          />
        </Space>
      </Card>

      {activeText && (
        <Card title="2. 预览与章节识别" style={{ marginBottom: 16 }}>
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            <div style={{ maxHeight: 200, overflow: 'auto', background: '#fafafa', padding: 12, borderRadius: 4, whiteSpace: 'pre-wrap', fontSize: 13 }}>
              {activeText.slice(0, 1000)}
              {activeText.length > 1000 && <Text type="secondary">...(共 {activeText.length} 字符)</Text>}
            </div>

            <Space wrap>
              <Button icon={<CheckOutlined />} loading={splitting} onClick={() => handleDetectSplits(activeText)}>
                重新识别章节边界
              </Button>
              {splits.length > 0 && (
                <Button onClick={() => setSplits([])}>清除识别结果</Button>
              )}
              {splits.length > 0 && (
                <Tag color="blue">正则匹配</Tag>
              )}
            </Space>

            {splitPreviews.length > 0 && (
              <div>
                <Text strong style={{ display: 'block', marginBottom: 8 }}>
                  章节分割预览（{splitPreviews.length} 章）
                </Text>
                <List
                  size="small"
                  bordered
                  dataSource={splitPreviews}
                  renderItem={(item, i) => (
                    <List.Item>
                      <Space direction="vertical" size={2} style={{ width: '100%' }}>
                        <Space>
                          <Tag color="blue">第 {i + 1} 章</Tag>
                          <Text strong>{item.title}</Text>
                          <Tag>{item.end_char - item.start_char} 字</Tag>
                          {item.source && <Tag color={item.source === 'llm' ? 'purple' : 'default'}>{item.source}</Tag>}
                          {item.needs_review && <Tag color="orange">需复核</Tag>}
                        </Space>
                        <Text type="secondary" ellipsis style={{ maxWidth: '100%' }}>
                          位置 {item.start_char}-{item.end_char}: {item.preview}
                        </Text>
                        {item.needs_review && item.review_reason && (
                          <Text type="warning">校正提示：{item.review_reason}</Text>
                        )}
                      </Space>
                    </List.Item>
                  )}
                />
              </div>
            )}
          </Space>
        </Card>
      )}

      {activeText && (
        <Card title="3. 确认导入" style={{ marginBottom: 16 }}>
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            <div>
              <Text strong style={{ display: 'block', marginBottom: 4 }}>关联到大纲节点（可选）</Text>
              <Select
                value={outlineNodeId}
                onChange={setOutlineNodeId}
                allowClear
                showSearch
                optionFilterProp="label"
                options={outlineOptions}
                placeholder="选择大纲节点，导入的章节将关联到此节点下"
                style={{ width: '100%', maxWidth: 500 }}
              />
            </div>

            <Space>
              <Button type="primary" icon={<CheckOutlined />} loading={importing} onClick={handleConfirmImport} size="large">
                确认导入 ({splitPreviews.length} 章)
              </Button>
              <Button onClick={clearAll}>清除全部</Button>
            </Space>
          </Space>
        </Card>
      )}

      {!activeText && (
        <Card>
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="上传文件或粘贴文本开始导入" />
        </Card>
      )}
    </div>
  )
}

export default ImportPage
