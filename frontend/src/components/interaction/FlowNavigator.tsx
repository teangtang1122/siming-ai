import { Steps } from 'antd'
import type { StepsProps } from 'antd'

export function FlowNavigator({
  current,
  items,
  onChange,
  direction = 'vertical',
}: {
  current: number
  items: StepsProps['items']
  onChange?: (index: number) => void
  direction?: StepsProps['direction']
}) {
  return (
    <Steps
      className="siming-flow-navigator"
      current={current}
      direction={direction}
      onChange={onChange}
      items={items}
      aria-label="创作流程"
    />
  )
}
