import { Alert, Button } from 'antd'
import { PersistentOutcome, RecoveryPanel } from '../interaction'

interface StageBlocker {
  stage: string
  label: string
}
export function StageFeedback({
  currentStage,
  status,
  hasData,
  staleReason,
  blockers,
  error,
  recommendedStageLabel,
  canRetryNext,
  onViewStage,
  onRetryNext,
}: {
  currentStage: string
  status?: string
  hasData: boolean
  staleReason?: string
  blockers: StageBlocker[]
  error?: string
  recommendedStageLabel: string
  canRetryNext: boolean
  onViewStage: (stage: string) => void
  onRetryNext: () => void
}) {
  return (
    <>
      {status === 'generated' && hasData && (
        <PersistentOutcome
          className="creation-stage-outcome"
          outcome="waiting_user"
          title={currentStage === 'final_review' ? '最终审阅已生成，等待你创建正式作品' : '生成完成，等待你确认'}
          description="内容已保存到立项草稿。你可以先阅读、修改或重新生成；只有确认后才会进入下一阶段。"
        />
      )}
      {status === 'stale' && (
        <PersistentOutcome
          className="creation-stage-outcome"
          outcome="blocked"
          title="上游内容已变化，本阶段需要重新校验"
          description={staleReason || '请检查内容后重新生成或编辑，再完成确认。'}
        />
      )}
      {status === 'pending' && blockers.length > 0 && (
        <Alert
          className="creation-stage-outcome"
          type="info"
          showIcon
          message={`先确认“${blockers[0].label}”`}
          description="前置阶段确认后，这一阶段才会开放生成，避免后续内容建立在未定稿的信息上。"
          action={<Button onClick={() => onViewStage(blockers[0].stage)}>返回确认</Button>}
        />
      )}
      {error && (
        <RecoveryPanel
          title="下一步没有启动"
          description={error}
          retryLabel={`重试生成${recommendedStageLabel}`}
          onRetry={canRetryNext ? onRetryNext : undefined}
        />
      )}
    </>
  )
}
