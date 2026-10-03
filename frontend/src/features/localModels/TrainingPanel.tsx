import { useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { PauseOutlined, PlayCircleOutlined, PlusOutlined, StopOutlined } from '@ant-design/icons'
import { apiClient } from '../../api/client'
import type { CatalogResponse, HardwareProfile, TrainingDataset, TrainingJob } from './types'

const { Paragraph } = Typography

const TRAINABLE_MODEL_KEYS = new Set([
  'qwen3.6-27b-q4',
  'qwen3.8-27b-q4',
])

interface Props {
  hardware: HardwareProfile | null
  catalog: CatalogResponse | null
  datasets: TrainingDataset[]
  jobs: TrainingJob[]
  projects: Array<{ id: string; title: string }>
  onRefresh: () => Promise<void>
}

export default function TrainingPanel({
  hardware,
  catalog,
  datasets,
  jobs,
  projects,
  onRefresh,
}: Props) {
  const [datasetOpen, setDatasetOpen] = useState(false)
  const [jobOpen, setJobOpen] = useState(false)
  const [datasetForm] = Form.useForm()
  const [jobForm] = Form.useForm()

  const trainableModels = useMemo(
    () => (catalog?.items || []).filter((item) => TRAINABLE_MODEL_KEYS.has(item.model_key)),
    [catalog],
  )

  const createDataset = async () => {
    const values = await datasetForm.validateFields()
    try {
      await apiClient.post('/local-models/training/datasets', {
        ...values,
        chapter_ids: [],
      })
      message.success('训练集已生成')
      setDatasetOpen(false)
      datasetForm.resetFields()
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const createJob = async () => {
    const values = await jobForm.validateFields()
    try {
      await apiClient.post('/local-models/training/jobs', values)
      message.success('训练任务已启动')
      setJobOpen(false)
      jobForm.resetFields()
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const control = async (job: TrainingJob, action: string) => {
    try {
      await apiClient.post(`/local-models/training/jobs/${job.id}/${action}`)
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  if (!hardware?.training_supported) {
    return (
      <Alert
        showIcon
        type="warning"
        message="此设备暂不支持内置训练"
        description="当前内置基座均为 27B，QLoRA 训练需要至少 24GB 显存的 NVIDIA 显卡。本地推理仍可单独尝试。"
      />
    )
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        showIcon
        type="warning"
        message="LoRA 训练 Beta"
        description="首次训练会下载隔离环境。27B QLoRA 需要至少 24GB 显存。数据和产物只保存在本机。"
      />

      <Card
        size="small"
        title="训练数据集"
        extra={<Button icon={<PlusOutlined />} onClick={() => setDatasetOpen(true)}>创建数据集</Button>}
      >
        <Table
          rowKey="id"
          pagination={false}
          dataSource={datasets}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '样本', dataIndex: 'sample_count', width: 100 },
            { title: '训练/验证', width: 130, render: (_, row) => `${row.train_count} / ${row.eval_count}` },
            { title: '平均长度', width: 120, render: (_, row) => `${Math.round(row.stats.avg_chars || 0)} 字符` },
            { title: '权利确认', width: 100, render: (_, row) => <Tag color={row.rights_confirmed ? 'success' : 'warning'}>{row.rights_confirmed ? '已确认' : '未确认'}</Tag> },
          ]}
        />
      </Card>

      <Card
        size="small"
        title="训练任务"
        extra={<Button type="primary" icon={<PlayCircleOutlined />} disabled={!datasets.length} onClick={() => setJobOpen(true)}>开始训练</Button>}
      >
        <Table
          rowKey="id"
          pagination={false}
          dataSource={jobs}
          expandable={{
            expandedRowRender: (job) => (
              <Paragraph code copyable style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
                {job.log_tail || '尚无日志'}
              </Paragraph>
            ),
          }}
          columns={[
            { title: '任务', dataIndex: 'name' },
            { title: '基座', dataIndex: 'base_model_key', width: 140 },
            {
              title: '进度',
              width: 220,
              render: (_, job) => (
                <Progress
                  percent={Math.round((job.progress || 0) * 100)}
                  status={job.status === 'failed' ? 'exception' : job.status === 'completed' ? 'success' : 'active'}
                  format={() => job.status}
                />
              ),
            },
            {
              title: '操作',
              width: 180,
              render: (_, job) => (
                <Space>
                  {['running', 'preparing', 'converting'].includes(job.status) && (
                    <Button icon={<PauseOutlined />} onClick={() => control(job, 'pause')}>暂停</Button>
                  )}
                  {job.status === 'paused' && (
                    <Button icon={<PlayCircleOutlined />} onClick={() => control(job, 'resume')}>继续</Button>
                  )}
                  {!['completed', 'failed', 'cancelled'].includes(job.status) && (
                    <Button danger icon={<StopOutlined />} onClick={() => control(job, 'cancel')} />
                  )}
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal title="创建本地训练集" open={datasetOpen} onCancel={() => setDatasetOpen(false)} onOk={createDataset} okText="生成训练集">
        <Form form={datasetForm} layout="vertical" initialValues={{
          include_outline_pairs: true,
          include_revision_pairs: true,
          include_character_dialogue: true,
          eval_ratio: 0.1,
          rights_confirmed: false,
        }}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="project_id" label="作品" rules={[{ required: true }]}>
            <Select options={projects.map((item) => ({ value: item.id, label: item.title }))} />
          </Form.Item>
          <Space direction="vertical">
            <Form.Item name="include_outline_pairs" valuePropName="checked" noStyle><Checkbox>大纲到正文样本</Checkbox></Form.Item>
            <Form.Item name="include_revision_pairs" valuePropName="checked" noStyle><Checkbox>修改前后重写样本</Checkbox></Form.Item>
            <Form.Item name="include_character_dialogue" valuePropName="checked" noStyle><Checkbox>角色对白样本</Checkbox></Form.Item>
          </Space>
          <Form.Item name="eval_ratio" label="验证集比例" style={{ marginTop: 16 }}>
            <InputNumber min={0.05} max={0.3} step={0.05} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="rights_confirmed"
            valuePropName="checked"
            rules={[{ validator: (_, value) => value ? Promise.resolve() : Promise.reject(new Error('必须确认文本权利')) }]}
          >
            <Checkbox>我确认拥有所选文本的训练使用权</Checkbox>
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="开始 QLoRA 训练" open={jobOpen} onCancel={() => setJobOpen(false)} onOk={createJob} okText="开始训练">
        <Form form={jobForm} layout="vertical" initialValues={{
          epochs: 1,
          learning_rate: 0.0002,
          lora_rank: 16,
          batch_size: 1,
          gradient_accumulation: 8,
          max_sequence_length: 4096,
        }}>
          <Form.Item name="name" label="适配器名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="dataset_id" label="数据集" rules={[{ required: true }]}>
            <Select options={datasets.map((item) => ({ value: item.id, label: `${item.name} (${item.sample_count})` }))} />
          </Form.Item>
          <Form.Item name="base_model_key" label="训练基座" rules={[{ required: true }]}>
            <Select options={trainableModels.map((item) => ({ value: item.model_key, label: item.display_name }))} />
          </Form.Item>
          <Form.Item name="project_id" label="绑定作品">
            <Select allowClear options={projects.map((item) => ({ value: item.id, label: item.title }))} />
          </Form.Item>
          <Space wrap>
            <Form.Item name="epochs" label="轮数"><InputNumber min={0.1} max={10} step={0.5} /></Form.Item>
            <Form.Item name="lora_rank" label="LoRA Rank"><InputNumber min={4} max={128} /></Form.Item>
            <Form.Item name="max_sequence_length" label="样本长度"><InputNumber min={512} max={16384} step={512} /></Form.Item>
          </Space>
        </Form>
      </Modal>
    </Space>
  )
}
