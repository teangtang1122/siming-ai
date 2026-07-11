import { Alert, List, Space, Tag, Typography } from 'antd'

const { Text } = Typography

interface LedgerItem {
  id?: string
  ledger_type?: string
  title?: string
  status?: string
  storyline?: string
  confidence?: number | null
}

interface NarrativeLedgerPanelProps {
  data: unknown
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

export function NarrativeLedgerPanel({ data }: NarrativeLedgerPanelProps) {
  const root = asRecord(data)
  const ledger = asRecord(root?.narrative_ledger)
  const rawItems = Array.isArray(ledger?.items) ? ledger.items : []
  const items = rawItems.map(asRecord).filter(Boolean).map((item) => ({
    id: String(item?.id || ''),
    ledger_type: String(item?.ledger_type || 'entry'),
    title: String(item?.title || ''),
    status: String(item?.status || 'active'),
    storyline: String(item?.storyline || ''),
    confidence: typeof item?.confidence === 'number' ? item.confidence : null,
  })) as LedgerItem[]
  const checkpointId = typeof root?.ledger_checkpoint_id === 'string' ? root.ledger_checkpoint_id : ''
  const counts = asRecord(ledger?.counts)

  if (!ledger && !checkpointId) return null

  return (
    <Alert
      type="success"
      showIcon
      style={{ marginTop: 8 }}
      message="叙事账本已更新"
      description={(
        <Space direction="vertical" size={6} style={{ width: '100%' }}>
          {checkpointId && <Text type="secondary">已建立可回退检查点</Text>}
          {counts && (
            <Space wrap size={4}>
              {['new', 'advanced', 'fulfilled', 'invalidated', 'pending_review'].map((key) => {
                const value = Number(counts[key] || 0)
                return value > 0 ? <Tag key={key}>{key}: {value}</Tag> : null
              })}
            </Space>
          )}
          <List
            size="small"
            dataSource={items.slice(0, 6)}
            locale={{ emptyText: '本章没有新增账本项' }}
            renderItem={(item) => (
              <List.Item style={{ padding: '3px 0' }}>
                <Space wrap size={4}>
                  <Tag color="blue">{item.ledger_type}</Tag>
                  <Text>{item.title || '未命名项'}</Text>
                  <Tag color={item.status === 'fulfilled' ? 'green' : item.status === 'pending_review' ? 'orange' : 'default'}>{item.status}</Tag>
                  {item.storyline && <Text type="secondary">{item.storyline}</Text>}
                </Space>
              </List.Item>
            )}
          />
        </Space>
      )}
    />
  )
}
