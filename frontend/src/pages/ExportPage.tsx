import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Input,
  Radio,
  Space,
  Table,
  Typography,
  message,
  Statistic,
  Row,
  Col,
} from 'antd'
import {
  DownloadOutlined,
  FileTextOutlined,
  FileWordOutlined,
  FilePdfOutlined,
  ExportOutlined,
  FolderOpenOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'

const { Title, Text } = Typography

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface ChapterWordCount {
  id: string
  title: string
  word_count: number
  version: number
}

interface WordCountReport {
  chapters: ChapterWordCount[]
  total_chapters: number
  total_words: number
}

interface ExportResult {
  file_id: string
  filename: string
  format: string
  size: number
  download_url: string
  saved_path?: string | null
}

interface ExportPageProps {
  projectId: string
}

function ExportPage({ projectId }: ExportPageProps) {
  const [scope, setScope] = useState('all')
  const [format, setFormat] = useState('txt')
  const [exporting, setExporting] = useState(false)
  const [report, setReport] = useState<WordCountReport | null>(null)
  const [selectedChapterIds, setSelectedChapterIds] = useState<string[]>([])
  const [outputDirectory, setOutputDirectory] = useState('')

  const selectOutputDirectory = async () => {
    const desktopWindow = window as typeof window & {
      pywebview?: { api?: { select_export_directory?: () => Promise<string> } }
    }
    const desktopApi = desktopWindow.pywebview?.api
    if (!desktopApi?.select_export_directory) {
      message.info('当前为浏览器模式，请直接输入本机导出目录')
      return
    }
    try {
      const selected = await desktopApi.select_export_directory()
      if (selected) setOutputDirectory(selected)
    } catch (err: any) {
      message.error(err.message || '选择导出目录失败')
    }
  }

  const fetchReport = useCallback(async () => {
    try {
      const res = await apiClient.get<ApiResponse<WordCountReport>>(`/projects/${projectId}/export/word-count`)
      setReport(res.data.data)
    } catch (err: any) {
      message.error(err.message || '获取字数统计失败')
    }
  }, [projectId])

  useEffect(() => {
    fetchReport()
  }, [fetchReport])

  const handleExport = async () => {
    if (scope === 'selected' && selectedChapterIds.length === 0) {
      message.warning('请先选择要导出的章节')
      return
    }

    setExporting(true)
    try {
      const res = await apiClient.post<ApiResponse<ExportResult>>(`/projects/${projectId}/export`, {
        scope,
        format,
        chapter_ids: scope === 'selected' ? selectedChapterIds : [],
        output_directory: outputDirectory.trim() || null,
      })
      const exportData = res.data.data
      if (exportData.saved_path) {
        message.success(`导出完成：${exportData.saved_path}`)
        return
      }
      const downloadRes = await fetch(exportData.download_url)
      if (!downloadRes.ok) throw new Error('下载导出文件失败')
      const blob = await downloadRes.blob()
      const url = URL.createObjectURL(blob)
      const a = window.document.createElement('a')
      a.href = url
      a.download = exportData.filename || `export_${projectId.slice(0, 8)}_${new Date().toISOString().slice(0, 10)}.${format}`
      window.document.body.appendChild(a)
      a.click()
      window.document.body.removeChild(a)
      URL.revokeObjectURL(url)
      message.success(`导出完成：${exportData.filename}`)
    } catch (err: any) {
      message.error(err.message || '导出失败')
    } finally {
      setExporting(false)
    }
  }

  const columns = [
    { title: '章节', dataIndex: 'title', key: 'title' },
    {
      title: '字数',
      dataIndex: 'word_count',
      key: 'word_count',
      render: (v: number) => v.toLocaleString(),
    },
    { title: '版本', dataIndex: 'version', key: 'version', render: (v: number) => `v${v}` },
  ]

  return (
    <div style={{ padding: 16, maxWidth: 800 }}>
      <Title level={4} style={{ marginTop: 0 }}>
        <ExportOutlined /> 导出
      </Title>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={8}>
          <Card>
            <Statistic
              title="总章节"
              value={report?.total_chapters || 0}
              prefix={<FileTextOutlined />}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="总字数"
              value={(report?.total_words || 0).toLocaleString()}
              suffix="字"
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="总版本"
              value={report?.chapters.reduce((sum, c) => sum + c.version, 0) || 0}
            />
          </Card>
        </Col>
      </Row>

      <Card title="导出选项" style={{ marginBottom: 16 }}>
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <div>
            <Text strong style={{ display: 'block', marginBottom: 8 }}>导出范围</Text>
            <Radio.Group value={scope} onChange={(e) => setScope(e.target.value)}>
              <Radio.Button value="all">全部内容</Radio.Button>
              <Radio.Button value="chapters">仅章节正文</Radio.Button>
              <Radio.Button value="selected">指定章节</Radio.Button>
              <Radio.Button value="outline">仅大纲</Radio.Button>
              <Radio.Button value="characters">仅角色设定</Radio.Button>
              <Radio.Button value="worldbuilding">仅世界观</Radio.Button>
            </Radio.Group>
          </div>

          <div>
            <Text strong style={{ display: 'block', marginBottom: 8 }}>导出格式</Text>
            <Radio.Group value={format} onChange={(e) => setFormat(e.target.value)}>
              <Radio.Button value="txt">TXT 纯文本</Radio.Button>
              <Radio.Button value="docx">
                <FileWordOutlined /> Word (.docx)
              </Radio.Button>
              <Radio.Button value="pdf">
                <FilePdfOutlined /> PDF
              </Radio.Button>
            </Radio.Group>
          </div>

          <div>
            <Text strong style={{ display: 'block', marginBottom: 8 }}>导出目录</Text>
            <Space.Compact style={{ width: '100%' }}>
              <Input
                value={outputDirectory}
                onChange={(event) => setOutputDirectory(event.target.value)}
                placeholder="未选择时使用浏览器下载；也可直接输入完整目录"
              />
              <Button icon={<FolderOpenOutlined />} onClick={selectOutputDirectory}>
                选择目录
              </Button>
            </Space.Compact>
          </div>

          <Button
            type="primary"
            icon={<DownloadOutlined />}
            size="large"
            loading={exporting}
            onClick={handleExport}
          >
            开始导出
          </Button>
        </Space>
      </Card>

      <Card title="章节字数明细">
        <Table
          dataSource={report?.chapters || []}
          columns={columns}
          rowKey="id"
          size="small"
          rowSelection={{
            selectedRowKeys: selectedChapterIds,
            onChange: (keys) => setSelectedChapterIds(keys.map(String)),
          }}
          pagination={false}
          summary={() =>
            report ? (
              <Table.Summary.Row>
                <Table.Summary.Cell index={0}><Text strong>合计</Text></Table.Summary.Cell>
                <Table.Summary.Cell index={1}>
                  <Text strong>{report.total_words.toLocaleString()} 字</Text>
                </Table.Summary.Cell>
                <Table.Summary.Cell index={2}>{report.total_chapters} 章</Table.Summary.Cell>
              </Table.Summary.Row>
            ) : null
          }
        />
      </Card>
    </div>
  )
}

export default ExportPage
