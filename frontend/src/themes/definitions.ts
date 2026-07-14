import type { ThemeConfig } from 'antd'

export interface ThemeDefinition {
  key: string
  name: string
  description: string
  config: ThemeConfig
  /** CSS accent color for decorative use (borders, gradients) */
  accent: string
  /** Subtle background pattern opacity */
  grainOpacity: number
}

const FONT_BODY = "'Noto Sans SC', 'Source Han Sans SC', 'Microsoft YaHei', sans-serif"
const FONT_DISPLAY = "'Noto Serif SC', 'Source Han Serif SC', 'Songti SC', serif"

/** 文房 — 纸白、石墨、朱砂，安静而清晰 */
const wenfang: ThemeDefinition = {
  key: 'wenfang',
  name: '文房',
  description: '纸白朱砂，安静清晰',
  accent: '#963a36',
  grainOpacity: 0.008,
  config: {
    token: {
      colorPrimary: '#963a36',
      colorSuccess: '#39735d',
      colorWarning: '#a66a16',
      colorError: '#b33a36',
      colorInfo: '#315f75',
      colorBgLayout: '#f4f4f1',
      colorBgContainer: '#ffffff',
      colorBgElevated: '#ffffff',
      colorText: '#20201f',
      colorTextSecondary: '#62615e',
      colorTextTertiary: '#8a8883',
      colorTextQuaternary: '#b7b5af',
      colorBorder: '#d8d6d0',
      colorBorderSecondary: '#e7e6e1',
      borderRadius: 6,
      borderRadiusLG: 8,
      boxShadow: '0 1px 3px rgba(24, 24, 22, 0.05)',
      boxShadowSecondary: '0 10px 30px rgba(24, 24, 22, 0.10)',
      fontFamily: FONT_BODY,
    },
    components: {
      Layout: { bodyBg: '#f4f4f1', siderBg: '#ffffff', headerBg: '#fafaf8' },
      Menu: {
        itemBg: 'transparent',
        subMenuItemBg: 'transparent',
        itemSelectedBg: 'rgba(150, 58, 54, 0.09)',
        itemSelectedColor: '#963a36',
        itemHoverBg: 'rgba(32, 32, 31, 0.04)',
        itemActiveBg: 'rgba(150, 58, 54, 0.13)',
        itemBorderRadius: 6,
        itemMarginInline: 8,
        itemPaddingInline: 12,
      },
      Card: { colorBgContainer: '#ffffff', boxShadow: 'none' },
      Table: { colorBgContainer: '#ffffff', headerBg: '#f6f6f3', rowHoverBg: '#fafaf8' },
      Input: { colorBgContainer: '#ffffff', activeShadow: '0 0 0 2px rgba(150, 58, 54, 0.12)' },
      Select: { colorBgContainer: '#ffffff' },
      Modal: { contentBg: '#ffffff', titleFontSize: 17 },
      Tabs: { inkBarColor: '#963a36', itemSelectedColor: '#963a36', itemHoverColor: '#7d302d' },
      Tag: { defaultBg: '#f4f4f1', defaultColor: '#52514e' },
      Button: { primaryShadow: '0 2px 6px rgba(150, 58, 54, 0.18)' },
    },
  },
}

/** 墨白 — 清爽、高对比度、书卷气 */
const mobai: ThemeDefinition = {
  key: 'mobai',
  name: '墨白',
  description: '清爽素雅，黑白分明',
  accent: '#2a2a2a',
  grainOpacity: 0.012,
  config: {
    token: {
      colorPrimary: '#2a2a2a',
      colorSuccess: '#52c41a',
      colorWarning: '#faad14',
      colorError: '#f5222d',
      colorInfo: '#2a2a2a',
      colorBgLayout: '#f7f7f7',
      colorBgContainer: '#ffffff',
      colorBgElevated: '#ffffff',
      colorText: '#111111',
      colorTextSecondary: '#555555',
      colorTextTertiary: '#8c8c8c',
      colorTextQuaternary: '#bfbfbf',
      colorBorder: '#d9d9d9',
      colorBorderSecondary: '#f0f0f0',
      borderRadius: 4,
      borderRadiusLG: 6,
      boxShadow: '0 1px 3px rgba(0, 0, 0, 0.06), 0 1px 2px rgba(0, 0, 0, 0.03)',
      boxShadowSecondary: '0 4px 16px rgba(0, 0, 0, 0.08), 0 2px 6px rgba(0, 0, 0, 0.03)',
      fontFamily: FONT_BODY,
    },
    components: {
      Layout: { bodyBg: '#f7f7f7', siderBg: '#ffffff', headerBg: '#fafafa' },
      Menu: {
        itemBg: 'transparent',
        subMenuItemBg: 'transparent',
        itemSelectedBg: '#f0f0f0',
        itemSelectedColor: '#111111',
        itemHoverBg: '#f5f5f5',
        itemActiveBg: '#e8e8e8',
        itemBorderRadius: 4,
        itemMarginInline: 8,
        itemPaddingInline: 12,
      },
      Card: { colorBgContainer: '#ffffff', boxShadow: '0 1px 3px rgba(0, 0, 0, 0.05)' },
      Table: { colorBgContainer: '#ffffff', headerBg: '#fafafa', rowHoverBg: '#f5f5f5' },
      Input: { colorBgContainer: '#ffffff', activeShadow: '0 0 0 2px rgba(42, 42, 42, 0.1)' },
      Select: { colorBgContainer: '#ffffff' },
      Modal: { contentBg: '#ffffff', titleFontSize: 17 },
      Tabs: { inkBarColor: '#111111', itemSelectedColor: '#111111', itemHoverColor: '#2a2a2a' },
      Tag: { defaultBg: '#f5f5f5', defaultColor: '#2a2a2a' },
      Button: { primaryShadow: '0 2px 4px rgba(0, 0, 0, 0.15)' },
    },
  },
}

/** 青竹 — 清新、自然、生机 */
const qingzhu: ThemeDefinition = {
  key: 'qingzhu',
  name: '青竹',
  description: '清新自然，竹韵悠然',
  accent: '#2d6b4e',
  grainOpacity: 0.014,
  config: {
    token: {
      colorPrimary: '#2d6b4e',
      colorSuccess: '#52c41a',
      colorWarning: '#d4a843',
      colorError: '#c44a3f',
      colorInfo: '#2d6b4e',
      colorBgLayout: '#f0f6f2',
      colorBgContainer: '#f8fbf9',
      colorBgElevated: '#f8fbf9',
      colorText: '#1a2e23',
      colorTextSecondary: '#5a7d6a',
      colorTextTertiary: '#8aa496',
      colorTextQuaternary: '#b8cfc3',
      colorBorder: '#c0d8ca',
      colorBorderSecondary: '#d8ece2',
      borderRadius: 8,
      borderRadiusLG: 10,
      boxShadow: '0 1px 3px rgba(45, 107, 78, 0.06), 0 1px 2px rgba(45, 107, 78, 0.04)',
      boxShadowSecondary: '0 4px 16px rgba(45, 107, 78, 0.08), 0 2px 6px rgba(45, 107, 78, 0.04)',
      fontFamily: FONT_BODY,
    },
    components: {
      Layout: { bodyBg: '#f0f6f2', siderBg: '#f8fbf9', headerBg: '#eaf3ed' },
      Menu: {
        itemBg: 'transparent',
        subMenuItemBg: 'transparent',
        itemSelectedBg: 'rgba(45, 107, 78, 0.08)',
        itemSelectedColor: '#2d6b4e',
        itemHoverBg: 'rgba(45, 107, 78, 0.04)',
        itemActiveBg: 'rgba(45, 107, 78, 0.12)',
        itemBorderRadius: 8,
        itemMarginInline: 8,
        itemPaddingInline: 12,
      },
      Card: { colorBgContainer: '#f8fbf9', boxShadow: '0 1px 3px rgba(45, 107, 78, 0.06)' },
      Table: { colorBgContainer: '#f8fbf9', headerBg: '#eaf3ed', rowHoverBg: '#e8f2ec' },
      Input: { colorBgContainer: '#f8fbf9', activeShadow: '0 0 0 2px rgba(45, 107, 78, 0.12)' },
      Select: { colorBgContainer: '#f8fbf9' },
      Modal: { contentBg: '#f8fbf9', titleFontSize: 17 },
      Tabs: { inkBarColor: '#2d6b4e', itemSelectedColor: '#2d6b4e', itemHoverColor: '#4a9e7a' },
      Tag: { defaultBg: '#e8f2ec', defaultColor: '#2d6b4e' },
      Button: { primaryShadow: '0 2px 4px rgba(45, 107, 78, 0.2)' },
    },
  },
}

/** 夜读 — 石墨暗色、护眼、沉浸 */
const yedu: ThemeDefinition = {
  key: 'yedu',
  name: '夜读',
  description: '暗色沉浸，深夜护眼',
  accent: '#d77b72',
  grainOpacity: 0.012,
  config: {
    algorithm: undefined, // Will be set to darkAlgorithm at runtime
    token: {
      colorPrimary: '#d77b72',
      colorSuccess: '#65a98a',
      colorWarning: '#d4a45f',
      colorError: '#e06d67',
      colorInfo: '#75a9c2',
      colorBgLayout: '#131517',
      colorBgContainer: '#1c1f21',
      colorBgElevated: '#25292c',
      colorText: '#ecebe8',
      colorTextSecondary: '#b2b1ad',
      colorTextTertiary: '#858580',
      colorTextQuaternary: '#555752',
      colorBorder: '#3b3e40',
      colorBorderSecondary: '#2b2e30',
      borderRadius: 6,
      borderRadiusLG: 8,
      boxShadow: '0 1px 3px rgba(0, 0, 0, 0.2), 0 1px 2px rgba(0, 0, 0, 0.12)',
      boxShadowSecondary: '0 4px 16px rgba(0, 0, 0, 0.25), 0 2px 6px rgba(0, 0, 0, 0.15)',
      fontFamily: FONT_BODY,
    },
    components: {
      Layout: { bodyBg: '#131517', siderBg: '#1c1f21', headerBg: '#181b1d' },
      Menu: {
        itemBg: 'transparent',
        subMenuItemBg: 'transparent',
        itemSelectedBg: 'rgba(215, 123, 114, 0.14)',
        itemSelectedColor: '#d77b72',
        itemHoverBg: 'rgba(255, 255, 255, 0.05)',
        itemActiveBg: 'rgba(215, 123, 114, 0.2)',
        itemBorderRadius: 6,
        itemMarginInline: 8,
        itemPaddingInline: 12,
      },
      Card: { colorBgContainer: '#1c1f21', boxShadow: '0 1px 3px rgba(0, 0, 0, 0.2)' },
      Table: { colorBgContainer: '#1c1f21', headerBg: '#25292c', rowHoverBg: '#2b2e30' },
      Input: { colorBgContainer: '#25292c', activeShadow: '0 0 0 2px rgba(215, 123, 114, 0.18)' },
      Select: { colorBgContainer: '#25292c' },
      Modal: { contentBg: '#1c1f21', titleFontSize: 17 },
      Tabs: { inkBarColor: '#d77b72', itemSelectedColor: '#d77b72', itemHoverColor: '#e3938b' },
      Tag: { defaultBg: '#2b2e30', defaultColor: '#d9d8d4' },
      Button: { primaryShadow: '0 2px 6px rgba(215, 123, 114, 0.18)' },
    },
  },
}

export const THEMES: ThemeDefinition[] = [wenfang, mobai, qingzhu, yedu]

export const DEFAULT_THEME_KEY = 'wenfang'

export { FONT_BODY, FONT_DISPLAY }

export function getThemeByKey(key: string): ThemeDefinition {
  return THEMES.find((t) => t.key === key) || wenfang
}
