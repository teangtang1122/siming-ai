import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  List,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  LockOutlined,
  SafetyOutlined,
  WarningOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import type { ExternalAgentSettings } from '../types/externalAgentSettings'
import { PERMISSION_PACKS } from '../types/externalAgentSettings'

const { Text, Paragraph } = Typography

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface ExternalAgentPermissionPanelProps {
  projectId: string
}

const RISK_COLORS: Record<string, string> = {
  safe: 'green',
  low: 'blue',
  medium: 'orange',
  high: 'red',
  destructive: 'red',
}

function ExternalAgentPermissionPanel({ projectId }: ExternalAgentPermissionPanelProps) {
  const [settings, setSettings] = useState<ExternalAgentSettings | null>(null)
  const [loading, setLoading] = useState(false)
  const [showTools, setShowTools] = useState<string | null>(null)

  const fetchSettings = useCallback(async () => {
    try {
      const resp = await apiClient.get<ApiResponse<ExternalAgentSettings>>(
        `/projects/${projectId}/agent-runs/settings`
      )
      setSettings(resp.data.data)
    } catch {
      // Use defaults
      setSettings({
        project_id: projectId,
        enabled_packs: ['readonly_collaboration'],
        trusted_local_enabled: false,
        trusted_local_clients: [],
        require_confirmation_for_writes: true,
        require_confirmation_for_destructive: true,
      })
    }
  }, [projectId])

  useEffect(() => {
    fetchSettings()
  }, [fetchSettings])

  const updateSettings = async (updates: Partial<ExternalAgentSettings>) => {
    if (!settings) return
    setLoading(true)
    try {
      const resp = await apiClient.put<ApiResponse<ExternalAgentSettings>>(
        `/projects/${projectId}/agent-runs/settings`,
        updates
      )
      setSettings(resp.data.data)
      message.success('设置已更新')
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '更新失败')
    } finally {
      setLoading(false)
    }
  }

  const handlePackToggle = (packName: string, enabled: boolean) => {
    if (!settings) return
    const packIndex = PERMISSION_PACKS.findIndex(p => p.name === packName)
    if (packIndex === -1) return

    let newPacks: string[]
    if (enabled) {
      // Enable this pack and all lower packs
      const packsToEnable = PERMISSION_PACKS.slice(0, packIndex + 1).map(p => p.name)
      newPacks = [...new Set([...settings.enabled_packs, ...packsToEnable])]
    } else {
      // Disable this pack and all higher packs
      const packsToDisable = PERMISSION_PACKS.slice(packIndex).map(p => p.name)
      newPacks = settings.enabled_packs.filter(p => !packsToDisable.includes(p))
    }
    updateSettings({ enabled_packs: newPacks })
  }

  const handleTrustedLocalToggle = (enabled: boolean) => {
    updateSettings({ trusted_local_enabled: enabled })
  }

  const handleConfirmationToggle = (field: 'require_confirmation_for_writes' | 'require_confirmation_for_destructive', value: boolean) => {
    updateSettings({ [field]: value })
  }

  if (!settings) return null

  return (
    <Card
      size="small"
      title={
        <Space>
          <SafetyOutlined />
          <span>外部 Agent 权限设置</span>
        </Space>
      }
      style={{ marginBottom: 16 }}
    >
      <Paragraph type="secondary" style={{ marginBottom: 16 }}>
        配置 Claude Code 或 Codex 可以在此项目中执行的操作。默认仅允许只读访问。
      </Paragraph>

      {/* Permission packs */}
      {PERMISSION_PACKS.map((pack, index) => {
        const isEnabled = settings.enabled_packs.includes(pack.name)
        const isLocked = index > 0 && !settings.enabled_packs.includes(PERMISSION_PACKS[index - 1].name)

        return (
          <Card
            key={pack.name}
            size="small"
            style={{ marginBottom: 8, opacity: isLocked ? 0.6 : 1 }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Space>
                <Switch
                  checked={isEnabled}
                  onChange={(checked) => handlePackToggle(pack.name, checked)}
                  disabled={isLocked || loading}
                  size="small"
                />
                <Text strong>{pack.label}</Text>
                <Tag color={RISK_COLORS[pack.riskLevel]}>{pack.riskLevel}</Tag>
              </Space>
              <Button
                size="small"
                type="link"
                icon={<ToolOutlined />}
                onClick={() => setShowTools(showTools === pack.name ? null : pack.name)}
              >
                {pack.tools.length} 个工具
              </Button>
            </div>
            <Paragraph type="secondary" style={{ margin: '8px 0 0 0', fontSize: 13 }}>
              {pack.description}
            </Paragraph>
            {showTools === pack.name && (
              <List
                size="small"
                dataSource={pack.tools}
                renderItem={tool => (
                  <List.Item style={{ padding: '4px 0' }}>
                    <Text code style={{ fontSize: 12 }}>{tool}</Text>
                  </List.Item>
                )}
                style={{ marginTop: 8, maxHeight: 200, overflow: 'auto' }}
              />
            )}
          </Card>
        )
      })}

      {/* Permanent deny-list notice */}
      <Alert
        message="永久禁止的工具"
        description="API 密钥、模型密钥、加密密钥等敏感工具永远不会通过 MCP 暴露，无论权限包如何配置。"
        type="warning"
        showIcon
        icon={<LockOutlined />}
        style={{ marginTop: 16, marginBottom: 16 }}
      />

      {/* Trusted local mode */}
      <Card size="small" title="可信本地模式" style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Space>
              <Switch
                checked={settings.trusted_local_enabled}
                onChange={handleTrustedLocalToggle}
                disabled={loading}
                size="small"
              />
              <Text strong>启用可信本地模式</Text>
            </Space>
          </div>
          {settings.trusted_local_enabled && (
            <Alert
              message="警告"
              description="可信本地模式允许外部 Agent 跳过写入确认。仅在您信任的本地机器上启用。所有写入操作仍会被审计记录。"
              type="warning"
              showIcon
              icon={<WarningOutlined />}
            />
          )}
        </Space>
      </Card>

      {/* Confirmation toggles */}
      <Card size="small" title="确认设置">
        <Space direction="vertical" style={{ width: '100%' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text>写入操作需要确认</Text>
            <Switch
              checked={settings.require_confirmation_for_writes}
              onChange={(checked) => handleConfirmationToggle('require_confirmation_for_writes', checked)}
              disabled={loading || settings.trusted_local_enabled}
              size="small"
            />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text>危险操作需要确认</Text>
            <Switch
              checked={settings.require_confirmation_for_destructive}
              onChange={(checked) => handleConfirmationToggle('require_confirmation_for_destructive', checked)}
              disabled={loading}
              size="small"
            />
          </div>
        </Space>
      </Card>
    </Card>
  )
}

export default ExternalAgentPermissionPanel
