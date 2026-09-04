/* Input composer for the assistant chat. */
import { Button, Input, Tag, Typography } from 'antd'
import { SendOutlined, StopOutlined } from '@ant-design/icons'
import { INPUT_PLACEHOLDER } from './constants'

const { Text } = Typography

interface ComposerProps {
  input: string
  generating: boolean
  disabled?: boolean
  disabledPlaceholder?: string
  cancelPending?: boolean
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
  disabled = false,
  disabledPlaceholder,
  cancelPending = false,
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
          placeholder={disabled && disabledPlaceholder ? disabledPlaceholder : INPUT_PLACEHOLDER}
          autoSize={{ minRows: 2, maxRows: 5 }}
          disabled={generating || disabled}
          onKeyDown={(event) => {
            if (disabled) return
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              onSend()
            }
          }}
        />
        <div className="workspace-assistant-actions">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Text type="secondary" style={{ fontSize: 11 }}>Enter 发送，Shift+Enter 换行</Text>
          </div>
          {generating ? (
            <Button
              aria-label={cancelPending ? '正在取消任务' : '取消当前任务'}
              danger
              disabled={cancelPending}
              icon={<StopOutlined />}
              loading={cancelPending}
              onClick={onStop}
            >
              {cancelPending ? '正在取消' : '取消任务'}
            </Button>
          ) : (
            <Button type="primary" icon={<SendOutlined />} onClick={onSend} disabled={disabled || !input.trim()}>
              发送
            </Button>
          )}
        </div>
      </div>
    </>
  )
}
