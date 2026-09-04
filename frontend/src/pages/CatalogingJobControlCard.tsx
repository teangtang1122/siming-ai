import { Button, Card, Progress, Space, Tag, Typography } from 'antd'
import { StepForwardOutlined } from '@ant-design/icons'
import type { CatalogingJob, CatalogingRun } from './catalogingTypes'
import { catalogingStatusColor, catalogingStatusLabel } from './catalogingTypes'
import { PersistentActionBar, PersistentOutcome } from '../components/interaction'
import type { OperationOutcome, OperationResult } from '../components/interaction'
import { formatApiDateTime } from '../utils/dateTime'

const { Text } = Typography

const catalogingStageLabel: Record<string, string> = {
  queued: '准备中',
  facts: '事实抽取',
  candidates: '候选生成',
  apply: '写入档案',
  completed: '处理完成',
}

interface CatalogingJobControlCardProps {
  job: CatalogingJob | null
  currentRun?: CatalogingRun
  factCount?: number
  candidateCount?: number
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
  currentRun,
  factCount = 0,
  candidateCount = 0,
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
    outcomeTitle = job.review_warning
      ? '候选已生成，有原文线索需要你核对'
      : '当前章节已生成档案候选，等待你确认'
    result = {
      summary: job.review_warning || '已完成的章节和候选内容都已保存。确认写入后才会继续下一章。',
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
        {['queued', 'running'].includes(job.status) && currentRun && (
          <Space direction="vertical" size={4}>
            <Space wrap>
              <Text type="secondary">当前章节：{currentRun.chapter_title}</Text>
              <Tag color={catalogingStatusColor[currentRun.status] || 'default'}>
                {job.execution_mode === 'auto' && currentRun.status === 'awaiting_confirmation'
                  ? '候选应用中'
                  : catalogingStatusLabel[currentRun.status] || currentRun.status}
              </Tag>
              {job.current_stage && (
                <Tag color="processing">{catalogingStageLabel[job.current_stage] || job.current_stage}</Tag>
              )}
              <Text type="secondary">已保存 {factCount} 条事实 · {candidateCount} 条候选</Text>
            </Space>
            {(job.current_message || job.last_activity_at) && (
              <Space wrap>
                {job.current_message && <Text type="secondary">{job.current_message}</Text>}
                {job.last_activity_at && (
                  <Text type="secondary">
                    最近活动：{formatApiDateTime(job.last_activity_at) || '时间未记录'}
                  </Text>
                )}
              </Space>
            )}
          </Space>
        )}
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
