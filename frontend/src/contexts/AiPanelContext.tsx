import { createContext, useCallback, useContext, useMemo, useState } from 'react'

interface AiSelectionState {
  selectedText: string
  selectedTextChapterId: string | null
}

export interface GeneratedChapterDraft {
  draftId: string
  projectId: string
  title: string
  outlineNodeId: string | null
  contextManifestId: string | null
  savedChapterId: string | null
  draftKind: 'new' | 'revision'
  targetChapterId: string | null
  baseChapterVersion: number | null
  content: string
  wordCount: number
  status: 'pending' | 'saved' | 'superseded'
}

export interface GeneratedOutlineDraftNode {
  id?: string
  title: string
  node_type: 'volume' | 'chapter' | 'section'
  summary?: string
  parent_title?: string | null
  actual_summary?: string | null
  planned_summary?: string | null
  character_names?: string[]
  status: 'pending'
  metadata?: Record<string, unknown> | null
}

export interface GeneratedOutlineDraft {
  draftId: string
  projectId: string
  contextManifestId: string | null
  parentId: string | null
  insertAfterId: string | null
  status: 'pending' | 'confirmed' | 'discarded' | 'superseded'
  nodes: GeneratedOutlineDraftNode[]
  designNotes: string
  savedOutlineNodeIds: string[]
}

export interface PendingAuthorAgentRequest {
  projectId: string
  message: string
}

interface AiPanelContextValue extends AiSelectionState {
  setAiContext: (partial: Partial<AiSelectionState>) => void
  generatedDraft: GeneratedChapterDraft | null
  openGeneratedDraft: (draft: GeneratedChapterDraft) => void
  updateGeneratedDraft: (partial: Partial<GeneratedChapterDraft>) => void
  clearGeneratedDraft: () => void
  generatedOutlineDraft: GeneratedOutlineDraft | null
  openGeneratedOutlineDraft: (draft: GeneratedOutlineDraft) => void
  updateGeneratedOutlineDraft: (partial: Partial<GeneratedOutlineDraft>) => void
  clearGeneratedOutlineDraft: () => void
  pendingAuthorAgentRequest: PendingAuthorAgentRequest | null
  requestAuthorAgentTurn: (request: PendingAuthorAgentRequest) => void
  consumeAuthorAgentTurn: () => void
  refreshKey: number
  triggerRefresh: () => void
}

const AiPanelContext = createContext<AiPanelContextValue>({
  selectedText: '',
  selectedTextChapterId: null,
  setAiContext: () => {},
  generatedDraft: null,
  openGeneratedDraft: () => {},
  updateGeneratedDraft: () => {},
  clearGeneratedDraft: () => {},
  generatedOutlineDraft: null,
  openGeneratedOutlineDraft: () => {},
  updateGeneratedOutlineDraft: () => {},
  clearGeneratedOutlineDraft: () => {},
  pendingAuthorAgentRequest: null,
  requestAuthorAgentTurn: () => {},
  consumeAuthorAgentTurn: () => {},
  refreshKey: 0,
  triggerRefresh: () => {},
})

export function AiPanelProvider({ children }: { children: React.ReactNode }) {
  const [context, setContext] = useState<AiSelectionState>({
    selectedText: '',
    selectedTextChapterId: null,
  })
  const [generatedDraft, setGeneratedDraft] = useState<GeneratedChapterDraft | null>(null)
  const [generatedOutlineDraft, setGeneratedOutlineDraft] = useState<GeneratedOutlineDraft | null>(null)
  const [pendingAuthorAgentRequest, setPendingAuthorAgentRequest] = useState<PendingAuthorAgentRequest | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const setAiContext = useCallback(
    (partial: Partial<AiSelectionState>) => {
      setContext((prev) => ({ ...prev, ...partial }))
    },
    [],
  )

  const openGeneratedDraft = useCallback((draft: GeneratedChapterDraft) => {
    setGeneratedDraft(draft)
  }, [])

  const updateGeneratedDraft = useCallback((partial: Partial<GeneratedChapterDraft>) => {
    setGeneratedDraft((current) => current ? { ...current, ...partial } : current)
  }, [])

  const clearGeneratedDraft = useCallback(() => setGeneratedDraft(null), [])

  const openGeneratedOutlineDraft = useCallback((draft: GeneratedOutlineDraft) => {
    setGeneratedOutlineDraft(draft)
  }, [])

  const updateGeneratedOutlineDraft = useCallback((partial: Partial<GeneratedOutlineDraft>) => {
    setGeneratedOutlineDraft((current) => current ? { ...current, ...partial } : current)
  }, [])

  const clearGeneratedOutlineDraft = useCallback(() => setGeneratedOutlineDraft(null), [])
  const requestAuthorAgentTurn = useCallback((request: PendingAuthorAgentRequest) => {
    setPendingAuthorAgentRequest(request)
  }, [])
  const consumeAuthorAgentTurn = useCallback(() => setPendingAuthorAgentRequest(null), [])

  const triggerRefresh = useCallback(() => {
    setRefreshKey((key) => key + 1)
  }, [])

  const value = useMemo<AiPanelContextValue>(
    () => ({
      ...context,
      setAiContext,
      generatedDraft,
      openGeneratedDraft,
      updateGeneratedDraft,
      clearGeneratedDraft,
      generatedOutlineDraft,
      openGeneratedOutlineDraft,
      updateGeneratedOutlineDraft,
      clearGeneratedOutlineDraft,
      pendingAuthorAgentRequest,
      requestAuthorAgentTurn,
      consumeAuthorAgentTurn,
      refreshKey,
      triggerRefresh,
    }),
    [
      context,
      setAiContext,
      generatedDraft,
      openGeneratedDraft,
      updateGeneratedDraft,
      clearGeneratedDraft,
      generatedOutlineDraft,
      openGeneratedOutlineDraft,
      updateGeneratedOutlineDraft,
      clearGeneratedOutlineDraft,
      pendingAuthorAgentRequest,
      requestAuthorAgentTurn,
      consumeAuthorAgentTurn,
      refreshKey,
      triggerRefresh,
    ],
  )

  return <AiPanelContext.Provider value={value}>{children}</AiPanelContext.Provider>
}

export function useAiPanelContext() {
  return useContext(AiPanelContext)
}

export default AiPanelContext
