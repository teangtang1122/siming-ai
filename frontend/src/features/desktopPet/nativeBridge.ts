import type { DesktopPetSettings } from './types'
import type { DesktopPetSide, DesktopPetWindowContext, DesktopPetResizeAnchor } from './behavior'
import type { PeekLayout } from './poses/placement'
import type { NativeHitRegion } from './hitRegion'

interface CompactWindowRequest {
  width: number
  height: number
  viewport_width: number
  viewport_height: number
}

interface DesktopPetNativeApi {
  show_main_window?: (route?: string) => Promise<boolean>
  show_desktop_pet?: () => Promise<boolean>
  hide_desktop_pet?: () => Promise<boolean>
  begin_desktop_pet_drag?: (screenX: number, screenY: number) => Promise<boolean>
  move_desktop_pet_drag?: (screenX: number, screenY: number) => Promise<boolean>
  end_desktop_pet_drag?: () => Promise<boolean>
  get_desktop_pet_window_context?: () => Promise<DesktopPetWindowContext | null>
  move_desktop_pet_to_edge?: (side: DesktopPetSide, compact: CompactWindowRequest) => Promise<DesktopPetWindowContext | null>
  cancel_desktop_pet_edge_move?: () => Promise<boolean>
  update_desktop_pet_hit_region?: (region: NativeHitRegion) => Promise<boolean>
  apply_desktop_pet_preferences?: (
    preferences: Pick<
      DesktopPetSettings,
      'desktop_pet_scale' | 'desktop_pet_opacity' | 'desktop_pet_muted' | 'desktop_pet_on_top'
    >,
    notifyPet: boolean,
    resizeAnchor: DesktopPetResizeAnchor | null,
  ) => Promise<boolean>
}

declare global {
  interface Window {
    pywebview?: { api?: DesktopPetNativeApi }
  }
}

function nativeApi() {
  return window.pywebview?.api
}

function safeLocalRoute(route?: string) {
  if (!route || !route.startsWith('/') || route.startsWith('//')) return '/gui'
  return route
}

export function hasDesktopPetBridge() {
  return Boolean(nativeApi()?.show_desktop_pet)
}

export async function showMainWindow(route?: string) {
  const target = safeLocalRoute(route)
  const api = nativeApi()
  if (api?.show_main_window) return api.show_main_window(target)
  window.location.assign(target)
  return true
}

export async function showDesktopPet() {
  return nativeApi()?.show_desktop_pet?.() ?? false
}

export async function hideDesktopPet() {
  return nativeApi()?.hide_desktop_pet?.() ?? false
}

export async function beginDesktopPetDrag(screenX: number, screenY: number) {
  return nativeApi()?.begin_desktop_pet_drag?.(screenX, screenY) ?? false
}

export async function moveDesktopPetDrag(screenX: number, screenY: number) {
  return nativeApi()?.move_desktop_pet_drag?.(screenX, screenY) ?? false
}

export async function endDesktopPetDrag() {
  return nativeApi()?.end_desktop_pet_drag?.() ?? false
}

export async function moveDesktopPetToEdge(side: DesktopPetSide, layout: PeekLayout) {
  const context = await nativeApi()?.move_desktop_pet_to_edge?.(side, {
    width: layout.width, height: layout.height,
    viewport_width: layout.normalWidth, viewport_height: layout.normalHeight,
  })
  // Never hide against a browser window or pretend a failed native move worked.
  return validWindowContext(context) ? context : null
}

export async function cancelDesktopPetEdgeMove() {
  return nativeApi()?.cancel_desktop_pet_edge_move?.() ?? false
}

export async function updateDesktopPetHitRegion(region: NativeHitRegion) {
  return nativeApi()?.update_desktop_pet_hit_region?.(region) ?? false
}

function browserWindowContext(): DesktopPetWindowContext {
  const desktopScreen = window.screen as Screen & {
    availLeft?: number
    availTop?: number
  }
  return {
    x: Number.isFinite(window.screenX) ? window.screenX : 0,
    y: Number.isFinite(window.screenY) ? window.screenY : 0,
    width: window.outerWidth || window.innerWidth,
    height: window.outerHeight || window.innerHeight,
    work_x: desktopScreen.availLeft || 0,
    work_y: desktopScreen.availTop || 0,
    work_width: desktopScreen.availWidth || desktopScreen.width,
    work_height: desktopScreen.availHeight || desktopScreen.height,
  }
}

function validWindowContext(
  value: DesktopPetWindowContext | null | undefined,
): value is DesktopPetWindowContext {
  return Boolean(value && [
    value.x,
    value.y,
    value.width,
    value.height,
    value.work_x,
    value.work_y,
    value.work_width,
    value.work_height,
  ].every(Number.isFinite) && value.width > 0 && value.height > 0
    && value.work_width > 0 && value.work_height > 0)
}

export async function getDesktopPetWindowContext() {
  try {
    const value = await nativeApi()?.get_desktop_pet_window_context?.()
    if (validWindowContext(value)) return value
  } catch {
    // Browser metrics keep the layout usable while the native overlay is starting.
  }
  return browserWindowContext()
}

export async function applyDesktopPetPreferences(settings: DesktopPetSettings, notifyPet = true,
  resizeAnchor: DesktopPetResizeAnchor | null = null) {
  return nativeApi()?.apply_desktop_pet_preferences?.({
    desktop_pet_scale: settings.desktop_pet_scale,
    desktop_pet_opacity: settings.desktop_pet_opacity,
    desktop_pet_muted: settings.desktop_pet_muted,
    desktop_pet_on_top: settings.desktop_pet_on_top,
  }, notifyPet, resizeAnchor) ?? false
}

export {}
