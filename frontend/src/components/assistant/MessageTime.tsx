import {
  apiDateTimeIso,
  formatAssistantTimestamp,
  formatApiDateTime,
} from '../../utils/dateTime'

interface AssistantMessageTimeProps {
  value?: string | null
}

export function AssistantMessageTime({ value }: AssistantMessageTimeProps) {
  const label = formatAssistantTimestamp(value)
  const iso = apiDateTimeIso(value)
  if (!label || !iso) return null
  return (
    <time
      className="assistant-message-time"
      dateTime={iso}
      title={formatApiDateTime(value) || undefined}
    >
      {label}
    </time>
  )
}
