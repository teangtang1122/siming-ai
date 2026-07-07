import { Alert, Button, Card, Collapse, Divider, Input, List, Select, Space, Tag, Typography } from 'antd'
import { CheckOutlined, CloseOutlined, SaveOutlined } from '@ant-design/icons'
import CatalogingCandidatePayloadEditor from './CatalogingCandidatePayloadEditor'
import CatalogingMergePreview from './CatalogingMergePreview'
import { catalogingCandidateStatusOptions, catalogingCandidateTypeOptions } from './catalogingOptions'
import type { CatalogingCandidate, CatalogingFact, CatalogingJob } from './catalogingTypes'
import { catalogingStatusColor, safeStringify } from './catalogingTypes'

const { Text } = Typography
const { TextArea } = Input

interface CatalogingCandidatesPanelProps {
  projectId: string
  job: CatalogingJob | null
  visibleCandidates: CatalogingCandidate[]
  facts: CatalogingFact[]
  candidateDrafts: Record<string, string>
  candidateStatusFilter: string
  newCandidateType: string
  newCandidatePayload: string
  logs: string[]
  onCandidateStatusFilterChange: (status: string) => void
  onNewCandidateTypeChange: (itemType: string) => void
  onNewCandidatePayloadChange: (payload: string) => void
  onBulkUpdateCandidates: (status: 'approved' | 'rejected') => void
  onSaveCandidate: (candidate: CatalogingCandidate, status?: string) => void
  onCandidateDraftChange: (candidateId: string, value: string) => void
  onCreateManualCandidate: () => void
}

function CatalogingCandidatesPanel({
  projectId,
  job,
  visibleCandidates,
  facts,
  candidateDrafts,
  candidateStatusFilter,
  newCandidateType,
  newCandidatePayload,
  logs,
  onCandidateStatusFilterChange,
  onNewCandidateTypeChange,
  onNewCandidatePayloadChange,
  onBulkUpdateCandidates,
  onSaveCandidate,
  onCandidateDraftChange,
  onCreateManualCandidate,
}: CatalogingCandidatesPanelProps) {
  return (
    <>
      <Card title="候选写入项" size="small">
        <Collapse
          size="small"
          style={{ marginBottom: 12 }}
          items={[{
            key: 'facts',
            label: `事实线索（旧任务兼容，${facts.length}）`,
            children: <CatalogingFactsList facts={facts} />,
          }]}
        />

        <Space style={{ marginBottom: 12 }}>
          <Select
            size="small"
            value={candidateStatusFilter}
            style={{ width: 150 }}
            onChange={onCandidateStatusFilterChange}
            options={catalogingCandidateStatusOptions}
          />
          <Button size="small" onClick={() => onBulkUpdateCandidates('approved')} disabled={!job || visibleCandidates.length === 0}>
            批量确认
          </Button>
          <Button size="small" danger onClick={() => onBulkUpdateCandidates('rejected')} disabled={!job || visibleCandidates.length === 0}>
            批量拒绝
          </Button>
        </Space>

        {job?.status === 'paused_on_failure' && (
          <Alert type="error" showIcon message="当前章节失败，系统已停止继续处理后续章节。" style={{ marginBottom: 12 }} />
        )}

        {job && ['paused_on_failure', 'waiting_confirmation'].includes(job.status) && (
          <Card size="small" title="手动补充候选项" style={{ marginBottom: 12 }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Space wrap>
                <Select
                  size="small"
                  value={newCandidateType}
                  style={{ width: 210 }}
                  onChange={onNewCandidateTypeChange}
                  options={catalogingCandidateTypeOptions}
                />
                <Button size="small" type="primary" onClick={onCreateManualCandidate}>新增候选项</Button>
              </Space>
              <TextArea
                value={newCandidatePayload}
                autoSize={{ minRows: 3, maxRows: 8 }}
                onChange={(event) => onNewCandidatePayloadChange(event.target.value)}
              />
            </Space>
          </Card>
        )}

        <List
          dataSource={visibleCandidates}
          locale={{ emptyText: '任务运行后，模型抽取到的信息会实时出现在这里' }}
          renderItem={(item) => (
            <List.Item>
              <Card size="small" style={{ width: '100%' }}>
                <Space style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                  <Space wrap>
                    <Tag>{item.item_type}</Tag>
                    <Tag color={catalogingStatusColor[item.status] || 'default'}>{item.status}</Tag>
                    {item.target_name && <Text strong>{item.target_name}</Text>}
                    {typeof item.confidence === 'number' && <Text type="secondary">置信度 {Math.round(item.confidence * 100)}%</Text>}
                  </Space>
                  <Space>
                    <Button size="small" icon={<SaveOutlined />} onClick={() => onSaveCandidate(item)}>保存修改</Button>
                    <Button size="small" icon={<CheckOutlined />} onClick={() => onSaveCandidate(item, 'approved')}>确认</Button>
                    <Button size="small" danger icon={<CloseOutlined />} onClick={() => onSaveCandidate(item, 'rejected')}>拒绝</Button>
                  </Space>
                </Space>
                {item.evidence && <Text type="secondary">依据：{item.evidence}</Text>}
                {item.error && <div><Text type="danger">{item.error}</Text></div>}
                <Divider style={{ margin: '8px 0' }} />
                {item.item_type === 'character_merge_candidate' && (
                  <CatalogingMergePreview
                    projectId={projectId}
                    candidate={item}
                    draft={candidateDrafts[item.id] || safeStringify(item.payload)}
                    disabled={['applying', 'applied'].includes(item.status)}
                    onDraftChange={onCandidateDraftChange}
                  />
                )}
                {item.item_type !== 'character_merge_candidate' && (
                  <CatalogingCandidatePayloadEditor
                    candidate={item}
                    draft={candidateDrafts[item.id] || safeStringify(item.payload)}
                    disabled={['applying', 'applied'].includes(item.status)}
                    onDraftChange={onCandidateDraftChange}
                  />
                )}
              </Card>
            </List.Item>
          )}
        />
      </Card>

      <Card title="运行日志" size="small" style={{ marginTop: 16 }}>
        <div style={{ maxHeight: 220, overflow: 'auto' }}>
          {logs.length === 0 ? <Text type="secondary">暂无日志</Text> : logs.map((log, index) => <div key={`${log}-${index}`}>{log}</div>)}
        </div>
      </Card>
    </>
  )
}

function CatalogingFactsList({ facts }: { facts: CatalogingFact[] }) {
  if (!facts.length) {
    return <Text type="secondary">当前章节没有单独保存的事实线索；融合建档会直接生成候选项。</Text>
  }
  return (
    <List
      size="small"
      dataSource={facts}
      renderItem={(item) => (
        <List.Item>
          <Space direction="vertical" style={{ width: '100%' }} size={6}>
            <Space wrap>
              <Tag color="purple">{item.fact_type}</Tag>
              {typeof item.confidence === 'number' && <Text type="secondary">置信度 {Math.round(item.confidence * 100)}%</Text>}
              {item.status && <Tag>{item.status}</Tag>}
            </Space>
            {item.evidence && <Text type="secondary">依据：{item.evidence}</Text>}
            {item.error && <Text type="danger">{item.error}</Text>}
            <TextArea value={safeStringify(item.payload)} autoSize={{ minRows: 2, maxRows: 8 }} readOnly />
          </Space>
        </List.Item>
      )}
    />
  )
}

export default CatalogingCandidatesPanel
