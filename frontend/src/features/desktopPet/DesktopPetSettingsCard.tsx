import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Col, Row, Slider, Space, Switch, Typography, message } from 'antd'
import { EyeInvisibleOutlined, EyeOutlined, SaveOutlined } from '@ant-design/icons'
import { apiClient } from '../../shared/api/client'
import type { ApiEnvelope } from '../../shared/api/contracts'
import {
  applyDesktopPetPreferences,
  hasDesktopPetBridge,
  hideDesktopPet,
  showDesktopPet,
} from './nativeBridge'
import type { DesktopPetSettings, DesktopPetSettingsPatch } from './types'

const { Paragraph, Text } = Typography

interface DesktopPetSettingsCardProps {
  settings: DesktopPetSettings | null
  loading?: boolean
  onSaved: (settings: DesktopPetSettings) => void
}

function editableSettings(settings: DesktopPetSettings): Required<DesktopPetSettingsPatch> {
  return {
    desktop_pet_enabled: settings.desktop_pet_enabled,
    desktop_pet_scale: settings.desktop_pet_scale,
    desktop_pet_opacity: settings.desktop_pet_opacity,
    desktop_pet_muted: settings.desktop_pet_muted,
    desktop_pet_on_top: settings.desktop_pet_on_top,
  }
}

export default function DesktopPetSettingsCard({
  settings,
  loading = false,
  onSaved,
}: DesktopPetSettingsCardProps) {
  const [draft, setDraft] = useState<Required<DesktopPetSettingsPatch> | null>(
    settings ? editableSettings(settings) : null,
  )
  const [saving, setSaving] = useState(false)
  const nativeBridgeAvailable = hasDesktopPetBridge()

  useEffect(() => {
    if (settings) setDraft(editableSettings(settings))
  }, [settings])

  const changed = useMemo(() => {
    if (!settings || !draft) return false
    return Object.entries(draft).some(([key, value]) => (
      settings[key as keyof typeof draft] !== value
    ))
  }, [draft, settings])

  const updateDraft = <Key extends keyof Required<DesktopPetSettingsPatch>>(
    key: Key,
    value: Required<DesktopPetSettingsPatch>[Key],
  ) => setDraft((current) => current ? { ...current, [key]: value } : current)

  const save = async () => {
    if (!draft) return
    setSaving(true)
    try {
      const response = await apiClient.put<ApiEnvelope<DesktopPetSettings>>('/config/launcher', draft)
      const next = response.data.data
      onSaved(next)
      setDraft(editableSettings(next))
      const applied = await applyDesktopPetPreferences(next)
      if (!next.desktop_pet_enabled) await hideDesktopPet()
      if (next.desktop_pet_enabled && nativeBridgeAvailable) await showDesktopPet()
      message.success(applied || !nativeBridgeAvailable
        ? '桌宠设置已保存'
        : '桌宠设置已保存，重启司命后完整生效')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '桌宠设置保存失败')
    } finally {
      setSaving(false)
    }
  }

  const showPet = async () => {
    if (await showDesktopPet()) message.success('桌宠已显示')
    else message.info('当前没有可唤醒的桌宠窗口；启用后重启司命即可显示')
  }

  if (!settings || !draft) {
    return <Card className="settings-card" title="司命桌宠" loading={loading}>正在读取桌宠设置…</Card>
  }

  return (
    <Card className="settings-card desktop-pet-settings-card" title="司命桌宠" loading={loading && !settings}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <div>
          <Paragraph style={{ marginBottom: 4 }}>
            让司命以透明悬浮角色陪伴创作：抱书、读书、坐着打盹和贴边偷看，并显示真实任务状态。桌宠只读任务信息，不会保存草稿、建档或继续下一章。
          </Paragraph>
          <Text type="secondary">仅 Windows 桌面窗口可用；浏览器模式和手机端不会启动桌宠。</Text>
        </div>

        {!settings.desktop_pet_supported && (
          <Alert
            showIcon
            type="info"
            message="当前运行环境不支持透明桌宠窗口"
            description="设置可以保留，但只有 Windows 桌面窗口模式会创建桌宠。"
          />
        )}

        <Row gutter={[24, 18]} align="middle">
          <Col xs={24} md={12}>
            <Space direction="vertical" size={4}>
              <Text strong>随司命启动桌宠</Text>
              <Text type="secondary">关闭后，下次启动不创建桌宠窗口。</Text>
            </Space>
          </Col>
          <Col xs={24} md={12}>
            <Switch
              checked={draft.desktop_pet_enabled}
              onChange={(checked) => updateDraft('desktop_pet_enabled', checked)}
              checkedChildren="启用"
              unCheckedChildren="关闭"
            />
          </Col>

          <Col xs={24} md={12}>
            <Space direction="vertical" size={4}>
              <Text strong>大小</Text>
              <Text type="secondary">{Math.round(draft.desktop_pet_scale * 100)}%</Text>
            </Space>
          </Col>
          <Col xs={24} md={12}>
            <Slider
              min={0.7}
              max={1.35}
              step={0.05}
              value={draft.desktop_pet_scale}
              tooltip={{ formatter: (value) => `${Math.round(Number(value) * 100)}%` }}
              onChange={(value) => updateDraft('desktop_pet_scale', value)}
            />
          </Col>

          <Col xs={24} md={12}>
            <Space direction="vertical" size={4}>
              <Text strong>透明度</Text>
              <Text type="secondary">{Math.round(draft.desktop_pet_opacity * 100)}%</Text>
            </Space>
          </Col>
          <Col xs={24} md={12}>
            <Slider
              min={0.55}
              max={1}
              step={0.05}
              value={draft.desktop_pet_opacity}
              tooltip={{ formatter: (value) => `${Math.round(Number(value) * 100)}%` }}
              onChange={(value) => updateDraft('desktop_pet_opacity', value)}
            />
          </Col>

          <Col xs={24} md={12}>
            <Space direction="vertical" size={4}>
              <Text strong>始终置顶</Text>
              <Text type="secondary">关闭后，其他窗口可以盖住桌宠。</Text>
            </Space>
          </Col>
          <Col xs={24} md={12}>
            <Switch
              checked={draft.desktop_pet_on_top}
              onChange={(checked) => updateDraft('desktop_pet_on_top', checked)}
            />
          </Col>

          <Col xs={24} md={12}>
            <Space direction="vertical" size={4}>
              <Text strong>静音</Text>
              <Text type="secondary">控制任务完成与报错提示音。</Text>
            </Space>
          </Col>
          <Col xs={24} md={12}>
            <Switch
              checked={draft.desktop_pet_muted}
              onChange={(checked) => updateDraft('desktop_pet_muted', checked)}
            />
          </Col>
        </Row>

        <Space wrap>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saving}
            disabled={!changed}
            onClick={() => void save()}
          >
            保存桌宠设置
          </Button>
          <Button icon={<EyeOutlined />} disabled={!nativeBridgeAvailable} onClick={() => void showPet()}>
            显示桌宠
          </Button>
          <Button icon={<EyeInvisibleOutlined />} disabled={!nativeBridgeAvailable} onClick={() => void hideDesktopPet()}>
            暂时隐藏
          </Button>
          <Text type="secondary">
            {settings.desktop_pet_runtime_active ? '桌面窗口已连接' : '当前不是桌宠运行窗口'}
          </Text>
        </Space>
      </Space>
    </Card>
  )
}
