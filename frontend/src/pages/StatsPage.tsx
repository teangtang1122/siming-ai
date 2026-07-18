import { useState } from 'react'
import {
  Card,
  Col,
  Progress,
  Row,
  Select,
  Statistic,
  Table,
  Typography,
  message,
  InputNumber,
  Button,
  Space,
} from 'antd'
import {
  EditOutlined,
  FireOutlined,
  TrophyOutlined,
  FileTextOutlined,
} from '@ant-design/icons'
import {
  type DailyStats,
  useStatsHistory,
  useTodayStats,
  useUpdateDailyGoal,
} from '../features/statistics'

const { Title, Text } = Typography

interface StatsPageProps {
  projectId: string
}

function StatsPage({ projectId }: StatsPageProps) {
  const [historyDays, setHistoryDays] = useState(7)
  const [goalEditing, setGoalEditing] = useState(false)
  const [goalValue, setGoalValue] = useState(6000)
  const todayQuery = useTodayStats(projectId)
  const historyQuery = useStatsHistory(projectId, historyDays)
  const goalMutation = useUpdateDailyGoal(projectId)
  const today = todayQuery.data
  const history = historyQuery.data

  const updateGoal = async () => {
    try {
      await goalMutation.mutateAsync(goalValue)
      message.success('每日目标已更新')
      setGoalEditing(false)
    } catch (err) {
      message.error(err instanceof Error ? err.message : '更新目标失败')
    }
  }

  const historyColumns = [
    {
      title: '日期',
      dataIndex: 'date',
      key: 'date',
      render: (v: string) => new Date(v).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', weekday: 'short' }),
    },
    {
      title: '字数',
      dataIndex: 'total_words',
      key: 'total_words',
      render: (v: number, record: DailyStats) => (
        <Space>
          <Text strong>{v.toLocaleString()}</Text>
          <Text type="secondary">/ {record.daily_goal.toLocaleString()}</Text>
        </Space>
      ),
    },
    {
      title: '完成度',
      key: 'progress',
      render: (_: unknown, record: DailyStats) => {
        const pct = record.daily_goal > 0 ? Math.min((record.total_words / record.daily_goal) * 100, 100) : 0
        return <Progress percent={Math.round(pct)} size="small" status={pct >= 100 ? 'success' : 'active'} style={{ width: 160 }} />
      },
    },
  ]

  return (
    <div style={{ padding: 16 }}>
      <Title level={4} style={{ marginTop: 0 }}>
        <FireOutlined /> 写作统计
      </Title>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="今日字数"
              value={today?.total_words || 0}
              suffix="字"
              prefix={<EditOutlined />}
              valueStyle={{ color: (today?.total_words || 0) >= (today?.daily_goal || 6000) ? '#52c41a' : '#1677ff' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="每日目标"
              value={today?.daily_goal || 6000}
              suffix="字"
              prefix={<TrophyOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="今日进度"
              value={today?.progress_percent || 0}
              suffix="%"
              precision={1}
              prefix={<FireOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="今日更新章节"
              value={today?.chapters_written || 0}
              suffix="章"
              prefix={<FileTextOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Card style={{ marginBottom: 16 }}>
        <Progress
          percent={Math.min(today?.progress_percent || 0, 100)}
          status={(today?.progress_percent || 0) >= 100 ? 'success' : 'active'}
          strokeColor={{ '0%': '#1677ff', '100%': '#52c41a' }}
        />
        <div style={{ textAlign: 'center', marginTop: 8 }}>
          {today ? (
            <Text type="secondary">
              {today.total_words.toLocaleString()} / {today.daily_goal.toLocaleString()} 字
              {(today?.progress_percent || 0) >= 100 ? ' — 目标达成！' : ''}
            </Text>
          ) : null}
        </div>
      </Card>

      <Card
        title={
          <Space>
            <span>修改每日目标</span>
            {goalEditing ? (
              <>
                <InputNumber min={0} step={500} value={goalValue} onChange={(v) => setGoalValue(v || 0)} style={{ width: 120 }} />
                <Button type="primary" size="small" onClick={updateGoal}>保存</Button>
                <Button size="small" onClick={() => setGoalEditing(false)}>取消</Button>
              </>
            ) : (
              <Button
                icon={<EditOutlined />}
                size="small"
                onClick={() => {
                  setGoalValue(today?.daily_goal || 6000)
                  setGoalEditing(true)
                }}
              >修改</Button>
            )}
          </Space>
        }
        style={{ marginBottom: 16 }}
      />

      <Card
        title="历史统计"
        extra={
          <Select value={historyDays} onChange={setHistoryDays} style={{ width: 140 }}>
            <Select.Option value={7}>最近 7 天</Select.Option>
            <Select.Option value={30}>最近 30 天</Select.Option>
            <Select.Option value={90}>最近 90 天</Select.Option>
          </Select>
        }
      >
        {history && (
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={8}>
              <Statistic title="统计天数" value={history.total_days} suffix="天" />
            </Col>
            <Col span={8}>
              <Statistic title="累计字数" value={history.total_words.toLocaleString()} suffix="字" />
            </Col>
            <Col span={8}>
              <Statistic title="日均字数" value={history.average_words_per_day.toLocaleString()} suffix="字" />
            </Col>
          </Row>
        )}
        <Table
          dataSource={history?.items || []}
          columns={historyColumns}
          rowKey="date"
          loading={historyQuery.isLoading || historyQuery.isFetching}
          pagination={false}
          size="small"
        />
      </Card>
    </div>
  )
}

export default StatsPage
