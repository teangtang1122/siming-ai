import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../shared/api/client'
import type { ApiEnvelope } from '../../shared/api/contracts'
import { applyDesktopPetPreferences, hasDesktopPetBridge } from './nativeBridge'
import { DEFAULT_DESKTOP_PET_SETTINGS, type DesktopPetSettings, type DesktopPetSettingsPatch } from './types'
import type { DesktopPetResizeAnchor } from './behavior'

const SETTINGS_QUERY_KEY = ['launcher-settings', 'desktop-pet'] as const
export const PET_PREFERENCE_PREVIEW_MS = 16
const signature = (settings: DesktopPetSettings) => JSON.stringify([
  settings.desktop_pet_scale, settings.desktop_pet_opacity,
  settings.desktop_pet_muted, settings.desktop_pet_on_top,
])

export function useDesktopPetSettings(onError: (message: string) => void) {
  const queryClient = useQueryClient()
  const [settings, setSettings] = useState(DEFAULT_DESKTOP_PET_SETTINGS)
  const [saving, setSaving] = useState(false)
  const current = useRef(settings)
  const saved = useRef(settings)
  const dirty = useRef<DesktopPetSettingsPatch>({})
  const pendingSave = useRef<DesktopPetSettingsPatch>({})
  const saveBusy = useRef(false)
  const saveVersion = useRef(0)
  const resizeAnchor = useRef<DesktopPetResizeAnchor | null>(null)
  const mounted = useRef(false)
  const error = useRef(onError)
  error.current = onError
  const queuePreview = useRef<(next: DesktopPetSettings) => void>(() => {})
  const publish = useCallback((next: DesktopPetSettings) => {
    current.current = next
    if (mounted.current) setSettings(next)
  }, [])

  useEffect(() => {
    mounted.current = true
    let cancelled = false, busy = false, timer = 0, lastStart = -Infinity, applied = ''
    let pending: DesktopPetSettings | null = null
    // One native request at a time; replace intermediate slider values instead
    // of queueing a resize for every input event. Disk I/O is not on this path.
    const schedule = () => {
      if (cancelled || busy || timer || !pending) return
      timer = window.setTimeout(() => { void flush() }, Math.max(0,
        PET_PREFERENCE_PREVIEW_MS - (performance.now() - lastStart)))
    }
    const flush = async () => {
      timer = 0
      if (cancelled || !pending || busy) return
      const next = pending
      pending = null
      const anchor = resizeAnchor.current
      const key = JSON.stringify([signature(next), anchor])
      if (key === applied || !hasDesktopPetBridge()) return
      busy = true
      lastStart = performance.now()
      try {
        // This page already owns the draft. Only changes from the main settings
        // window need a native event back to it; self echoes can arrive stale.
        if (!await applyDesktopPetPreferences(next, false, anchor)) throw new Error('桌宠设置预览未能同步，请重试。')
        applied = key
      } catch (reason) {
        if (!cancelled) error.current(reason instanceof Error ? reason.message : '桌宠设置预览失败')
      } finally {
        busy = false
        schedule()
      }
    }
    queuePreview.current = next => { pending = next; schedule() }
    const version = saveVersion.current
    void apiClient.get<ApiEnvelope<DesktopPetSettings>>('/config/launcher').then(response => {
      if (cancelled || version !== saveVersion.current) return
      saved.current = response.data.data
      publish({ ...saved.current, ...dirty.current })
      queryClient.setQueryData(SETTINGS_QUERY_KEY, saved.current)
    }).catch(() => { if (!cancelled) error.current('设置暂时无法同步') })
    const preferences = (event: Event) => {
      const detail = (event as CustomEvent<DesktopPetSettingsPatch>).detail
      // Changes saved by the main settings window share this contract, but
      // must not overwrite a gesture that is still in progress here.
      if (detail) {
        saved.current = { ...saved.current, ...detail }
        publish({ ...current.current, ...detail, ...dirty.current })
      }
    }
    window.addEventListener('siming:desktop-pet-preferences', preferences)
    return () => {
      cancelled = true; mounted.current = false
      window.clearTimeout(timer)
      queuePreview.current = () => {}
      window.removeEventListener('siming:desktop-pet-preferences', preferences)
    }
  }, [publish, queryClient])

  const previewSettings = useCallback((patch: DesktopPetSettingsPatch, anchor: DesktopPetResizeAnchor | null = null) => {
    resizeAnchor.current = anchor
    dirty.current = { ...dirty.current, ...patch }
    const next = { ...current.current, ...patch }
    publish(next)
    queuePreview.current(next)
  }, [publish])

  const persistSettings = useCallback(async (patch: DesktopPetSettingsPatch, anchor: DesktopPetResizeAnchor | null = null) => {
    previewSettings(patch, anchor)
    pendingSave.current = { ...pendingSave.current, ...patch }
    if (saveBusy.current) return
    saveBusy.current = true
    setSaving(true)
    error.current('')
    try {
      while (mounted.current && Object.keys(pendingSave.current).length) {
        const changes = Object.fromEntries(Object.entries(pendingSave.current)
          .filter(([key, value]) => saved.current[key as keyof DesktopPetSettings] !== value)) as DesktopPetSettingsPatch
        pendingSave.current = {}
        if (!Object.keys(changes).length) {
          for (const key of Object.keys(dirty.current) as (keyof DesktopPetSettingsPatch)[]) {
            if (dirty.current[key] === saved.current[key]) delete dirty.current[key]
          }
          break
        }
        saveVersion.current++
        try {
          const response = await apiClient.put<ApiEnvelope<DesktopPetSettings>>('/config/launcher', changes)
          saved.current = response.data.data
          queryClient.setQueryData(SETTINGS_QUERY_KEY, saved.current)
        } catch (reason) {
          if (mounted.current) error.current(reason instanceof Error ? reason.message : '设置保存失败')
        }
        if (!mounted.current) break
        for (const key of Object.keys(changes) as (keyof DesktopPetSettingsPatch)[]) {
          if (dirty.current[key] === changes[key]) delete dirty.current[key]
        }
        // Rebase newer edits over the saved state (or roll back only the failed
        // edit). An older HTTP response must not resize a newer preview.
        const next = { ...saved.current, ...dirty.current }
        publish(next)
        queuePreview.current(next)
      }
    } finally {
      saveBusy.current = false
      if (mounted.current) setSaving(false)
    }
  }, [previewSettings, publish, queryClient])

  return { settings, saving, previewSettings, persistSettings }
}
