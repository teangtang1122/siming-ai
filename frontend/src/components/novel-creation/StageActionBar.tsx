import { BookOutlined, CheckCircleOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { Button } from 'antd'
import { PersistentActionBar } from '../interaction'

export function StageActionBar({
  currentStage,
  status,
  hasData,
  busy,
  createdProjectId,
  finalReady,
  recommendedStage,
  recommendedStageLabel,
  nextStageLabel,
  canGenerate,
  currentStageLabel,
  onOpenProject,
  onCreateProject,
  onConfirmOnly,
  onConfirmAndContinue,
  onContinue,
  onGenerate,
}: {
  currentStage: string
  status?: string
  hasData: boolean
  busy: boolean
  createdProjectId?: string | null
  finalReady: boolean
  recommendedStage?: string | null
  recommendedStageLabel: string
  nextStageLabel: string
  canGenerate: boolean
  currentStageLabel: string
  onOpenProject: () => void
  onCreateProject: () => void
  onConfirmOnly: () => void
  onConfirmAndContinue: () => void
  onContinue: () => void
  onGenerate: () => void
}) {
  let action = null
  if (currentStage === 'final_review') {
    action = createdProjectId ? (
      <Button size="large" type="primary" icon={<BookOutlined />} onClick={onOpenProject}>进入已创建作品</Button>
    ) : (
      <Button size="large" type="primary" icon={<CheckCircleOutlined />} disabled={!finalReady || busy} loading={busy} onClick={onCreateProject}>确认并创建正式作品</Button>
    )
  } else if (status === 'generated' || status === 'stale') {
    action = (
      <>
        <Button size="large" disabled={!hasData || busy} onClick={onConfirmOnly}>仅确认，稍后继续</Button>
        <Button size="large" type="primary" icon={<CheckCircleOutlined />} disabled={!hasData || busy} loading={busy} onClick={onConfirmAndContinue}>
          {nextStageLabel ? `确认并生成${nextStageLabel}` : '确认本阶段'}
        </Button>
      </>
    )
  } else if (status === 'confirmed' && recommendedStage && recommendedStage !== currentStage) {
    action = (
      <Button size="large" type="primary" icon={<PlayCircleOutlined />} disabled={busy} loading={busy} onClick={onContinue}>
        生成{recommendedStageLabel}
      </Button>
    )
  } else if (canGenerate) {
    action = (
      <Button size="large" type="primary" icon={<PlayCircleOutlined />} disabled={busy} loading={busy} onClick={onGenerate}>
        生成{currentStageLabel}
      </Button>
    )
  }

  if (!action) return null
  return (
    <PersistentActionBar className="creation-stage-actions" label="立项阶段操作">
      {action}
    </PersistentActionBar>
  )
}
