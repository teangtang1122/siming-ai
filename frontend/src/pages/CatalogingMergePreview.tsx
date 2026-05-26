import { Alert, Col, Descriptions, Input, Row, Select, Space, Spin, Tag, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { apiClient } from '../api/client'
import type { ApiResponse, CatalogingCandidate, CharacterMergePreview, CharacterSnapshot } from './catalogingTypes'
import { safeStringify } from './catalogingTypes'

const { Text } = Typography
const { TextArea } = Input

interface CatalogingMergePreviewProps {
  projectId: string
  candidate: CatalogingCandidate
  draft: string
  disabled?: boolean
  onDraftChange: (candidateId: string, value: string) => void
}

function CatalogingMergePreview({
  projectId,
  candidate,
  draft,
  disabled,
  onDraftChange,
}: CatalogingMergePreviewProps) {
  const [preview, setPreview] = useState<CharacterMergePreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const payload = useMemo(() => parsePayload(draft, candidate.payload), [candidate.payload, draft])

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)
    apiClient
      .get<ApiResponse<CharacterMergePreview>>(`/projects/${projectId}/cataloging/candidates/${candidate.id}/merge-preview`)
      .then((res) => {
        if (active) setPreview(res.data.data)
      })
      .catch((err: Error) => {
        if (active) setError(err.message || '读取合并预览失败')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [candidate.id, projectId])

  const updatePayload = (key: string, value: unknown) => {
    onDraftChange(candidate.id, safeStringify({ ...payload, [key]: value }))
  }

  return (
    <div style={{ marginBottom: 12 }}>
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <div>
          <Text strong>角色合并预览</Text>
          <Text type="secondary" style={{ marginLeft: 8 }}>
            核对后再确认，系统会把副卡片的经历、关系、出场记录合并到主卡片。
          </Text>
        </div>

        {loading && <Spin size="small" />}
        {error && <Alert type="warning" showIcon message={error} />}
        {!loading && preview && (!preview.primary || !preview.secondary) && (
          <Alert
            type="warning"
            showIcon
            message="未能同时找到主角色和待合并角色"
            description="请检查 primary_name / secondary_name 或 canonical_name 是否能匹配现有角色名、ID 或别名。"
          />
        )}

        <Row gutter={12}>
          <Col span={12}>
            <CharacterSnapshotBlock title="主卡片" character={preview?.primary} />
          </Col>
          <Col span={12}>
            <CharacterSnapshotBlock title="待合并卡片" character={preview?.secondary} />
          </Col>
        </Row>

        <Row gutter={12}>
          <Col span={8}>
            <Text type="secondary">主角色</Text>
            <Input
              size="small"
              disabled={disabled}
              value={stringValue(payload.primary_name)}
              onChange={(event) => updatePayload('primary_name', event.target.value)}
            />
          </Col>
          <Col span={8}>
            <Text type="secondary">待合并角色</Text>
            <Input
              size="small"
              disabled={disabled}
              value={stringValue(payload.secondary_name)}
              onChange={(event) => updatePayload('secondary_name', event.target.value)}
            />
          </Col>
          <Col span={8}>
            <Text type="secondary">合并后名称</Text>
            <Input
              size="small"
              disabled={disabled}
              value={stringValue(payload.canonical_name)}
              onChange={(event) => updatePayload('canonical_name', event.target.value)}
            />
          </Col>
        </Row>

        <div>
          <Text type="secondary">别名 / 马甲名</Text>
          <Select
            mode="tags"
            size="small"
            disabled={disabled}
            value={arrayValue(payload.aliases)}
            style={{ width: '100%' }}
            onChange={(value) => updatePayload('aliases', value)}
          />
        </div>

        <div>
          <Text type="secondary">合并证据</Text>
          <Select
            mode="tags"
            size="small"
            disabled={disabled}
            value={arrayValue(payload.evidence_points)}
            style={{ width: '100%' }}
            onChange={(value) => updatePayload('evidence_points', value)}
          />
        </div>

        <Row gutter={12}>
          <Col span={12}>
            <Text type="secondary">合并理由</Text>
            <TextArea
              disabled={disabled}
              value={stringValue(payload.confidence_reason)}
              autoSize={{ minRows: 2, maxRows: 5 }}
              onChange={(event) => updatePayload('confidence_reason', event.target.value)}
            />
          </Col>
          <Col span={12}>
            <Text type="secondary">背景补充</Text>
            <TextArea
              disabled={disabled}
              value={stringValue(payload.background_append)}
              autoSize={{ minRows: 2, maxRows: 5 }}
              onChange={(event) => updatePayload('background_append', event.target.value)}
            />
          </Col>
        </Row>
      </Space>
    </div>
  )
}

function CharacterSnapshotBlock({ title, character }: { title: string; character?: CharacterSnapshot | null }) {
  return (
    <div style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 12, height: '100%' }}>
      <Space direction="vertical" style={{ width: '100%' }} size={8}>
        <Space wrap>
          <Text strong>{title}</Text>
          {character ? <Tag color="blue">{character.name}</Tag> : <Tag>未匹配</Tag>}
          {character?.role_type && <Tag>{character.role_type}</Tag>}
        </Space>
        {character ? (
          <>
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="别名">{tagList(character.aliases)}</Descriptions.Item>
              <Descriptions.Item label="位置">{empty(character.current_location)}</Descriptions.Item>
              <Descriptions.Item label="境界">{empty(character.realm_or_level)}</Descriptions.Item>
              <Descriptions.Item label="状态">{empty(character.life_status || character.physical_state)}</Descriptions.Item>
              <Descriptions.Item label="目标">{empty(character.current_goal)}</Descriptions.Item>
              <Descriptions.Item label="冲突">{empty(character.active_conflict)}</Descriptions.Item>
            </Descriptions>
            <PreviewText label="背景" value={character.background} />
            <PreviewText label="外貌" value={character.appearance} />
            <PreviewText label="性格" value={character.personality} />
            <PreviewText label="能力" value={(character.abilities || []).join('、') || character.abilities_state} />
            <PreviewText label="装备" value={character.items_or_assets} />
          </>
        ) : (
          <Text type="secondary">请先把候选项中的角色名改成现有角色名、ID 或别名。</Text>
        )}
      </Space>
    </div>
  )
}

function PreviewText({ label, value }: { label: string; value?: string | null }) {
  return (
    <div>
      <Text type="secondary">{label}：</Text>
      <Text>{truncate(value)}</Text>
    </div>
  )
}

function tagList(items?: string[]) {
  const values = (items || []).filter(Boolean)
  if (!values.length) return <Text type="secondary">无</Text>
  return (
    <Space size={[4, 4]} wrap>
      {values.map((item) => <Tag key={item}>{item}</Tag>)}
    </Space>
  )
}

function parsePayload(draft: string, fallback: Record<string, unknown>) {
  try {
    const parsed = JSON.parse(draft || '{}')
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>
    }
  } catch {
    // Keep the form usable even while the raw JSON editor has a temporary syntax error.
  }
  return fallback || {}
}

function stringValue(value: unknown) {
  return typeof value === 'string' ? value : ''
}

function arrayValue(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean)
  if (typeof value === 'string') return value.split(/[，,]/).map((item) => item.trim()).filter(Boolean)
  return []
}

function empty(value: unknown) {
  const text = String(value || '').trim()
  return text || <Text type="secondary">无</Text>
}

function truncate(value: unknown) {
  const text = String(value || '').trim()
  if (!text) return <Text type="secondary">无</Text>
  return text.length > 180 ? `${text.slice(0, 180)}...` : text
}

export default CatalogingMergePreview
