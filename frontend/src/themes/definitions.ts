import type { ThemeConfig } from 'antd'

export interface ThemeDefinition {
  key: string
  name: string
  description: string
  /** Icon emoji for the theme switcher */
  icon: string
  config: ThemeConfig
}

const FONT_BODY = "'LXGW WenKai', 'Noto Serif SC', 'PingFang SC', 'Microsoft YaHei', sans-serif"
const FONT_DISPLAY = "'Noto Serif SC', 'LXGW WenKai', 'PingFang SC', serif"

/** 文房 — 温润、沉静、文学感 */
const wenfang: ThemeDefinition = {
  key: 'wenfang',
  name: '文房',
  description: '温润沉静，文学气息',
  icon: '📜',
  config: {
    token: {
      colorPrimary: '#7c5e2a',
      colorSuccess: '#5a8a3c',
      colorWarning: '#c48816',
      colorError: '#b84233',
      colorInfo: '#7c5e2a',
      colorBgLayout: '#f8f5ef',
      colorBgContainer: '#fffcf7',
      colorBgElevated: '#fffcf7',
      colorText: '#2c2417',
      colorTextSecondary: '#8a7e6b',
      colorTextTertiary: '#b0a591',
      colorTextQuaternary: '#d4ccbe',
      colorBorder: '#e8e2d6',
      colorBorderSecondary: '#ece7dc',
      borderRadius: 6,
      fontFamily: FONT_BODY,
    },
    components: {
      Layout: { bodyBg: '#f8f5ef', siderBg: '#fffcf7', headerBg: '#f5f1e9' },
      Menu: { itemBg: '#fffcf7', subMenuItemBg: '#fffcf7', itemSelectedBg: '#faf6ee', itemSelectedColor: '#7c5e2a', itemHoverBg: '#f5f1e9' },
      Card: { colorBgContainer: '#fffcf7' },
      Table: { colorBgContainer: '#fffcf7', headerBg: '#f5f1e9', rowHoverBg: '#faf6ee' },
      Input: { colorBgContainer: '#fffcf7' },
      Select: { colorBgContainer: '#fffcf7' },
      Modal: { contentBg: '#fffcf7' },
      Tabs: { inkBarColor: '#7c5e2a', itemSelectedColor: '#7c5e2a', itemHoverColor: '#9a7d3e' },
      Tag: { defaultBg: '#faf6ee', defaultColor: '#7c5e2a' },
    },
  },
}

/** 墨白 — 清爽、高对比度、书卷气 */
const mobai: ThemeDefinition = {
  key: 'mobai',
  name: '墨白',
  description: '清爽素雅，黑白分明',
  icon: '🖋️',
  config: {
    token: {
      colorPrimary: '#434343',
      colorSuccess: '#52c41a',
      colorWarning: '#faad14',
      colorError: '#f5222d',
      colorInfo: '#434343',
      colorBgLayout: '#f5f5f5',
      colorBgContainer: '#ffffff',
      colorBgElevated: '#ffffff',
      colorText: '#141414',
      colorTextSecondary: '#595959',
      colorTextTertiary: '#8c8c8c',
      colorTextQuaternary: '#bfbfbf',
      colorBorder: '#d9d9d9',
      colorBorderSecondary: '#f0f0f0',
      borderRadius: 4,
      fontFamily: FONT_BODY,
    },
    components: {
      Layout: { bodyBg: '#f5f5f5', siderBg: '#ffffff', headerBg: '#fafafa' },
      Menu: { itemBg: '#ffffff', subMenuItemBg: '#ffffff', itemSelectedBg: '#f5f5f5', itemSelectedColor: '#141414', itemHoverBg: '#fafafa' },
      Card: { colorBgContainer: '#ffffff' },
      Table: { colorBgContainer: '#ffffff', headerBg: '#fafafa', rowHoverBg: '#f5f5f5' },
      Input: { colorBgContainer: '#ffffff' },
      Select: { colorBgContainer: '#ffffff' },
      Modal: { contentBg: '#ffffff' },
      Tabs: { inkBarColor: '#141414', itemSelectedColor: '#141414', itemHoverColor: '#434343' },
      Tag: { defaultBg: '#f5f5f5', defaultColor: '#434343' },
    },
  },
}

/** 青竹 — 清新、自然、生机 */
const qingzhu: ThemeDefinition = {
  key: 'qingzhu',
  name: '青竹',
  description: '清新自然，竹韵悠然',
  icon: '🎋',
  config: {
    token: {
      colorPrimary: '#3d7a5c',
      colorSuccess: '#52c41a',
      colorWarning: '#d4a843',
      colorError: '#c44a3f',
      colorInfo: '#3d7a5c',
      colorBgLayout: '#f2f7f4',
      colorBgContainer: '#f9fcfa',
      colorBgElevated: '#f9fcfa',
      colorText: '#1a2e23',
      colorTextSecondary: '#6b8a7a',
      colorTextTertiary: '#94aa9e',
      colorTextQuaternary: '#c2d4ca',
      colorBorder: '#c8ddd0',
      colorBorderSecondary: '#ddeee5',
      borderRadius: 8,
      fontFamily: FONT_BODY,
    },
    components: {
      Layout: { bodyBg: '#f2f7f4', siderBg: '#f9fcfa', headerBg: '#edf5f0' },
      Menu: { itemBg: '#f9fcfa', subMenuItemBg: '#f9fcfa', itemSelectedBg: '#e8f2ec', itemSelectedColor: '#3d7a5c', itemHoverBg: '#edf5f0' },
      Card: { colorBgContainer: '#f9fcfa' },
      Table: { colorBgContainer: '#f9fcfa', headerBg: '#edf5f0', rowHoverBg: '#e8f2ec' },
      Input: { colorBgContainer: '#f9fcfa' },
      Select: { colorBgContainer: '#f9fcfa' },
      Modal: { contentBg: '#f9fcfa' },
      Tabs: { inkBarColor: '#3d7a5c', itemSelectedColor: '#3d7a5c', itemHoverColor: '#5a9e7a' },
      Tag: { defaultBg: '#e8f2ec', defaultColor: '#3d7a5c' },
    },
  },
}

/** 夜读 — 暗色、护眼、沉浸 */
const yedu: ThemeDefinition = {
  key: 'yedu',
  name: '夜读',
  description: '暗色沉浸，深夜护眼',
  icon: '🌙',
  config: {
    algorithm: undefined, // Will be set to darkAlgorithm at runtime
    token: {
      colorPrimary: '#d4a843',
      colorSuccess: '#6abf4b',
      colorWarning: '#e8b84d',
      colorError: '#d4534d',
      colorInfo: '#d4a843',
      colorBgLayout: '#1a1714',
      colorBgContainer: '#242019',
      colorBgElevated: '#2c2720',
      colorText: '#e8dfd0',
      colorTextSecondary: '#a89a85',
      colorTextTertiary: '#7a6e5d',
      colorTextQuaternary: '#5a5045',
      colorBorder: '#3d362c',
      colorBorderSecondary: '#332d24',
      borderRadius: 6,
      fontFamily: FONT_BODY,
    },
    components: {
      Layout: { bodyBg: '#1a1714', siderBg: '#242019', headerBg: '#201c17' },
      Menu: { itemBg: '#242019', subMenuItemBg: '#242019', itemSelectedBg: '#332d24', itemSelectedColor: '#d4a843', itemHoverBg: '#2c2720' },
      Card: { colorBgContainer: '#242019' },
      Table: { colorBgContainer: '#242019', headerBg: '#2c2720', rowHoverBg: '#332d24' },
      Input: { colorBgContainer: '#2c2720' },
      Select: { colorBgContainer: '#2c2720' },
      Modal: { contentBg: '#242019' },
      Tabs: { inkBarColor: '#d4a843', itemSelectedColor: '#d4a843', itemHoverColor: '#b8922e' },
      Tag: { defaultBg: '#332d24', defaultColor: '#d4a843' },
    },
  },
}

export const THEMES: ThemeDefinition[] = [wenfang, mobai, qingzhu, yedu]

export const DEFAULT_THEME_KEY = 'wenfang'

export { FONT_BODY, FONT_DISPLAY }

export function getThemeByKey(key: string): ThemeDefinition {
  return THEMES.find((t) => t.key === key) || wenfang
}
