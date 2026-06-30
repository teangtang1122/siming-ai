import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Input, Modal, Select, Space, Switch, Tabs, Typography, message } from 'antd'
import { ExperimentOutlined, HddOutlined, SlidersOutlined } from '@ant-design/icons'
import { apiClient } from '../api/client'
import ModelCatalogPanel from '../features/localModels/ModelCatalogPanel'
import TrainingPanel from '../features/localModels/TrainingPanel'
import type {
  CatalogResponse,
  DownloadTask,
  HardwareProfile,
  ModelAdapter,
  TrainingDataset,
  TrainingJob,
} from '../features/localModels/types'

const { Title, Paragraph, Text } = Typography

interface Props {
  embedded?: boolean
}

export default function ModelCenterPage({ embedded = false }: Props) {
  const [hardware, setHardware] = useState<HardwareProfile | null>(null)
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null)
  const [downloads, setDownloads] = useState<DownloadTask[]>([])
  const [datasets, setDatasets] = useState<TrainingDataset[]>([])
  const [jobs, setJobs] = useState<TrainingJob[]>([])
  const [adapters, setAdapters] = useState<ModelAdapter[]>([])
  const [projects, setProjects] = useState<Array<{ id: string; title: string }>>([])
  const [loading, setLoading] = useState(true)
  const [compareOpen, setCompareOpen] = useState(false)
  const [comparePrompt, setComparePrompt] = useState('')
  const [compareModel, setCompareModel] = useState<string>()
  const [compareAdapters, setCompareAdapters] = useState<string[]>([])
  const [comparison, setComparison] = useState<{ id: string; variants: Array<{ label: string; content: string }> } | null>(null)
  const [reveal, setReveal] = useState<Record<string, string> | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [hardwareRes, catalogRes, downloadsRes, datasetsRes, jobsRes, adaptersRes, projectsRes] = await Promise.all([
        apiClient.get<{ data: HardwareProfile }>('/local-models/hardware'),
        apiClient.get<{ data: CatalogResponse }>('/local-models/catalog'),
        apiClient.get<{ data: { items: DownloadTask[] } }>('/local-models/downloads'),
        apiClient.get<{ data: { items: TrainingDataset[] } }>('/local-models/training/datasets'),
        apiClient.get<{ data: { items: TrainingJob[] } }>('/local-models/training/jobs'),
        apiClient.get<{ data: { items: ModelAdapter[] } }>('/local-models/adapters'),
        apiClient.get<{ data: { items: Array<{ id: string; title: string }> } }>('/projects'),
      ])
      setHardware(hardwareRes.data.data)
      setCatalog(catalogRes.data.data)
      setDownloads(downloadsRes.data.data.items || [])
      setDatasets(datasetsRes.data.data.items || [])
      setJobs(jobsRes.data.data.items || [])
      setAdapters(adaptersRes.data.data.items || [])
      setProjects(projectsRes.data.data.items || [])
    } catch (error: any) {
      message.error(error.message || '模型中心加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    const timer = window.setInterval(() => {
      const active = downloads.some((item) => !['completed', 'failed', 'cancelled'].includes(item.status))
        || jobs.some((item) => !['completed', 'failed', 'cancelled', 'paused'].includes(item.status))
      if (active) refresh()
    }, 1500)
    return () => window.clearInterval(timer)
  }, [downloads, jobs, refresh])

  const updateAdapter = async (adapter: ModelAdapter, values: Partial<ModelAdapter>) => {
    try {
      await apiClient.patch(`/local-models/adapters/${adapter.id}`, values)
      await refresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const runComparison = async () => {
    if (!compareModel || !comparePrompt.trim()) {
      message.warning('请选择模型并填写同一段写作要求')
      return
    }
    const hide = message.loading('正在依次生成盲测文本...', 0)
    try {
      const response = await apiClient.post<{ data: any }>('/local-models/adapters/compare', {
        model_key: compareModel,
        prompt: comparePrompt,
        adapter_ids: compareAdapters,
      })
      setComparison({
        id: response.data.data.comparison_id,
        variants: response.data.data.variants,
      })
      setReveal(null)
    } catch (error: any) {
      message.error(error.message)
    } finally {
      hide()
    }
  }

  const revealComparison = async () => {
    if (!comparison) return
    const response = await apiClient.get<{ data: { mapping: Record<string, string> } }>(
      `/local-models/adapters/compare/${comparison.id}/reveal`,
    )
    setReveal(response.data.data.mapping)
  }

  return (
    <div style={{ padding: embedded ? 24 : '24px max(24px, 5vw)', maxWidth: 1500, margin: '0 auto' }}>
      <Title level={2} style={{ marginBottom: 4 }}>本地 AI 模型中心</Title>
      <Paragraph type="secondary">
        下载一次即可离线使用。司命自动管理模型、运行时、任务路由和写作适配器。
      </Paragraph>
      <Tabs items={[
        {
          key: 'models',
          label: <Space><HddOutlined />模型与运行时</Space>,
          children: (
            <ModelCatalogPanel
              hardware={hardware}
              catalog={catalog}
              downloads={downloads}
              loading={loading}
              onRefresh={refresh}
            />
          ),
        },
        {
          key: 'adapters',
          label: <Space><SlidersOutlined />写作适配器</Space>,
          children: (
            <Card
              size="small"
              title="已安装适配器"
              extra={<Button onClick={() => setCompareOpen(true)}>基座 / 适配器盲测</Button>}
            >
              {adapters.length === 0 ? (
                <Paragraph type="secondary">尚未安装官方或私人写作适配器。基座模型仍可正常写作。</Paragraph>
              ) : (
                <Space direction="vertical" style={{ width: '100%' }}>
                  {adapters.map((adapter) => (
                    <Card key={adapter.id} size="small">
                      <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
                        <Space direction="vertical" size={0}>
                          <Text strong>{adapter.name}</Text>
                          <Text type="secondary">{adapter.base_model_key} · {adapter.scope}</Text>
                        </Space>
                        <Space>
                          <Switch
                            checked={adapter.enabled}
                            checkedChildren="启用"
                            unCheckedChildren="停用"
                            onChange={(enabled) => updateAdapter(adapter, { enabled })}
                          />
                          <Button onClick={() => updateAdapter(adapter, {
                            is_default_for_writing: !adapter.is_default_for_writing,
                          })}>
                            {adapter.is_default_for_writing ? '取消写作默认' : '设为写作默认'}
                          </Button>
                        </Space>
                      </Space>
                    </Card>
                  ))}
                </Space>
              )}
            </Card>
          ),
        },
        {
          key: 'training',
          label: <Space><ExperimentOutlined />LoRA 训练 Beta</Space>,
          children: (
            <TrainingPanel
              hardware={hardware}
              catalog={catalog}
              datasets={datasets}
              jobs={jobs}
              projects={projects}
              onRefresh={refresh}
            />
          ),
        },
      ]} />
      <Modal
        width={900}
        title="写作适配器盲测"
        open={compareOpen}
        onCancel={() => setCompareOpen(false)}
        footer={[
          <Button key="run" type="primary" onClick={runComparison}>生成对比</Button>,
          <Button key="reveal" disabled={!comparison || Boolean(reveal)} onClick={revealComparison}>揭晓来源</Button>,
        ]}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Select
            placeholder="选择基座模型"
            value={compareModel}
            onChange={setCompareModel}
            options={(catalog?.items || []).filter((item) => item.status === 'installed').map((item) => ({
              value: item.model_key,
              label: item.display_name,
            }))}
          />
          <Select
            mode="multiple"
            maxCount={2}
            placeholder="选择最多两个适配器；始终包含基座模型"
            value={compareAdapters}
            onChange={setCompareAdapters}
            options={adapters.filter((item) => !compareModel || item.base_model_key === compareModel).map((item) => ({
              value: item.id,
              label: item.name,
            }))}
          />
          <Input.TextArea
            rows={4}
            value={comparePrompt}
            onChange={(event) => setComparePrompt(event.target.value)}
            placeholder="例如：写一段主角在雨夜发现宗门戒律被篡改的场景，800字。"
          />
          {comparison?.variants.map((variant) => (
            <Card key={variant.label} size="small" title={`方案 ${variant.label}${reveal ? ` · ${reveal[variant.label]}` : ''}`}>
              <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{variant.content}</Paragraph>
            </Card>
          ))}
        </Space>
      </Modal>
    </div>
  )
}
