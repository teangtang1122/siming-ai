import { Button, Dropdown, Tooltip } from 'antd'
import { BgColorsOutlined } from '@ant-design/icons'
import { useTheme } from './ThemeContext'

function ThemeSwitcher() {
  const { currentTheme, setTheme, themes } = useTheme()

  return (
    <Tooltip title="切换主题">
      <Dropdown
        menu={{
          items: themes.map((t) => ({
            key: t.key,
            label: (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 120 }}>
                <span style={{ fontSize: 18 }}>{t.icon}</span>
                <div>
                  <div style={{ fontWeight: t.key === currentTheme.key ? 600 : 400 }}>{t.name}</div>
                  <div style={{ fontSize: 11, color: 'var(--ant-color-text-tertiary)' }}>{t.description}</div>
                </div>
              </div>
            ),
          })),
          selectedKeys: [currentTheme.key],
          onClick: ({ key }) => setTheme(key),
        }}
        trigger={['click']}
        placement="bottomRight"
      >
        <Button type="text" size="small" icon={<BgColorsOutlined />}>
          {currentTheme.icon} {currentTheme.name}
        </Button>
      </Dropdown>
    </Tooltip>
  )
}

export default ThemeSwitcher
