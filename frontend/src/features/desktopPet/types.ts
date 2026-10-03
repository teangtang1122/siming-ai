export interface DesktopPetSettings {
  desktop_pet_enabled: boolean
  desktop_pet_scale: number
  desktop_pet_opacity: number
  desktop_pet_muted: boolean
  desktop_pet_on_top: boolean
  desktop_pet_supported: boolean
  desktop_pet_runtime_active: boolean
}

export type DesktopPetSettingsPatch = Partial<Pick<
  DesktopPetSettings,
  | 'desktop_pet_enabled'
  | 'desktop_pet_scale'
  | 'desktop_pet_opacity'
  | 'desktop_pet_muted'
  | 'desktop_pet_on_top'
>>

export const DEFAULT_DESKTOP_PET_SETTINGS: DesktopPetSettings = {
  desktop_pet_enabled: true,
  desktop_pet_scale: 0.8,
  desktop_pet_opacity: 0.96,
  desktop_pet_muted: true,
  desktop_pet_on_top: true,
  desktop_pet_supported: true,
  desktop_pet_runtime_active: false,
}
