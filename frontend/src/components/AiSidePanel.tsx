import { Button, Typography } from 'antd'
import { MenuFoldOutlined, RobotOutlined } from '@ant-design/icons'
import './AiSidePanel.css'

const { Title } = Typography

interface AiSidePanelProps {
  collapsed: boolean
  onToggle: () => void
  width: number
  onResizeHandle: (e: React.MouseEvent) => void
  dragging: boolean
  children: React.ReactNode
}

function AiSidePanel({ collapsed, onToggle, width, onResizeHandle, dragging, children }: AiSidePanelProps) {
  if (collapsed) return null

  return (
    <aside
      className={`ai-side-panel${dragging ? ' ai-side-panel-dragging' : ''}`}
      style={{ width }}
    >
      <div className="ai-side-resize-handle" onMouseDown={onResizeHandle} />
      <div className="ai-side-head">
        <Title level={5} style={{ margin: 0 }}>
          <RobotOutlined /> 项目助手
        </Title>
        <Button type="text" size="small" icon={<MenuFoldOutlined />} onClick={onToggle} />
      </div>
      <div className="ai-side-body">
        {children}
      </div>
    </aside>
  )
}

export default AiSidePanel
