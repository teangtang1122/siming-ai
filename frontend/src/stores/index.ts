import { create } from 'zustand'

interface AppUiState {
  error: string | null
  setError: (error: string | null) => void
}

/**
 * Cross-page client state only. Server data belongs to TanStack Query feature caches.
 */
export const useAppStore = create<AppUiState>((set) => ({
  error: null,
  setError: (error) => set({ error }),
}))
