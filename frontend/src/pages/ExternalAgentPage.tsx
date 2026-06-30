import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Card,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ApiOutlined,
  LockOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import SystemNav from '../components/SystemNav'
import { getPermissionPackDependencies, PERMISSION_PACKS } from '../types/externalAgentSettings'

const { Title, Paragraph, Text } = Typography

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface GlobalSettings {
  id?: string
  enabled_packs: string[]
  trusted_local_enabled: boolean
  trusted_local_clients: string[]
  require_confirmation_for_writes: boolean
  require_confirmation_for_destructive: boolean
  mcp_permission_source: string
}

const RISK_COLORS: Record<string, string> = {
  safe: 'green',
  low: 'blue',
  medium: 'orange',
  high: 'red',
  destructive: 'red',
}

interface ExternalAgentPageProps {
  embedded?: boolean
}

function ExternalAgentPage({ embedded = false }: ExternalAgentPageProps = {}) {
  const [settings, setSettings] = useState<GlobalSettings | null>(null)
  const [loading, setLoading] = useState(false)

  const fetchSettings = useCallback(async () => {
    try {
      const resp = await apiClient.get<ApiResponse<GlobalSettings>>(
        `/external-agent/settings`
      )
      setSettings(resp.data.data)
    } catch {
      setSettings({
        enabled_packs: ['readonly_collaboration'],
        trusted_local_enabled: false,
        trusted_local_clients: [],
        require_confirmation_for_writes: true,
        require_confirmation_for_destructive: true,
        mcp_permission_source: 'global_settings',
      })
    }
  }, [])

  useEffect(() => {
    fetchSettings()
  }, [fetchSettings])

  const updateSettings = async (updates: Partial<GlobalSettings>) => {
    if (!settings) return
    setLoading(true)
    try {
      const resp = await apiClient.put<ApiResponse<GlobalSettings>>(
        `/external-agent/settings`,
        updates
      )
      setSettings(resp.data.data)
      message.success('设置已保存')
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '保存失败')
    } finally {
      setLoading(false)
    }
  }

  const handlePackToggle = (packName: string, enabled: boolean) => {
    if (!settings) return
    if (!PERMISSION_PACKS.some(p => p.name === packName)) return

    let newPacks: string[]
    if (enabled) {
      const packsToEnable = new Set<string>()
      const addWithDependencies = (name: string) => {
        packsToEnable.add(name)
        getPermissionPackDependencies(name).forEach(addWithDependencies)
      }
      addWithDependencies(packName)
      newPacks = [...new Set([...settings.enabled_packs, ...packsToEnable])]
    } else {
      const packsToDisable = new Set<string>([packName])
      let changed = true
      while (changed) {
        changed = false
        PERMISSION_PACKS.forEach(pack => {
          if (!packsToDisable.has(pack.name) && (pack.dependsOn ?? []).some(dep => packsToDisable.has(dep))) {
            packsToDisable.add(pack.name)
            changed = true
          }
        })
      }
      newPacks = settings.enabled_packs.filter(p => !packsToDisable.has(p))
    }
    updateSettings({ enabled_packs: newPacks })
  }

  if (!settings) return null

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: '0 auto' }}>
      {!embedded && <SystemNav current="external-agent" />}

      <Title level={3}>
        <ApiOutlined style={{ marginRight: 8 }} />
        外部 Agent / MCP 设置
      </Title>

      <Paragraph type="secondary" style={{ marginBottom: 24 }}>
        配置 Claude Code、Codex 等外部 AI 客户端如何通过 MCP 连接司命。
        全局设置适用于所有项目，项目级设置可以覆盖全局设置。
      </Paragraph>

      {/* CLI override warning */}
      {settings.mcp_permission_source === 'cli_override' && (
        <Alert
          type="warning"
          message="CLI 覆盖生效中"
          description="MCP 权限来源为 CLI --permission-pack 参数。UI 设置更改不会生效，直到 MCP 服务器使用 --permission-pack auto 启动。"
          showIcon
          icon={<WarningOutlined />}
          style={{ marginBottom: 16 }}
        />
      )}

      {/* Permission pack toggles */}
      <Card size="small" title="权限包" style={{ marginBottom: 16 }}>
        <Paragraph type="secondary" style={{ marginBottom: 16 }}>
          选择要启用的权限包。启用高级权限包会自动启用所有低级权限包。
        </Paragraph>

        {PERMISSION_PACKS.map(pack => {
          const isEnabled = settings.enabled_packs.includes(pack.name)
          const isLocked = (pack.dependsOn ?? []).some(dep => !settings.enabled_packs.includes(dep))

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
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {pack.tools.length} 个工具
                </Text>
              </div>
              <Paragraph type="secondary" style={{ margin: '8px 0 0 0', fontSize: 13 }}>
                {pack.description}
              </Paragraph>
            </Card>
          )
        })}
      </Card>

      {/* Permanent deny-list notice */}
      <Alert
        message="永久禁止的工具"
        description="API 密钥、模型密钥、加密密钥等敏感工具永远不会通过 MCP 暴露，无论权限包如何配置。"
        type="warning"
        showIcon
        icon={<LockOutlined />}
        style={{ marginBottom: 16 }}
      />

      {/* Trusted local mode */}
      <Card size="small" title="可信本地模式" style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Space>
              <Switch
                checked={settings.trusted_local_enabled}
                onChange={(checked) => updateSettings({ trusted_local_enabled: checked })}
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
      <Card size="small" title="确认设置" style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text>写入操作需要确认</Text>
            <Switch
              checked={settings.require_confirmation_for_writes}
              onChange={(checked) => updateSettings({ require_confirmation_for_writes: checked })}
              disabled={loading || settings.trusted_local_enabled}
              size="small"
            />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text>危险操作需要确认</Text>
            <Switch
              checked={settings.require_confirmation_for_destructive}
              onChange={(checked) => updateSettings({ require_confirmation_for_destructive: checked })}
              disabled={loading}
              size="small"
            />
          </div>
        </Space>
      </Card>

      {/* MCP connection info */}
      <Card size="small" title="MCP 连接信息">
        <Paragraph>
          MCP 服务器通过 stdio 传输运行。在 Claude Desktop 或 Cursor 中配置：
        </Paragraph>
        <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 4, fontSize: 13 }}>
{`{
  "mcpServers": {
    "siming": {
      "command": "python",
      "args": ["scripts/siming-mcp-server.py", "--permission-pack", "auto"],
      "cwd": "D:\\\\AI\\\\agent"
    }
  }
}`}
        </pre>
        <Paragraph type="secondary" style={{ marginTop: 8 }}>
          使用 <code>--permission-pack auto</code>（默认）让 UI 设置生效。
        </Paragraph>
      </Card>
    </div>
  )
}

export default ExternalAgentPage
