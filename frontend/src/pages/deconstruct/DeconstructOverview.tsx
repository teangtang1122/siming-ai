import { Button, Card, Col, Row, Space, Statistic } from 'antd'

import { phaseLabel } from './constants'
import type { PreviewData, ReportSummary } from './types'

interface DeconstructOverviewProps {
  reports: ReportSummary[]
  preview: PreviewData | null
  sourceMode: 'chapters' | 'manual'
  activeWordCount: number
  estimatedChunks: number
  onLoadReport: (reportId: string) => void
}

export default function DeconstructOverview({
  reports,
  preview,
  sourceMode,
  activeWordCount,
  estimatedChunks,
  onLoadReport,
}: DeconstructOverviewProps) {
  return (
    <>
      {reports.length > 0 && (
        <Card title="已持久化拆书报告" size="small" style={{ marginBottom: 16 }}>
          <Space wrap>
            {reports.slice(0, 6).map((report) => (
              <Button key={report.id} size="small" onClick={() => onLoadReport(report.id)}>
                {report.status === 'completed' ? '已完成' : phaseLabel(report.phase)} · {(report.total_words || 0).toLocaleString()}字 · {new Date(report.created_at || '').toLocaleString('zh-CN')}
              </Button>
            ))}
          </Space>
        </Card>
      )}

      {preview && (
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col xs={12} md={6}><Card size="small"><Statistic title="已有章节" value={preview.total_chapters} suffix="章" /></Card></Col>
          <Col xs={12} md={6}><Card size="small"><Statistic title="总字数" value={preview.total_words.toLocaleString()} suffix="字" /></Card></Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title={sourceMode === 'chapters' ? '选中字数' : '文本长度'} value={activeWordCount.toLocaleString()} suffix="字" />
            </Card>
          </Col>
          <Col xs={12} md={6}><Card size="small"><Statistic title="预计分析块数" value={estimatedChunks} suffix="块" /></Card></Col>
        </Row>
      )}
    </>
  )
}
