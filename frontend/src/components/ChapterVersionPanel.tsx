import { useMemo, useState } from 'react'
import { Alert, Button, List, Popconfirm, Select, Space, Tag, Typography, message } from 'antd'
import { DiffOutlined, RollbackOutlined } from '@ant-design/icons'
import { apiClient } from '../api/client'

const { Paragraph, Text } = Typography

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface SnapshotItem {
  id: string
  chapter_id: string
  version_number: number
  word_count: number
  trigger_type: string
  created_at: string
}

interface ChapterVersionData {
  chapter?: {
    id?: string
    chapter_id?: string
    title?: string
    current_version?: number
  }
  items?: SnapshotItem[]
}

interface DiffResponse {
  from_snapshot: SnapshotItem
  to_snapshot: SnapshotItem
  total_changes: number
  changes: Array<{
    type: string
    from_lines: string[]
    to_lines: string[]
  }>
}

interface ChapterVersionPanelProps {
  projectId: string
  data: unknown
  onRestored?: () => void
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function normalizeVersionData(data: unknown): ChapterVersionData | null {
  if (!isRecord(data)) return null
  const chapter = isRecord(data.chapter) ? data.chapter : null
  const rawItems = Array.isArray(data.items) ? data.items : []
  const items = rawItems.filter(isRecord).map((item) => ({
    id: String(item.id || ''),
    chapter_id: String(item.chapter_id || chapter?.id || chapter?.chapter_id || ''),
    version_number: Number(item.version_number || 0),
    word_count: Number(item.word_count || 0),
    trigger_type: String(item.trigger_type || ''),
    created_at: String(item.created_at || ''),
  })).filter((item) => item.id && item.chapter_id)
  if (!chapter && items.length === 0) return null
  return {
    chapter: chapter
      ? {
          id: typeof chapter.id === 'string' ? chapter.id : undefined,
          chapter_id: typeof chapter.chapter_id === 'string' ? chapter.chapter_id : undefined,
          title: typeof chapter.title === 'string' ? chapter.title : undefined,
          current_version: typeof chapter.current_version === 'number' ? chapter.current_version : undefined,
        }
      : undefined,
    items,
  }
}

export function ChapterVersionPanel({ projectId, data, onRestored }: ChapterVersionPanelProps) {
  const versionData = useMemo(() => normalizeVersionData(data), [data])
  const snapshots = versionData?.items || []
  const chapterId = versionData?.chapter?.id || versionData?.chapter?.chapter_id || snapshots[0]?.chapter_id
  const [fromSnapshotId, setFromSnapshotId] = useState<string | undefined>(snapshots[1]?.id || snapshots[0]?.id)
  const [toSnapshotId, setToSnapshotId] = useState<string | undefined>(snapshots[0]?.id)
  const [diff, setDiff] = useState<DiffResponse | null>(null)
  const [loadingDiff, setLoadingDiff] = useState(false)
  const [restoringId, setRestoringId] = useState<string | null>(null)

  if (!versionData || !chapterId || snapshots.length === 0) return null

  const options = snapshots.map((snapshot) => ({
    value: snapshot.id,
    label: `v${snapshot.version_number} · ${snapshot.trigger_type || 'snapshot'}`,
  }))

  const compare = async () => {
    if (!fromSnapshotId || !toSnapshotId || fromSnapshotId === toSnapshotId) {
      message.warning('请选择两个不同版本')
      return
    }
    setLoadingDiff(true)
    try {
      const resp = await apiClient.get<ApiResponse<DiffResponse>>(
        `/projects/${projectId}/chapters/${chapterId}/snapshots/diff`,
        { from_snapshot_id: fromSnapshotId, to_snapshot_id: toSnapshotId },
      )
      setDiff(resp.data.data)
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '版本对比失败')
    } finally {
      setLoadingDiff(false)
    }
  }

  const restore = async (snapshotId: string) => {
    setRestoringId(snapshotId)
    try {
      await apiClient.post<ApiResponse<unknown>>(
        `/projects/${projectId}/chapters/${chapterId}/restore/${snapshotId}`,
      )
      message.success('已恢复历史版本')
      onRestored?.()
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '恢复版本失败')
    } finally {
      setRestoringId(null)
    }
  }

  return (
    <Alert
      type="info"
      showIcon
      style={{ marginTop: 8 }}
      message={`章节版本：${versionData.chapter?.title || chapterId}`}
      description={(
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space wrap>
            <Select
              size="small"
              value={fromSnapshotId}
              options={options}
              onChange={setFromSnapshotId}
              style={{ minWidth: 150 }}
              placeholder="起始版本"
            />
            <Select
              size="small"
              value={toSnapshotId}
              options={options}
              onChange={setToSnapshotId}
              style={{ minWidth: 150 }}
              placeholder="目标版本"
            />
            <Button size="small" icon={<DiffOutlined />} loading={loadingDiff} onClick={compare}>
              对比
            </Button>
          </Space>
          <List
            size="small"
            dataSource={snapshots.slice(0, 5)}
            renderItem={(snapshot) => (
              <List.Item
                style={{ padding: '4px 0' }}
                actions={[
                  <Popconfirm
                    key="restore"
                    title="恢复此版本"
                    description="当前正文会被替换，并生成一条新的恢复快照。"
                    okText="恢复"
                    cancelText="取消"
                    onConfirm={() => restore(snapshot.id)}
                  >
                    <Button size="small" icon={<RollbackOutlined />} loading={restoringId === snapshot.id}>
                      恢复
                    </Button>
                  </Popconfirm>,
                ]}
              >
                <Space wrap>
                  <Tag color={snapshot.version_number === versionData.chapter?.current_version ? 'green' : 'blue'}>
                    v{snapshot.version_number}
                  </Tag>
                  <Text type="secondary">{snapshot.word_count} 字</Text>
                  <Text type="secondary">{snapshot.trigger_type}</Text>
                  {snapshot.created_at && <Text type="secondary">{new Date(snapshot.created_at).toLocaleString('zh-CN')}</Text>}
                </Space>
              </List.Item>
            )}
          />
          {diff && (
            <div>
              <Space wrap style={{ marginBottom: 6 }}>
                <Text strong>v{diff.from_snapshot.version_number} → v{diff.to_snapshot.version_number}</Text>
                <Tag color={diff.total_changes > 0 ? 'orange' : 'green'}>{diff.total_changes} 处变更</Tag>
              </Space>
              {diff.changes.filter((change) => change.type !== 'equal').slice(0, 4).map((change, index) => (
                <Paragraph
                  key={`${change.type}-${index}`}
                  style={{
                    marginBottom: 6,
                    padding: 8,
                    borderRadius: 6,
                    background: 'var(--ant-color-fill-tertiary)',
                    whiteSpace: 'pre-wrap',
                  }}
                >
                  <Tag>{change.type}</Tag>
                  {change.from_lines.length > 0 && <Text delete>{change.from_lines.join('\n').slice(0, 300)}</Text>}
                  {change.to_lines.length > 0 && <><br /><Text>{change.to_lines.join('\n').slice(0, 300)}</Text></>}
                </Paragraph>
              ))}
              {diff.total_changes === 0 && <Text type="secondary">两个版本没有正文差异。</Text>}
            </div>
          )}
        </Space>
      )}
    />
  )
}
