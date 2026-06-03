import { useState } from 'react'
import { Button, Modal, Space, Steps, Tag, Typography } from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  LoadingOutlined,
  PauseCircleOutlined,
  InfoCircleOutlined,
  ReloadOutlined,
  RightCircleOutlined,
} from '@ant-design/icons'

const { Text } = Typography

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AgentPlanStepView {
  step_key: string
  tool: string
  status: 'pending' | 'blocked' | 'running' | 'ok' | 'error' | 'skipped'
  label?: string
  detail?: string
  error?: string
  attempt_no?: number
  request?: unknown
  result?: unknown
}

export interface AgentPlanViewState {
  plan_id: string
  plan_name: string
  status: 'pending' | 'running' | 'completed' | 'error'
  steps: AgentPlanStepView[]
}

interface AgentPlanViewProps {
  plan: AgentPlanViewState
  onRetryStep?: (stepKey: string) => void
  onResumeFromStep?: (stepKey: string) => void
  onRetryAll?: () => void
  retryingKey?: string | null
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  blocked: 'orange',
  running: 'blue',
  ok: 'green',
  error: 'red',
  skipped: 'gold',
}

const STATUS_ICON: Record<string, React.ReactNode> = {
  pending: <ClockCircleOutlined />,
  blocked: <PauseCircleOutlined />,
  running: <LoadingOutlined spin />,
  ok: <CheckCircleOutlined />,
  error: <CloseCircleOutlined />,
  skipped: <InfoCircleOutlined />,
}

const STATUS_LABEL: Record<string, string> = {
  pending: '等待中',
  blocked: '已阻塞',
  running: '执行中',
  ok: '完成',
  error: '失败',
  skipped: '已跳过',
}

function stepToAntdStatus(s: string): 'wait' | 'process' | 'finish' | 'error' {
  if (s === 'ok') return 'finish'
  if (s === 'error') return 'error'
  if (s === 'running') return 'process'
  return 'wait'
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function AgentPlanView({
  plan,
  onRetryStep,
  onResumeFromStep,
  onRetryAll,
  retryingKey,
}: AgentPlanViewProps) {
  const [detailStep, setDetailStep] = useState<AgentPlanStepView | null>(null)

  const hasErrors = plan.steps.some((s) => s.status === 'error')

  return (
    <div style={{ marginBottom: 12 }}>
      {/* Plan header */}
      <Space size={8} style={{ marginBottom: 8 }}>
        <Tag color={plan.status === 'completed' ? 'green' : plan.status === 'error' ? 'red' : 'blue'}>
          {plan.plan_name}
        </Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {plan.status === 'completed' ? '计划完成' :
           plan.status === 'error' ? '计划遇到问题' :
           plan.status === 'running' ? '执行中...' : '等待执行'}
        </Text>
        {hasErrors && onRetryAll && (
          <Button
            size="small"
            loading={retryingKey === '__all__'}
            disabled={retryingKey !== null}
            onClick={onRetryAll}
            icon={<ReloadOutlined />}
          >
            重试全部失败
          </Button>
        )}
      </Space>

      {/* Steps as vertical timeline */}
      <Steps
        direction="vertical"
        size="small"
        current={plan.steps.findIndex((s) => s.status === 'running')}
        items={plan.steps.map((step) => {
          const isError = step.status === 'error'
          const isBlocked = step.status === 'blocked'
          const canRetry = isError || isBlocked

          return {
            title: (
              <Space size={6}>
                <Text code style={{ fontSize: 12 }}>{step.tool}</Text>
                {step.label && <Text type="secondary" style={{ fontSize: 12 }}>{step.label}</Text>}
              </Space>
            ),
            status: stepToAntdStatus(step.status),
            description: (
              <div style={{ marginBottom: 4 }}>
                <Space size={6} wrap>
                  <Tag color={STATUS_COLOR[step.status]} icon={STATUS_ICON[step.status]} style={{ fontSize: 11 }}>
                    {STATUS_LABEL[step.status] || step.status}
                  </Tag>
                  {step.attempt_no && step.attempt_no > 1 && (
                    <Tag style={{ fontSize: 11 }}>重试 #{step.attempt_no}</Tag>
                  )}
                </Space>

                {/* Detail / error text */}
                {step.error && (
                  <div style={{ marginTop: 4 }}>
                    <Text type="danger" style={{ fontSize: 12 }}>{step.error}</Text>
                  </div>
                )}
                {step.detail && !step.error && step.status !== 'ok' && (
                  <div style={{ marginTop: 4 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>{step.detail}</Text>
                  </div>
                )}

                {/* Action buttons */}
                {canRetry && (onRetryStep || onResumeFromStep) && (
                  <Space size={4} style={{ marginTop: 4 }}>
                    {onRetryStep && (
                      <Button
                        type="link"
                        size="small"
                        icon={<ReloadOutlined />}
                        loading={retryingKey === step.step_key}
                        disabled={retryingKey !== null}
                        onClick={() => onRetryStep(step.step_key)}
                      >
                        重试
                      </Button>
                    )}
                    {onResumeFromStep && (
                      <Button
                        type="link"
                        size="small"
                        loading={retryingKey === step.step_key}
                        disabled={retryingKey !== null}
                        onClick={() => onResumeFromStep(step.step_key)}
                        icon={<RightCircleOutlined />}
                      >
                        从这里继续
                      </Button>
                    )}
                    <Button
                      type="link"
                      size="small"
                      icon={<InfoCircleOutlined />}
                      onClick={() => setDetailStep(step)}
                    >
                      详情
                    </Button>
                  </Space>
                )}
              </div>
            ),
          }
        })}
      />

      {/* Step detail modal */}
      <Modal
        title="步骤详情"
        open={!!detailStep}
        onCancel={() => setDetailStep(null)}
        footer={null}
        width={600}
      >
        {detailStep && (
          <div>
            <p><strong>步骤 Key：</strong>{detailStep.step_key}</p>
            <p><strong>工具：</strong><Text code>{detailStep.tool}</Text></p>
            {detailStep.label && <p><strong>标签：</strong>{detailStep.label}</p>}
            <p>
              <strong>状态：</strong>
              <Tag color={STATUS_COLOR[detailStep.status]} icon={STATUS_ICON[detailStep.status]}>
                {STATUS_LABEL[detailStep.status] || detailStep.status}
              </Tag>
            </p>
            {detailStep.attempt_no && detailStep.attempt_no > 1 && (
              <p><strong>尝试次数：</strong>第 {detailStep.attempt_no} 次</p>
            )}
            {detailStep.request !== undefined && detailStep.request !== null && (
              <div>
                <Text strong>请求参数：</Text>
                <pre style={{
                  background: '#f5f5f5', padding: 8, borderRadius: 4,
                  maxHeight: 200, overflow: 'auto', fontSize: 12,
                }}>
                  {JSON.stringify(detailStep.request, null, 2)}
                </pre>
              </div>
            )}
            {detailStep.result !== undefined && detailStep.result !== null && (
              <div>
                <Text strong>执行结果：</Text>
                <pre style={{
                  background: '#f5f5f5', padding: 8, borderRadius: 4,
                  maxHeight: 300, overflow: 'auto', fontSize: 12,
                }}>
                  {JSON.stringify(detailStep.result, null, 2)}
                </pre>
              </div>
            )}
            {detailStep.error && (
              <div>
                <Text strong type="danger">错误信息：</Text>
                <pre style={{
                  background: '#fff2f0', padding: 8, borderRadius: 4,
                  maxHeight: 200, overflow: 'auto', fontSize: 12, color: '#cf1322',
                }}>
                  {detailStep.error}
                </pre>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
