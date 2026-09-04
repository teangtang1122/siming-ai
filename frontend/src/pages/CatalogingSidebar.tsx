import { Button, Card, Checkbox, Progress, Space, Tag, Typography } from 'antd'
import type { CatalogingJob, CatalogingRun, ChapterItem } from './catalogingTypes'
import { catalogingStatusColor, catalogingStatusLabel } from './catalogingTypes'

const { Text } = Typography

interface CatalogingSidebarProps {
  chapters: ChapterItem[]
  selectedChapterIds: string[]
  jobs: CatalogingJob[]
  jobsTotal: number
  jobsLoading: boolean
  hasMoreJobs: boolean
  onRefreshJobs: () => void
  onLoadMoreJobs: () => void
  activeJob: CatalogingJob | null
  runs: CatalogingRun[]
  onSelectedChapterIdsChange: (ids: string[]) => void
  onLoadJob: (jobId: string) => void
}

function CatalogingSidebar({
  chapters,
  selectedChapterIds,
  jobs,
  jobsTotal,
  jobsLoading,
  hasMoreJobs,
  onRefreshJobs,
  onLoadMoreJobs,
  activeJob,
  runs,
  onSelectedChapterIdsChange,
  onLoadJob,
}: CatalogingSidebarProps) {
  const handleChapterSelection = (values: Array<string | number | boolean>) => {
    onSelectedChapterIdsChange(values.map(String))
  }

  return (
    <>
      <Card title="历史任务" size="small" style={{ marginBottom: 16 }} extra={
        <Button size="small" loading={jobsLoading} onClick={onRefreshJobs}>刷新历史</Button>
      }>
        <div style={{ maxHeight: 260, overflow: 'auto' }}>
          {jobs.length === 0 ? (
            <Text type="secondary">暂无历史任务</Text>
          ) : jobs.map((item) => (
            <Card
              key={item.id}
              size="small"
              style={{ marginBottom: 8, borderColor: activeJob?.id === item.id ? '#1677ff' : undefined }}
            >
              <Space direction="vertical" style={{ width: '100%' }}>
                <Space style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Tag color={catalogingStatusColor[item.status] || 'default'}>
                    {catalogingStatusLabel[item.status] || item.status}
                  </Tag>
                  <Text type="secondary">{item.completed_chapters || 0}/{item.total_chapters || 0}</Text>
                </Space>
                <Progress
                  percent={item.total_chapters ? Math.round(((item.completed_chapters || 0) / item.total_chapters) * 100) : 0}
                  size="small"
                />
                <Button size="small" block onClick={() => onLoadJob(item.id)}>载入任务</Button>
              </Space>
            </Card>
          ))}
        </div>
        <Text type="secondary">已显示 {jobs.length} / {jobsTotal} 条任务</Text>
        {hasMoreJobs && (
          <Button size="small" block loading={jobsLoading} onClick={onLoadMoreJobs} style={{ marginTop: 8 }}>
            加载更早任务
          </Button>
        )}
      </Card>

      <Card title="章节范围" size="small">
        <Space style={{ marginBottom: 8 }}>
          <Button size="small" onClick={() => onSelectedChapterIdsChange(chapters.map((item) => item.id))}>全选</Button>
          <Button size="small" onClick={() => onSelectedChapterIdsChange([])}>清空</Button>
        </Space>
        <div style={{ maxHeight: 520, overflow: 'auto' }}>
          <Checkbox.Group value={selectedChapterIds} onChange={handleChapterSelection}>
            <Space direction="vertical" style={{ width: '100%' }}>
              {chapters.map((chapter) => (
                <Checkbox key={chapter.id} value={chapter.id}>
                  <span title={chapter.title} style={{ display: 'inline-block', maxWidth: 210, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom' }}>
                    {chapter.title}
                  </span>
                </Checkbox>
              ))}
            </Space>
          </Checkbox.Group>
        </div>
      </Card>

      <Card title="章节进度" size="small" style={{ marginTop: 16 }}>
        <div style={{ maxHeight: 260, overflow: 'auto' }}>
          {runs.length === 0 ? (
            <Text type="secondary">任务启动后显示</Text>
          ) : runs.map((run) => (
            <div key={run.id} style={{ display: 'flex', gap: 8, justifyContent: 'space-between', marginBottom: 8 }}>
              <span title={run.chapter_title} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {run.chapter_title}
              </span>
              <Tag color={catalogingStatusColor[run.status] || 'default'}>
                {catalogingStatusLabel[run.status] || run.status}
              </Tag>
            </div>
          ))}
        </div>
      </Card>
    </>
  )
}

export default CatalogingSidebar
