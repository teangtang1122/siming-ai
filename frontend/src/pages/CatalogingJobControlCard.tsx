import { Button, Card, Progress, Space, Tag, Typography } from 'antd'
import { StepForwardOutlined } from '@ant-design/icons'
import type { CatalogingJob } from './catalogingTypes'
import { catalogingStatusColor } from './catalogingTypes'

const { Text } = Typography

interface CatalogingJobControlCardProps {
  job: CatalogingJob | null
  progress: number
  streaming: boolean
  onApplyPending: () => void
  onRetryCurrent: () => void
  onRerunResolutionCurrent: () => void
  onRecoverCurrent: () => void
  onSkipCurrent: () => void
  onPauseCurrentJob: () => void
  onCancelCurrentJob: () => void
  onResumeCurrentJob: () => void
  onStreamJob: (jobId: string) => void
}

function CatalogingJobControlCard({
  job,
  progress,
  streaming,
  onApplyPending,
  onRetryCurrent,
  onRerunResolutionCurrent,
  onRecoverCurrent,
  onSkipCurrent,
  onPauseCurrentJob,
  onCancelCurrentJob,
  onResumeCurrentJob,
  onStreamJob,
}: CatalogingJobControlCardProps) {
  if (!job) return null

  return (
    <Card size="small" style={{ marginBottom: 16 }}>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Space wrap>
          <Tag color={catalogingStatusColor[job.status] || 'default'}>{job.status}</Tag>
          {(job.effective_model || job.model) && (
            <Tag color={job.provider === 'local_llama_cpp' ? 'volcano' : 'blue'}>
              {job.effective_model || job.model}
              {job.model_source ? ` · ${job.model_source}` : ''}
            </Tag>
          )}
          <Text>章节 {job.completed_chapters || 0}/{job.total_chapters || 0}</Text>
          {job.error && <Text type="danger">{job.error}</Text>}
        </Space>
        <Progress percent={progress} />
        <Space wrap>
          <Button icon={<StepForwardOutlined />} disabled={job.status !== 'waiting_confirmation'} onClick={onApplyPending}>
            写入并继续
          </Button>
          <Button disabled={!['paused_on_failure', 'waiting_confirmation'].includes(job.status)} onClick={onRetryCurrent}>
            重试当前章节
          </Button>
          <Button disabled={!['paused_on_failure', 'waiting_confirmation'].includes(job.status)} onClick={onRerunResolutionCurrent}>
            重跑候选生成
          </Button>
          <Button disabled={job.status !== 'paused_on_failure'} onClick={onRecoverCurrent}>
            使用候选项确认
          </Button>
          <Button danger disabled={!['waiting_confirmation', 'paused_on_failure'].includes(job.status)} onClick={onSkipCurrent}>
            显式跳过当前章节
          </Button>
          <Button disabled={['completed', 'cancelled', 'paused'].includes(job.status)} onClick={onPauseCurrentJob}>
            暂停任务
          </Button>
          <Button danger disabled={['completed', 'cancelled'].includes(job.status)} onClick={onCancelCurrentJob}>
            取消任务
          </Button>
          {!streaming && !['completed', 'failed', 'cancelled'].includes(job.status) && (
            <Button onClick={() => job.status === 'paused' ? onResumeCurrentJob() : onStreamJob(job.id)}>
              继续运行
            </Button>
          )}
        </Space>
      </Space>
    </Card>
  )
}

export default CatalogingJobControlCard
