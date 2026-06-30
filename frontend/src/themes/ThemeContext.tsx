import { createContext, useContext, useEffect, useState, useCallback, type ReactNode } from 'react'
import { ConfigProvider, theme as antdTheme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { THEMES, DEFAULT_THEME_KEY, getThemeByKey, type ThemeDefinition } from './definitions'

const STORAGE_KEY = 'siming-theme'
const LEGACY_STORAGE_KEY = 'moshu-theme'

interface ThemeContextValue {
  currentTheme: ThemeDefinition
  setTheme: (key: string) => void
  themes: ThemeDefinition[]
}

const ThemeContext = createContext<ThemeContextValue>({
  currentTheme: getThemeByKey(DEFAULT_THEME_KEY),
  setTheme: () => {},
  themes: THEMES,
})

export function useTheme() {
  return useContext(ThemeContext)
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [themeKey, setThemeKey] = useState<string>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) || localStorage.getItem(LEGACY_STORAGE_KEY) || DEFAULT_THEME_KEY
    } catch {
      return DEFAULT_THEME_KEY
    }
  })

  const setTheme = useCallback((key: string) => {
    setThemeKey(key)
    try {
      localStorage.setItem(STORAGE_KEY, key)
    } catch {
      // ignore
    }
  }, [])

  const currentTheme = getThemeByKey(themeKey)

  // Apply dark algorithm for night theme
  const themeConfig = { ...currentTheme.config }
  if (themeKey === 'yedu') {
    themeConfig.algorithm = antdTheme.darkAlgorithm
  }

  // Apply CSS variables to :root for CSS token usage
  useEffect(() => {
    const root = document.documentElement
    const token = currentTheme.config.token
    if (token) {
      const entries: Record<string, string> = {
        '--ant-color-primary': token.colorPrimary || '',
        '--ant-color-success': token.colorSuccess || '',
        '--ant-color-warning': token.colorWarning || '',
        '--ant-color-error': token.colorError || '',
        '--ant-color-bg-layout': token.colorBgLayout || '',
        '--ant-color-bg-container': token.colorBgContainer || '',
        '--ant-color-bg-elevated': token.colorBgElevated || '',
        '--ant-color-text': token.colorText || '',
        '--ant-color-text-secondary': token.colorTextSecondary || '',
        '--ant-color-text-tertiary': token.colorTextTertiary || '',
        '--ant-color-text-quaternary': token.colorTextQuaternary || '',
        '--ant-color-border': token.colorBorder || '',
        '--ant-color-border-secondary': token.colorBorderSecondary || '',
        '--siming-accent': currentTheme.accent,
        '--siming-grain-opacity': String(currentTheme.grainOpacity),
        '--moshu-accent': currentTheme.accent,
        '--moshu-grain-opacity': String(currentTheme.grainOpacity),
      }
      // Also set component-specific overrides from components config
      const comps = currentTheme.config.components
      if (comps?.Tabs) {
        entries['--ant-color-primary-hover'] = (comps.Tabs as any).itemHoverColor || token.colorPrimary || ''
      }
      Object.entries(entries).forEach(([k, v]) => {
        if (v) root.style.setProperty(k, v)
      })
    }
  }, [currentTheme])

  return (
    <ThemeContext.Provider value={{ currentTheme, setTheme, themes: THEMES }}>
      <ConfigProvider
        locale={zhCN}
        theme={themeConfig}
      >
        {children}
      </ConfigProvider>
    </ThemeContext.Provider>
  )
}
