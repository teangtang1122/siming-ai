import { useCallback, useEffect, useState } from 'react'
import {
  Card,
  Space,
  Tag,
  Typography,
} from 'antd'
import {
  ApiOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import ExternalAgentPermissionPanel from '../components/ExternalAgentPermissionPanel'

const { Title, Paragraph, Text } = Typography

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

interface GlobalSettings {
  enabled_packs: string[]
  trusted_local_enabled: boolean
  mcp_permission_source: string
}

function ExternalAgentPage() {
  const [globalSettings, setGlobalSettings] = useState<GlobalSettings | null>(null)
  const [selectedProjectId] = useState<string>('')

  const fetchGlobalSettings = useCallback(async () => {
    try {
      const resp = await apiClient.get<ApiResponse<GlobalSettings>>(
        `/projects/dummy/agent-runs/global-settings`
      )
      setGlobalSettings(resp.data.data)
    } catch {
      // Use defaults
      setGlobalSettings({
        enabled_packs: ['readonly_collaboration'],
        trusted_local_enabled: false,
        mcp_permission_source: 'global_settings',
      })
    }
  }, [])

  useEffect(() => {
    fetchGlobalSettings()
  }, [fetchGlobalSettings])

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: '0 auto' }}>
      <Title level={3}>
        <ApiOutlined style={{ marginRight: 8 }} />
        外部 Agent / MCP 设置
      </Title>

      <Paragraph type="secondary" style={{ marginBottom: 24 }}>
        配置 Claude Code、Codex 等外部 AI 客户端如何通过 MCP 连接墨枢。
        全局设置适用于所有项目，项目级设置可以覆盖全局设置。
      </Paragraph>

      {/* Global settings info */}
      <Card size="small" title="全局设置" style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <Text>当前权限来源：</Text>
            <Tag color={globalSettings?.mcp_permission_source === 'cli_override' ? 'red' : 'blue'}>
              {globalSettings?.mcp_permission_source === 'cli_override' ? 'CLI 覆盖' : '全局设置'}
            </Tag>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <Text>已启用的权限包：</Text>
            <Space>
              {globalSettings?.enabled_packs?.map(pack => (
                <Tag key={pack}>{pack}</Tag>
              ))}
            </Space>
          </div>
          {globalSettings?.mcp_permission_source === 'cli_override' && (
            <div style={{ background: '#fff2f0', padding: 12, borderRadius: 4, border: '1px solid #ffccc7' }}>
              <Text type="danger">
                ⚠️ MCP 权限来源为 CLI 覆盖。UI 设置更改不会生效，直到 MCP 服务器使用 --permission-pack auto 启动。
              </Text>
            </div>
          )}
        </Space>
      </Card>

      {/* Permission panel (requires a project) */}
      <Card size="small" title="项目级权限设置" style={{ marginBottom: 16 }}>
        <Paragraph type="secondary" style={{ marginBottom: 16 }}>
          项目级设置覆盖全局设置。选择一个项目来查看和修改其权限配置。
        </Paragraph>
        {selectedProjectId ? (
          <ExternalAgentPermissionPanel projectId={selectedProjectId} />
        ) : (
          <Text type="secondary">请从项目工作区进入此页面以查看项目级设置。</Text>
        )}
      </Card>

      {/* MCP connection info */}
      <Card size="small" title="MCP 连接信息">
        <Paragraph>
          MCP 服务器通过 stdio 传输运行。在 Claude Desktop 或 Cursor 中配置：
        </Paragraph>
        <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 4, fontSize: 13 }}>
{`{
  "mcpServers": {
    "moshu": {
      "command": "python",
      "args": ["scripts/moshu-mcp-server.py", "--permission-pack", "auto"],
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
