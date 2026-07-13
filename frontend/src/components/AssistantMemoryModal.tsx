import { Modal } from 'antd'
import { AssistantMemoryList } from './AssistantMemoryList'

interface AssistantMemoryModalProps {
  projectId: string
  open: boolean
  onClose: () => void
}

export function AssistantMemoryModal({ projectId, open, onClose }: AssistantMemoryModalProps) {
  return (
    <Modal
      title="记忆管理"
      open={open}
      onCancel={onClose}
      footer={null}
      width={700}
      destroyOnHidden
    >
      <AssistantMemoryList projectId={projectId} />
    </Modal>
  )
}
