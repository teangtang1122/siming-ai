import { useMemo, useState } from 'react'
import { Alert, Button, List, Space, Tag, Typography, message } from 'antd'
import { apiClient } from '../api/client'

const { Text } = Typography

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface OrphanChapterFile {
  path: string
  id?: string | null
  title?: string | null
  word_count?: number | null
  modified_at?: string | null
}

export interface StorageHealth {
  storage_target?: string | null
  orphan_chapter_files?: OrphanChapterFile[]
  orphan_chapter_file_count?: number
  next_action?: string | null
  warning?: string | null
}

interface StorageRepairResponse {
  tool_status?: string
  tool_detail?: string
  storage_health?: StorageHealth
}

interface StorageRepairActionsProps {
  projectId: string
  health: StorageHealth | null | undefined
  onRepaired?: (health?: StorageHealth) => void
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function normalizeStorageHealth(value: unknown): StorageHealth | null {
  if (!isRecord(value)) return null
  const files = Array.isArray(value.orphan_chapter_files)
    ? value.orphan_chapter_files.filter(isRecord).map((item) => ({
      path: String(item.path || ''),
      id: typeof item.id === 'string' ? item.id : null,
      title: typeof item.title === 'string' ? item.title : null,
      word_count: typeof item.word_count === 'number' ? item.word_count : null,
      modified_at: typeof item.modified_at === 'string' ? item.modified_at : null,
    })).filter((item) => item.path)
    : []
  const count = Number(value.orphan_chapter_file_count ?? files.length)
  const hasStorageSignal = (
    'orphan_chapter_files' in value
    || 'orphan_chapter_file_count' in value
    || 'storage_target' in value
    || 'next_action' in value
  )
  if (!hasStorageSignal) return null
  return {
    storage_target: typeof value.storage_target === 'string' ? value.storage_target : null,
    orphan_chapter_files: files,
    orphan_chapter_file_count: Number.isFinite(count) ? count : files.length,
    next_action: typeof value.next_action === 'string' ? value.next_action : null,
    warning: typeof value.warning === 'string' ? value.warning : null,
  }
}

export function findStorageHealth(value: unknown): StorageHealth | null {
  const seen = new Set<unknown>()
  const queue: Array<{ value: unknown; depth: number }> = [{ value, depth: 0 }]
  while (queue.length > 0 && seen.size < 80) {
    const current = queue.shift()
    if (!current) break
    if (!current.value || seen.has(current.value) || current.depth > 5) continue
    seen.add(current.value)
    const normalized = normalizeStorageHealth(current.value)
    if (normalized) return normalized
    if (Array.isArray(current.value)) {
      current.value.forEach((item) => queue.push({ value: item, depth: current.depth + 1 }))
    } else if (isRecord(current.value)) {
      Object.values(current.value).forEach((item) => {
        if (item && typeof item === 'object') {
          queue.push({ value: item, depth: current.depth + 1 })
        }
      })
    }
  }
  return null
}

export function StorageRepairActions({ projectId, health, onRepaired }: StorageRepairActionsProps) {
  const [dismissed, setDismissed] = useState(false)
  const [loadingAction, setLoadingAction] = useState<'import_orphans' | 'refresh_mirror' | null>(null)
  const files = useMemo(() => health?.orphan_chapter_files || [], [health])
  const count = health?.orphan_chapter_file_count ?? files.length

  if (!health || dismissed || count <= 0) return null

  const runRepair = async (action: 'import_orphans' | 'refresh_mirror') => {
    setLoadingAction(action)
    try {
      const resp = await apiClient.post<ApiResponse<StorageRepairResponse>>(
        `/projects/${projectId}/storage/repair`,
        { action },
      )
      const data = resp.data.data
      if (data?.tool_status && data.tool_status !== 'ok') {
        message.warning(data.tool_detail || '修复操作未执行')
      } else {
        message.success(action === 'import_orphans' ? '已导入未入库章节' : '已用数据库刷新文件镜像')
      }
      onRepaired?.(data?.storage_health)
      if ((data?.storage_health?.orphan_chapter_file_count ?? 0) <= 0) {
        setDismissed(true)
      }
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '修复失败')
    } finally {
      setLoadingAction(null)
    }
  }

  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginTop: 8 }}
      message="检测到章节文件未入库"
      description={(
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Text>
            前端只显示数据库章节；这些文件可能是 CLI 直接写入镜像目录后留下的，需要你明确选择怎么处理。
          </Text>
          <List
            size="small"
            dataSource={files.slice(0, 4)}
            renderItem={(file) => (
              <List.Item style={{ padding: '2px 0' }}>
                <Space size={6} wrap>
                  <Tag color="orange">{file.path}</Tag>
                  {file.title && <Text>{file.title}</Text>}
                  {typeof file.word_count === 'number' && <Text type="secondary">{file.word_count} 字</Text>}
                </Space>
              </List.Item>
            )}
            locale={{ emptyText: `${count} 个章节文件未入库` }}
          />
          <Space wrap>
            <Button
              size="small"
              type="primary"
              loading={loadingAction === 'import_orphans'}
              onClick={() => runRepair('import_orphans')}
            >
              导入为章节
            </Button>
            <Button
              size="small"
              loading={loadingAction === 'refresh_mirror'}
              onClick={() => runRepair('refresh_mirror')}
            >
              用数据库覆盖镜像
            </Button>
            <Button size="small" onClick={() => setDismissed(true)}>
              忽略本次提示
            </Button>
          </Space>
        </Space>
      )}
    />
  )
}
