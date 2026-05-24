import { createContext, useCallback, useContext, useMemo, useState } from 'react'

interface AiPanelContextValue {
  selectedOutlineNodeId: string | null
  selectedCharacterId: string | null
  selectedText: string
  selectedTextChapterId: string | null
  setAiContext: (partial: Partial<Omit<AiPanelContextValue, 'setAiContext' | 'refreshKey' | 'triggerRefresh'>>) => void
  refreshKey: number
  triggerRefresh: () => void
}

const AiPanelContext = createContext<AiPanelContextValue>({
  selectedOutlineNodeId: null,
  selectedCharacterId: null,
  selectedText: '',
  selectedTextChapterId: null,
  setAiContext: () => {},
  refreshKey: 0,
  triggerRefresh: () => {},
})

export function AiPanelProvider({ children }: { children: React.ReactNode }) {
  const [context, setContext] = useState<Omit<AiPanelContextValue, 'setAiContext' | 'refreshKey' | 'triggerRefresh'>>({
    selectedOutlineNodeId: null,
    selectedCharacterId: null,
    selectedText: '',
    selectedTextChapterId: null,
  })
  const [refreshKey, setRefreshKey] = useState(0)

  const setAiContext = useCallback(
    (partial: Partial<Omit<AiPanelContextValue, 'setAiContext' | 'refreshKey' | 'triggerRefresh'>>) => {
      setContext((prev) => ({ ...prev, ...partial }))
    },
    [],
  )

  const triggerRefresh = useCallback(() => {
    setRefreshKey((key) => key + 1)
  }, [])

  const value = useMemo<AiPanelContextValue>(
    () => ({ ...context, setAiContext, refreshKey, triggerRefresh }),
    [context, setAiContext, refreshKey, triggerRefresh],
  )

  return <AiPanelContext.Provider value={value}>{children}</AiPanelContext.Provider>
}

export function useAiPanelContext() {
  return useContext(AiPanelContext)
}

export default AiPanelContext
