import { expect, test, type Page, type Route } from '@playwright/test'

const zh = {
  workbench: '\u65b0\u4e66\u7acb\u9879\u5de5\u4f5c\u53f0',
  noModel: '\u5f53\u524d\u6ca1\u6709\u53ef\u7528\u6a21\u578b',
  saveDraft: '\u53ea\u4fdd\u5b58\u8349\u7a3f',
  createWork: '\u521b\u5efa\u65b0\u4f5c\u54c1',
  message: '\u7ed9\u53f8\u547d\u7684\u6d88\u606f',
  send: '\u53d1\u9001',
  skip: '\u8df3\u8fc7\u5e76\u751f\u6210\u521b\u610f\u65b9\u5411',
  runtime: '\u5f53\u524d\u6a21\u578b\u8fd0\u884c\u72b6\u6001',
  runtimeToggle: '\u67e5\u770b\u5f53\u524d\u6a21\u578b\u4e0e\u8fd0\u884c\u72b6\u6001',
  create: '\u786e\u8ba4\u5e76\u521b\u5efa\u6b63\u5f0f\u4f5c\u54c1',
  freeStart: '\u514d\u8d39\u5f00\u59cb',
  installOpenCode: '\u514d\u8d39\u5f00\u59cb\u5199\u5c0f\u8bf4',
}

const catalog = {
  categories: [{
    id: 'fantasy',
    label: '\u5947\u5e7b\u5f02\u4e16',
    description: '\u72ec\u7acb\u89c4\u5219\u4e0e\u9ad8\u538b\u5192\u9669',
    themes: [{ id: 'fantasy:experiment', label: '\u5b9e\u9a8c\u4f53\u9003\u4ea1' }],
    defaults: {
      world_tone: '\u9b54\u6cd5\u9700\u8981\u4ee3\u4ef7',
      story_structure: '\u9003\u4ea1\u4e0e\u63ed\u79d8\u53cc\u7ebf',
      pacing: '\u6bcf\u7ae0\u63a8\u8fdb\u4e00\u4e2a\u5371\u673a',
      writing_style: '\u8282\u594f\u5229\u843d',
      special_requirements: [],
      avoid: [],
    },
  }],
  platforms: ['\u8d77\u70b9'],
  audiences: ['\u6210\u5e74\u5927\u4f17'],
  length_options: [{ id: 'long', label: '\u957f\u7bc7', words: 600000, chapters: 240 }],
  stage_order: ['constraints', 'concepts', 'world_style', 'characters', 'locations', 'macro_outline', 'opening_outline', 'final_review'],
  stage_labels: {
    world_style: '\u6587\u98ce\u4e0e\u4e16\u754c\u89c2',
    characters: '\u89d2\u8272\u4e0e\u5173\u7cfb',
    locations: '\u5730\u70b9\u4e0e\u52bf\u529b',
    macro_outline: '\u5168\u4e66\u4e3b\u7ebf\u4e0e\u5377\u7eb2',
    opening_outline: '\u524d 15 \u7ae0\u7ec6\u7eb2',
    final_review: '\u6700\u7ec8\u5ba1\u9605',
  },
}

const baseForm = {
  brief: '\u88ab\u79d8\u5bc6\u7ec4\u7ec7\u57f9\u517b\u7684\u5b9e\u9a8c\u4f53\uff0c\u4ece\u4e16\u754c\u88c2\u9699\u9003\u5165\u9b54\u6cd5\u5f02\u4e16\u3002',
  preset_id: 'fantasy',
  theme_id: 'fantasy:experiment',
  genre: '\u5947\u5e7b\u5f02\u4e16',
  target_audience: '\u6210\u5e74\u5927\u4f17',
  platform: '\u8d77\u70b9',
  target_words: 600000,
  target_chapters: 240,
  world_tone: '\u9b54\u6cd5\u9700\u8981\u4ee3\u4ef7',
  story_structure: '\u9003\u4ea1\u4e0e\u63ed\u79d8\u53cc\u7ebf',
  pacing: '\u6bcf\u7ae0\u63a8\u8fdb\u4e00\u4e2a\u5371\u673a',
  writing_style: '\u8282\u594f\u5229\u843d',
  special_requirements: [],
  avoid: [],
}

const model = {
  id: 'model-1',
  provider: 'opencode_cli',
  default_model: 'free-model',
  is_global_default: true,
  readiness_status: 'ready',
  is_usable: true,
}

function conceptSession(id = 'session-1') {
  return {
    id,
    status: 'reviewing',
    revision: 1,
    current_stage: 'concepts',
    draft: {
      form: baseForm,
      concepts: [{
        id: 'concept-1',
        source_index: 0,
        title: '\u88c2\u9699\u5b9e\u9a8c\u4f53',
        subtitle: '\u9b54\u6cd5\u9003\u4ea1',
        logline: '\u5973\u5b69\u5fc5\u987b\u628a\u8bb0\u5fc6\u5178\u5f53\u4ee3\u4ef7\uff0c\u624d\u80fd\u9003\u8fc7\u4e24\u4e2a\u4e16\u754c\u7684\u8ffd\u6740\u3002',
        protagonist_seed: { name: '\u963f\u79bb', identity: '\u5b9e\u9a8c\u4f53', goal: '\u627e\u5230\u81ea\u5df1\u7684\u8eab\u4e16', lack: '\u4e0d\u6562\u4fe1\u4efb\u4efb\u4f55\u4eba' },
        world_hook: '\u9b54\u6cd5\u4f1a\u541e\u566c\u4f7f\u7528\u8005\u7684\u7ecf\u5386',
        core_conflict: '\u5979\u9700\u8981\u529b\u91cf\uff0c\u5374\u4e0d\u80fd\u5931\u53bb\u81ea\u5df1',
        story_engine: '\u6bcf\u4e00\u6b21\u9003\u4ea1\u90fd\u63ed\u5f00\u4e00\u6761\u5b9e\u9a8c\u4e16\u754c\u7684\u771f\u76f8',
        opening_hook: '\u5979\u5728\u5c38\u4f53\u4e0a\u770b\u89c1\u81ea\u5df1\u7684\u5b9e\u9a8c\u7f16\u53f7',
        differentiators: ['\u53cc\u4e16\u754c\u8ffd\u6740'],
        risks: [],
        coverage: { score: 92, covered: [], missing: [] },
      }],
      stages: {},
    },
  }
}

async function fulfill(route: Route, data: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(data),
  })
}

async function mockApi(page: Page, options: {
  models?: unknown[]
  sessions?: unknown[]
  session?: Record<string, unknown>
  gettingStarted?: Record<string, unknown>
  onInterview?: (route: Route, call: number) => Promise<void>
  onApply?: (route: Route) => Promise<void>
} = {}) {
  let interviewCalls = 0
  let startedSession: Record<string, unknown> | undefined
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()

    if (path === '/api/v1/config/models') {
      return fulfill(route, { code: 0, data: { items: options.models ?? [model], total: (options.models ?? [model]).length } })
    }
    if (path === '/api/v1/config/getting-started') {
      return fulfill(route, {
        code: 0,
        data: options.gettingStarted ?? {
          needs_setup: false,
          has_any_model: true,
          global_model: { provider: 'opencode_cli', model: 'free-model' },
        },
      })
    }
    if (path === '/api/v1/projects') {
      return fulfill(route, { code: 0, data: { items: [], total: 0 } })
    }
    if (path === '/api/v1/novel-creation/presets') {
      return fulfill(route, { code: 0, data: catalog })
    }
    if (path === '/api/v1/novel-creation/sessions' && method === 'GET') {
      const sessions = options.sessions ?? (options.session ? [options.session] : [])
      return fulfill(route, { code: 0, data: { sessions } })
    }
    if (path === '/api/v1/novel-creation/start' && method === 'POST') {
      const session = options.session ?? { id: 'draft-1', status: 'drafting', revision: 1, current_stage: 'constraints', draft: { form: baseForm, concepts: [], stages: {} } }
      startedSession = session
      return fulfill(route, { code: 0, data: { session_id: session.id, session } })
    }
    if (path.startsWith('/api/v1/novel-creation/sessions/') && path.endsWith('/interview/next')) {
      interviewCalls += 1
      if (options.onInterview) return options.onInterview(route, interviewCalls)
      return fulfill(route, { code: 0, data: { session_id: 'session-1', state: 'ready', history: [] } })
    }
    if (path.startsWith('/api/v1/novel-creation/sessions/') && method === 'GET') {
      const sessionId = path.split('/').pop() || 'session-1'
      return fulfill(route, { code: 0, data: options.session ?? startedSession ?? conceptSession(sessionId) })
    }
    if (path.startsWith('/api/v1/novel-creation/sessions/') && path.endsWith('/runs') && method === 'POST') {
      return fulfill(route, { code: 0, data: { run: { id: 'run-1', status: 'running', current_message: '\u6b63\u5728\u751f\u6210\u4e09\u5957\u8f7b\u91cf\u521b\u610f' } } })
    }
    if (path === '/api/v1/novel-creation/apply' && method === 'POST') {
      if (options.onApply) return options.onApply(route)
      return fulfill(route, { code: 0, data: { project_id: 'project-1', warnings: [] } })
    }
    if (path === '/api/v1/ai/system-assistant/conversations' && method === 'GET') {
      return fulfill(route, { code: 0, data: { items: [], total: 0 } })
    }
    if (path === '/api/v1/ai/system-assistant/conversations' && method === 'POST') {
      return fulfill(route, { code: 0, data: { conversation: { id: 'conversation-1', title: '\u65b0\u4e66' } } })
    }
    if (path.includes('/ai/system-assistant/conversations/') && path.endsWith('/turns') && method === 'POST') {
      return fulfill(route, { code: 0, data: { conversation: { id: 'conversation-1', title: '\u65b0\u4e66' } } })
    }
    return fulfill(route, { code: 0, data: {} })
  })
}

test('allows a no-model author to save and restore a creation draft', async ({ page }) => {
  await mockApi(page, { models: [] })
  await page.goto('/novel-creation', { waitUntil: 'domcontentloaded' })

  await expect(page.getByRole('heading', { name: zh.workbench })).toBeVisible()
  await expect(page.getByText(zh.noModel)).toBeVisible()
  await page.locator('textarea').first().fill(baseForm.brief)
  await expect(page.getByRole('button', { name: zh.saveDraft })).toBeEnabled()
  await page.getByRole('button', { name: zh.saveDraft }).click()
  await expect(page).toHaveURL(/session=draft-1/)
})

test('does not treat a detected Claude CLI as a usable writing model', async ({ page }) => {
  await mockApi(page, {
    models: [{
      id: 'claude-detected',
      provider: 'claude_cli',
      default_model: 'claude-code',
      is_global_default: false,
      readiness_status: 'detected',
      is_usable: false,
    }],
  })
  await page.goto('/novel-creation', { waitUntil: 'domcontentloaded' })

  await expect(page.getByText(zh.noModel)).toBeVisible()
  await expect(page.getByRole('combobox', { name: '\u9009\u62e9\u672c\u9636\u6bb5\u6a21\u578b' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /\u751f\u6210\u4e09\u5957\u8f7b\u91cf\u521b\u610f/ })).toBeDisabled()
})

test('keeps mobile navigation named and touch-sized at 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockApi(page)
  await page.goto('/novel-creation', { waitUntil: 'domcontentloaded' })

  for (const name of ['\u4f5c\u54c1\u5e93', '\u65b0\u4e66\u7acb\u9879', 'AI \u52a9\u624b', '\u7cfb\u7edf\u8bbe\u7f6e']) {
    const button = page.getByRole('button', { name, exact: true })
    await expect(button).toBeVisible()
    const box = await button.boundingBox()
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44)
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
  await expect(page.getByRole('button', { name: '\u65b0\u4e66\u7acb\u9879' })).toHaveAttribute('aria-current', 'page')
})

test('shows quota exhaustion as an error with the CLI runtime diagnostics', async ({ page }) => {
  await mockApi(page, {
    onInterview: async (route) => fulfill(route, {
      detail: {
        message: 'Free usage exceeded, retrying in 9h',
        failure_class: 'quota_or_rate_limit',
        next_action: '\u5207\u6362\u6709\u989d\u5ea6\u7684\u6a21\u578b\u540e\u91cd\u8bd5\u3002',
        runtime: {
          effective_model: 'opencode_cli:free-model', provider: 'opencode_cli', model_source: 'global_default',
          tool_mode: 'local_cli_text_json', timeout_seconds: 45, quota_status: 'exhausted_or_limited',
        },
      },
    }, 422),
  })
  await page.goto('/gui', { waitUntil: 'domcontentloaded' })
  await page.getByLabel(zh.message).fill('\u6211\u60f3\u521b\u5efa\u4e00\u672c\u65b0\u7684\u5c0f\u8bf4')
  await page.getByRole('button', { name: new RegExp(zh.send) }).click()

  const failure = page.locator('[data-message-status="error"]')
  await expect(failure).toContainText('Free usage exceeded')
  await page.getByLabel(zh.runtimeToggle).click()
  await expect(page.getByLabel(zh.runtime)).toContainText('\u989d\u5ea6\u5df2\u8017\u5c3d\u6216\u9650\u6d41')
  await expect(page.getByLabel(zh.runtime)).toContainText('opencode_cli:free-model')
  await expect(failure).not.toContainText('\u5df2\u5b8c\u6210')
})

test('shows a timeout as an error rather than a completed assistant turn', async ({ page }) => {
  await mockApi(page, {
    onInterview: async (route) => fulfill(route, {
      detail: {
        message: '\u8bf7\u6c42\u8d85\u65f6\uff0845 \u79d2\uff09',
        failure_class: 'timeout',
        next_action: '\u5207\u6362\u66f4\u5feb\u7684\u6a21\u578b\u540e\u91cd\u8bd5\u3002',
        runtime: {
          effective_model: 'codex_cli:codex-cli', provider: 'codex_cli', model_source: 'global_default',
          tool_mode: 'local_cli_text_json', timeout_seconds: 45, quota_status: 'unknown',
        },
      },
    }, 422),
  })
  await page.goto('/gui', { waitUntil: 'domcontentloaded' })
  await page.getByLabel(zh.message).fill('\u6211\u60f3\u521b\u5efa\u4e00\u672c\u65b0\u7684\u5c0f\u8bf4')
  await page.getByRole('button', { name: new RegExp(zh.send) }).click()

  const failure = page.locator('[data-message-status="error"]')
  await expect(failure).toContainText('\u8bf7\u6c42\u8d85\u65f6')
  await expect(failure).toContainText('\u6267\u884c\u5931\u8d25')
  await page.getByLabel(zh.runtimeToggle).click()
  await expect(page.getByLabel(zh.runtime)).toContainText('\u8d85\u65f645 \u79d2')
})

test('keeps a failed interview skip in the error state', async ({ page }) => {
  await mockApi(page, {
    onInterview: async (route, call) => {
      if (call === 1) {
        return fulfill(route, {
          code: 0,
          data: {
            session_id: 'session-1', state: 'question', history: [],
            question: { question: '\u4e3b\u89d2\u6700\u6015\u5931\u53bb\u4ec0\u4e48\uff1f', type: 'text' },
          },
        })
      }
      return fulfill(route, {
        detail: {
          message: 'Free usage exceeded, retrying in 9h',
          failure_class: 'quota_or_rate_limit',
          next_action: '\u5207\u6362\u6709\u989d\u5ea6\u7684\u6a21\u578b\u540e\u91cd\u8bd5\u3002',
          runtime: {
            effective_model: 'opencode_cli:free-model', provider: 'opencode_cli', model_source: 'global_default',
            tool_mode: 'local_cli_text_json', timeout_seconds: 45, quota_status: 'exhausted_or_limited',
          },
        },
      }, 422)
    },
  })
  await page.goto('/gui', { waitUntil: 'domcontentloaded' })
  await page.getByLabel(zh.message).fill('\u6211\u60f3\u521b\u5efa\u4e00\u672c\u65b0\u7684\u5c0f\u8bf4')
  await page.getByRole('button', { name: new RegExp(zh.send) }).click()
  await page.getByRole('button', { name: zh.skip }).click()

  const failure = page.locator('[data-message-status="error"]')
  await expect(failure).toContainText('Free usage exceeded')
  await expect(failure).toContainText('\u6267\u884c\u5931\u8d25')
  await expect(page).toHaveURL(/\/gui$/)
})

test('enters the one shared creation workbench from the dashboard', async ({ page }) => {
  await mockApi(page)
  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: zh.createWork }).click()
  await expect(page).toHaveURL(/\/novel-creation$/)
  await expect(page.getByRole('heading', { name: zh.workbench })).toBeVisible()
})

test('automatically guides a first-time user to one-click OpenCode setup', async ({ page }) => {
  await mockApi(page, {
    models: [],
    gettingStarted: {
      installed: false,
      command: null,
      version: null,
      managed_by_siming: false,
      model_source: 'none',
      free_models: [],
      recommended_model: null,
      platform_supported: true,
      install_location: 'C:/Users/author/AppData/Local/Siming/managed-cli/opencode/bin/opencode.exe',
      configured: false,
      configured_model: null,
      is_global_default: false,
      has_any_model: false,
      needs_setup: true,
      global_model: null,
      official_links: {
        releases: 'https://github.com/anomalyco/opencode/releases/latest',
        install_docs: 'https://opencode.ai/docs/#install',
        model_docs: 'https://opencode.ai/docs/providers/#opencode-zen',
      },
    },
  })
  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })

  await expect(page).toHaveURL(/\/getting-started$/)
  await expect(page.getByRole('button', { name: new RegExp(zh.installOpenCode) })).toBeVisible()
  await expect(page.getByText('\u4e0d\u7528\u586b\u5199 API Key')).toBeVisible()
})

test('turns one story sentence into the first three-concept run after setup', async ({ page }) => {
  await mockApi(page, {
    gettingStarted: {
      needs_setup: false,
      configured: true,
      is_global_default: true,
      platform_supported: true,
      free_models: [],
      global_model: { provider: 'opencode_cli', model: 'opencode/free-model' },
    },
  })
  await page.goto('/getting-started', { waitUntil: 'domcontentloaded' })
  await page.getByLabel('\u4f60\u60f3\u5199\u4ec0\u4e48\u6545\u4e8b\uff1f').fill('\u4e00\u5bb6\u53ea\u5728\u5348\u591c\u8425\u4e1a\u7684\u4fee\u4ed9\u5ba2\u6808')
  await page.getByRole('button', { name: /\u751f\u6210\u4e09\u5957\u5c0f\u8bf4\u521b\u610f/ }).click()
  await expect(page).toHaveURL(/\/novel-creation\?session=draft-1&run=run-1/)
})

test('restores a draft and creates the final project only after final review', async ({ page }) => {
  const session = {
    ...conceptSession(),
    current_stage: 'final_review',
    draft: {
      ...conceptSession().draft,
      selected_concept_id: 'concept-1',
      stages: {
        final_review: {
          status: 'generated',
          data: { ready: true, counts: { characters: 3, sections: 30 }, warnings: [], blocking: [] },
        },
      },
    },
  }
  await mockApi(page, {
    session,
    sessions: [session],
    onApply: async (route) => fulfill(route, { code: 0, data: { project_id: 'project-1', warnings: [] } }),
  })
  await page.goto('/novel-creation?session=session-1', { waitUntil: 'domcontentloaded' })

  await expect(page.getByRole('heading', { name: '\u6700\u7ec8\u5ba1\u9605' })).toBeVisible()
  await expect(page.getByRole('button', { name: zh.create })).toBeEnabled()
  await page.getByRole('button', { name: zh.create }).click()
  await expect(page).toHaveURL(/\/project\/project-1/)
})
