/* Input composer for the assistant chat. */
import { Button, Input, Tag, Typography } from 'antd'
import { SendOutlined, StopOutlined } from '@ant-design/icons'
import { INPUT_PLACEHOLDER } from './constants'

const { Text } = Typography

interface ComposerProps {
  input: string
  generating: boolean
  selectedText?: string
  showSelectionTag: boolean
  onInputChange: (value: string) => void
  onSend: () => void
  onStop: () => void
  onCloseSelectionTag: () => void
}

export function Composer({
  input,
  generating,
  selectedText,
  showSelectionTag,
  onInputChange,
  onSend,
  onStop,
  onCloseSelectionTag,
}: ComposerProps) {
  return (
    <>
      {selectedText && selectedText.trim() && showSelectionTag && (
        <div style={{ padding: '0 0 8px' }}>
          <Tag closable onClose={onCloseSelectionTag} color="blue">
            已选中 {selectedText.length} 字
          </Tag>
        </div>
      )}

      <div className="workspace-assistant-composer">
        <Input.TextArea
          value={input}
          onChange={(event) => onInputChange(event.target.value)}
          placeholder={INPUT_PLACEHOLDER}
          autoSize={{ minRows: 2, maxRows: 5 }}
          disabled={generating}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              onSend()
            }
          }}
        />
        <div className="workspace-assistant-actions">
          <Text type="secondary" style={{ fontSize: 11 }}>Enter 发送，Shift+Enter 换行</Text>
          {generating ? (
            <Button danger icon={<StopOutlined />} onClick={onStop}>
              停止
            </Button>
          ) : (
            <Button type="primary" icon={<SendOutlined />} onClick={onSend} disabled={!input.trim()}>
              发送
            </Button>
          )}
        </div>
      </div>
    </>
  )
}
