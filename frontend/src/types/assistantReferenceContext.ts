/** Data-only material attached to the latest exact author message. */
export interface AssistantReferenceContext {
  source_kind: 'long_text' | 'attachment' | 'routed_data'
  source_name: string
  content: string
  coverage: 'full' | 'distributed' | 'excerpt'
  source_chars: number
  content_sha256?: string | null
}
