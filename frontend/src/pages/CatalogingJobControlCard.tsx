import { Button, Card, Progress, Space, Tag, Typography } from 'antd'
import { StepForwardOutlined } from '@ant-design/icons'
import type { CatalogingJob } from './catalogingTypes'
import { catalogingStatusColor, catalogingStatusLabel } from './catalogingTypes'
import { PersistentActionBar, PersistentOutcome } from '../components/interaction'
import type { OperationOutcome, OperationResult } from '../components/interaction'

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
  const completed = job.completed_chapters || 0
  const total = job.total_chapters || 0
  let outcome: OperationOutcome | undefined
  let result: OperationResult | undefined
  let outcomeTitle: string | undefined

  if (job.status === 'waiting_confirmation') {
    outcome = 'waiting_user'
    outcomeTitle = '当前章节已生成档案候选，等待你确认'
    result = {
      summary: '已完成的章节和候选内容都已保存。确认写入后才会继续下一章。',
      completed: completed ? [`${completed} 章已完成`] : [],
      incomplete: ['确认或调整当前章节候选项'],
    }
  } else if (job.status === 'paused_on_failure') {
    outcome = completed ? 'partial_success' : 'blocked'
    outcomeTitle = '当前章节遇到问题，任务已停在最近检查点'
    result = {
      summary: job.error || '可重试当前章节、使用已有候选确认，或显式跳过当前章节。',
      completed: completed ? [`${completed} 章已完成`] : [],
      incomplete: [`${Math.max(0, total - completed)} 章尚未完成`],
    }
  } else if (job.status === 'completed') {
    outcome = 'completed_with_tools'
    outcomeTitle = '作品建档已完成'
    result = {
      summary: `共处理 ${completed}/${total} 章，档案已写入项目数据库。`,
      completed: [`${completed} 章已完成建档`],
      warnings: job.failed_chapters ? [`${job.failed_chapters} 章曾出现失败或跳过`] : [],
    }
  } else if (job.status === 'failed') {
    outcome = 'failed'
    outcomeTitle = '作品建档失败'
    result = {
      summary: job.error || '任务没有完成，可从当前章节重新尝试。',
      completed: completed ? [`${completed} 章已完成`] : [],
      incomplete: [`${Math.max(0, total - completed)} 章尚未完成`],
    }
  } else if (job.status === 'cancelled') {
    outcome = 'cancelled'
    outcomeTitle = '作品建档已取消'
    result = {
      summary: '已完成章节仍然保留，未完成章节没有写入。',
      completed: completed ? [`${completed} 章已完成`] : [],
      incomplete: total > completed ? [`${total - completed} 章未处理`] : [],
    }
  }

  return (
    <Card size="small" style={{ marginBottom: 16 }}>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Space wrap>
          <Tag color={catalogingStatusColor[job.status] || 'default'}>{catalogingStatusLabel[job.status] || job.status}</Tag>
          {(job.effective_model || job.model) && (
            <Tag color={job.provider === 'local_llama_cpp' ? 'volcano' : 'blue'}>
              {job.effective_model || job.model}
              {job.model_source ? ` · ${job.model_source}` : ''}
            </Tag>
          )}
          <Text>章节 {job.completed_chapters || 0}/{job.total_chapters || 0}</Text>
          {job.error && !outcome && <Text type="danger">{job.error}</Text>}
        </Space>
        <Progress percent={progress} />
        {outcome && <PersistentOutcome outcome={outcome} title={outcomeTitle} result={result} />}
        {job.status === 'waiting_confirmation' && (
          <PersistentActionBar label="作品建档操作">
            <Button onClick={onRetryCurrent}>重试当前章节</Button>
            <Button onClick={onRerunResolutionCurrent}>重跑候选生成</Button>
            <Button danger onClick={onSkipCurrent}>显式跳过当前章节</Button>
            <Button danger onClick={onCancelCurrentJob}>取消任务</Button>
            <Button type="primary" icon={<StepForwardOutlined aria-hidden="true" />} onClick={onApplyPending}>
              写入并继续
            </Button>
          </PersistentActionBar>
        )}
        {job.status === 'paused_on_failure' && (
          <PersistentActionBar label="作品建档恢复操作">
            <Button onClick={onRerunResolutionCurrent}>重跑候选生成</Button>
            <Button onClick={onRecoverCurrent}>使用候选项确认</Button>
            <Button danger onClick={onSkipCurrent}>显式跳过当前章节</Button>
            <Button danger onClick={onCancelCurrentJob}>取消任务</Button>
            <Button type="primary" onClick={onRetryCurrent}>重试当前章节</Button>
          </PersistentActionBar>
        )}
        {['queued', 'running'].includes(job.status) && (
          <PersistentActionBar label="作品建档运行操作">
            <Button danger onClick={onCancelCurrentJob}>取消任务</Button>
            {streaming ? (
              <Button onClick={onPauseCurrentJob}>暂停任务</Button>
            ) : (
              <Button type="primary" onClick={() => onStreamJob(job.id)}>继续运行</Button>
            )}
          </PersistentActionBar>
        )}
        {job.status === 'paused' && (
          <PersistentActionBar label="作品建档暂停操作">
            <Button danger onClick={onCancelCurrentJob}>取消任务</Button>
            <Button type="primary" onClick={onResumeCurrentJob}>继续运行</Button>
          </PersistentActionBar>
        )}
      </Space>
    </Card>
  )
}

export default CatalogingJobControlCard
