import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Input, Space, Spin, Steps, Typography, message } from 'antd'
import {
  ApiOutlined,
  ArrowRightOutlined,
  CheckCircleOutlined,
  ExportOutlined,
  ReloadOutlined,
  RocketOutlined,
} from '@ant-design/icons'
import PageWrapper from '../components/PageWrapper'
import SystemNav from '../components/SystemNav'
import { useGettingStartedStatus } from '../features/onboarding'
import { API_KEY_PORTALS, providerLabel } from '../features/localModels/settingsModelOptions'
import {
  startNovelCreationConceptRun,
  startNovelCreationSession,
  workbenchUrl,
} from '../services/novelCreationAgent'
import './GettingStartedPage.css'

const { Title, Paragraph, Text } = Typography

function FirstIdea({ model, label }: { model: string; label: string }) {
  const navigate = useNavigate()
  const [idea, setIdea] = useState('')
  const [creating, setCreating] = useState(false)

  const createIdeas = async () => {
    const brief = idea.trim()
    if (!brief || creating) return
    setCreating(true)
    try {
      const session = await startNovelCreationSession({ userBrief: brief, mode: 'internal_llm' })
      const run = await startNovelCreationConceptRun(session.id, model)
      navigate(workbenchUrl(session.id, run.id, model))
    } catch (error) {
      message.error(error instanceof Error ? error.message : '创意生成未能启动，请重试')
      setCreating(false)
    }
  }

  return (
    <section className="getting-started-first-idea" aria-label="开始创作">
      <CheckCircleOutlined aria-hidden className="getting-started-ready-icon" />
      <Title level={3}>模型已就绪，开始构思吧</Title>
      <Text className="getting-started-model">本次使用：{label}</Text>
      <Paragraph>说一句你想写的故事，司命会先生成一套创意方向，再按你的反馈完善角色、世界观和大纲。</Paragraph>
      <label htmlFor="getting-started-idea">你想写什么故事？</label>
      <Input.TextArea
        id="getting-started-idea"
        value={idea}
        onChange={(event) => setIdea(event.target.value)}
        placeholder="例如：一个能看见他人寿命的女孩，在修仙世界经营一家只在午夜营业的客栈"
        autoSize={{ minRows: 3, maxRows: 6 }}
        maxLength={2000}
        disabled={creating}
      />
      <Space wrap>
        <Button type="primary" size="large" icon={<RocketOutlined aria-hidden />} loading={creating}
          disabled={!idea.trim()} onClick={() => void createIdeas()}>
          生成小说创意
        </Button>
        <Button disabled={creating} onClick={() => navigate('/settings?section=ai')}>更换模型</Button>
        <Button disabled={creating} onClick={() => navigate('/dashboard')}>先看看作品库</Button>
      </Space>
    </section>
  )
}

export function GettingStartedPanel() {
  const navigate = useNavigate()
  const statusQuery = useGettingStartedStatus()
  const status = statusQuery.data

  if (statusQuery.isLoading) {
    return <div className="getting-started-loading"><Spin tip="正在读取模型配置…"><div /></Spin></div>
  }
  if (!status || statusQuery.isError) {
    return <Alert type="error" showIcon message="暂时无法读取模型配置"
      description={statusQuery.error instanceof Error ? statusQuery.error.message : '请确认司命服务正在运行。'}
      action={<Button aria-label="重试" loading={statusQuery.isFetching} onClick={() => void statusQuery.refetch()}>重试</Button>} />
  }

  const activeModel = status.global_model || status.available_model
  if (status.has_usable_models && activeModel) {
    return <FirstIdea model={`${activeModel.provider}:${activeModel.model}`}
      label={`${providerLabel(activeModel.provider)} · ${activeModel.model}`} />
  }

  return (
    <div className="getting-started-panel">
      <Steps size="small" current={0} className="getting-started-steps" items={[
        { title: '获取 API Key' }, { title: '配置并测试模型' }, { title: '开始创作' },
      ]} />
      <div className="getting-started-intro">
        <div>
          <Title level={3}>通过 API 连接模型</Title>
          <Paragraph type="secondary">准备好服务商的 API Key，连接并测试通过后，即可用于立项、写作和建档。</Paragraph>
        </div>
        <Button icon={<ReloadOutlined aria-hidden />} loading={statusQuery.isFetching} onClick={() => void statusQuery.refetch()}>
          刷新配置状态
        </Button>
      </div>
      {status.has_any_model && <Alert showIcon type="info" message="已有配置，还需完成可用性验证"
        description="请到模型设置查看测试结果，并验证一个可用模型。"
        action={<Button onClick={() => navigate('/settings?section=ai')}>查看并验证</Button>} />}
      <section className="getting-started-api" aria-labelledby="setup-api-title">
        <Title level={4} id="setup-api-title">1. 在官网获取 API Key</Title>
        <Paragraph>选择一家服务商，登录官网后创建 API Key。已有 Key 可直接进行下一步。</Paragraph>
        <div className="getting-started-api-portals">
          {API_KEY_PORTALS.map(({ provider, url }) => (
            <a key={provider} href={url} target="_blank" rel="noopener noreferrer"
              aria-label={`${providerLabel(provider)} 官网获取 API Key`}>
              <span><strong>{providerLabel(provider)}</strong><span>官网获取 API Key</span></span>
              <ExportOutlined aria-hidden />
            </a>
          ))}
        </div>
        <Text type="secondary">费用和可用额度以各服务商官网为准；也支持其他 OpenAI 兼容 API。</Text>
        <div className="getting-started-api-connect">
          <div>
            <Title level={4}>2. 在司命中配置并测试</Title>
            <Paragraph>选择提供商，填写 API Key 和模型，保存后点击“测试并启用”。测试通过后返回这里开始创作。</Paragraph>
          </div>
          <Button type="primary" size="large" icon={<ApiOutlined aria-hidden />}
            onClick={() => navigate('/settings?section=ai&setup=api')}>配置 API</Button>
        </div>
      </section>
    </div>
  )
}

export default function GettingStartedPage() {
  const navigate = useNavigate()
  const deferSetup = () => {
    localStorage.setItem('siming_getting_started_deferred', 'true')
    navigate('/dashboard')
  }
  return (
    <PageWrapper maxWidth={1180} className="getting-started-page">
      <SystemNav current="getting-started" />
      <header className="siming-section-header getting-started-heading">
        <div>
          <span className="siming-section-kicker">第一次使用</span>
          <Title level={2}><RocketOutlined aria-hidden /> 开始写第一本小说</Title>
          <p className="siming-section-description">连接模型，再把故事想法变成作品。也可以稍后配置，先体验手动创作。</p>
        </div>
        <Button icon={<ArrowRightOutlined aria-hidden />} onClick={deferSetup}>稍后设置</Button>
      </header>
      <GettingStartedPanel />
    </PageWrapper>
  )
}
