import assert from 'node:assert/strict'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'
import { createServer } from 'vite'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(scriptDir, '..')
const outputDir = path.resolve(frontendRoot, '..', 'docs', 'images', 'readme')
const timestamp = '2026-07-16T08:30:00.000Z'
const modelId = 'opencode_cli:opencode/deepseek-v4-flash-free'

const project = {
  id: 'mistlight-project',
  title: '雾海拾光',
  description: '潮汐测绘师沈禾追寻失落灯塔，从雾海里拾回被世界遗忘的名字。',
  tags: '["奇幻异世", "悬疑冒险", "成长"]',
  narrative_perspective: '第三人称限知',
  writing_style: '克制、清透，带海洋意象',
  short_sentences: false,
  daily_word_goal: 3000,
  created_at: '2026-06-18T09:00:00.000Z',
  updated_at: timestamp,
}

const model = {
  id: 'readme-opencode-model',
  provider: 'opencode_cli',
  name: 'OpenCode DeepSeek V4 Flash',
  default_model: 'opencode/deepseek-v4-flash-free',
  is_global_default: true,
  readiness_status: 'ready',
  readiness_message: '真实对话验证成功',
  is_usable: true,
  last_tested_at: timestamp,
}

const catalog = {
  categories: [{
    id: 'fantasy',
    label: '奇幻异世',
    description: '独立规则、未知地理与高压冒险',
    themes: [{ id: 'fantasy:mist-sea', label: '雾海群岛与失落文明' }],
    defaults: {
      world_tone: '潮汐记录一切，但每次读取都会付出记忆。',
      story_structure: '群岛公路片式探索，灯塔之谜逐卷收束。',
      pacing: '每章推进一次实际行动，每三章回收一条线索。',
      writing_style: '画面清透、节奏克制，用海雾与灯火表现人物情绪。',
      special_requirements: ['关键线索必须有前置证据', '角色选择改变后续岛屿状态'],
      avoid: ['无代价复活', '靠误会拖延主线'],
    },
  }],
  platforms: ['起点', '番茄', '晋江'],
  audiences: ['成年大众', '女频成长向', '奇幻悬疑读者'],
  length_options: [{ id: 'long', label: '长篇', words: 800000, chapters: 320 }],
  stage_order: ['constraints', 'concepts', 'world_style', 'characters', 'locations', 'macro_outline', 'opening_outline', 'final_review'],
  stage_labels: {
    constraints: '创作约束',
    concepts: '创意方向',
    world_style: '文风与世界观',
    characters: '角色与关系',
    locations: '地点与势力',
    macro_outline: '全书主线与卷纲',
    opening_outline: '前 15 章细纲',
    final_review: '最终审阅',
  },
}

const creationForm = {
  brief: '失去记忆的潮汐测绘师，在吞没旧世界的雾海中寻找一座不存在的灯塔。',
  preset_id: 'fantasy',
  theme_id: 'fantasy:mist-sea',
  genre: '奇幻异世',
  target_audience: '成年大众',
  platform: '起点',
  target_words: 800000,
  target_chapters: 320,
  world_tone: catalog.categories[0].defaults.world_tone,
  story_structure: catalog.categories[0].defaults.story_structure,
  pacing: catalog.categories[0].defaults.pacing,
  writing_style: catalog.categories[0].defaults.writing_style,
  special_requirements: catalog.categories[0].defaults.special_requirements,
  avoid: catalog.categories[0].defaults.avoid,
}

const concepts = [
  {
    id: 'mistlight',
    source_index: 0,
    title: '雾海拾光',
    subtitle: '从遗忘中拾回世界',
    logline: '失忆的测绘师沈禾必须点亮七座沉没灯塔，才能阻止雾海吞掉所有人的名字。',
    protagonist_seed: { name: '沈禾', identity: '潮汐测绘师', goal: '找回失落灯塔与自己的真名', lack: '害怕承认亲手封闭过灯塔' },
    world_hook: '海雾会抹去名字，灯塔用人的记忆作为燃料。',
    core_conflict: '她每点亮一座灯塔，就更接近真相，也更可能忘记想拯救的人。',
    story_engine: '一岛一谜案，每次测绘改写航路，并推进灯塔真相。',
    opening_hook: '沈禾从新地图上看见一座小时候才存在的岛。',
    differentiators: ['记忆作为航海资源', '群岛单元剧', '可变世界地图'],
    risks: ['需控制世界谜题的信息密度'],
    coverage: { score: 94, covered: ['世界观', '主角', '主线'], missing: [] },
  },
  {
    id: 'nameless-keeper',
    source_index: 1,
    title: '守灯人没有名字',
    subtitle: '一座灯塔，一城假记忆',
    logline: '沈禾成为废灯塔的新守灯人，却发现整座港城都在共享一段伪造的昨天。',
    protagonist_seed: { name: '沈禾', identity: '无名守灯人', goal: '找出港城为何不再抵达明天', lack: '总想独自承担代价' },
    world_hook: '灯塔可以将一天的记忆投向全城，却不能证明那一天真实发生过。',
    core_conflict: '维持假记忆能保护城民，拆穿它才能让时间继续。',
    story_engine: '逐日解谜与城市群像，每个被改写的人都提供一块真相。',
    opening_hook: '她在日记里连续三十页写下：“今天是我第一天守灯。”',
    differentiators: ['城市时间谜题', '群像真相拼图'],
    risks: ['主场景集中，需要持续制造空间变化'],
    coverage: { score: 89, covered: ['主角', '悬疑'], missing: ['长线反派'] },
  },
  {
    id: 'tide-courier',
    source_index: 2,
    title: '最后一封送往陆地的信',
    subtitle: '雾海信使的末日航程',
    logline: '当所有航路即将沉没，沈禾带着一封不能拆的信，穿过七座正在忘记彼此的岛。',
    protagonist_seed: { name: '沈禾', identity: '雾海信使', goal: '在大潮前将信送到传说中的陆地', lack: '不相信承诺能比生存更重要' },
    world_hook: '只有手写信不会被海雾篡改，每个送信人却会逐渐失去自己的过去。',
    core_conflict: '她必须守住别人的真相，同时决定自己的记忆是否值得牺牲。',
    story_engine: '递送任务串联群岛，每封旧信解锁一段共同历史。',
    opening_hook: '收件人一栏写的是沈禾自己，寄信日期却在三百年前。',
    differentiators: ['信件叙事', '末日公路冒险', '承诺主题'],
    risks: ['需避免单元任务重复'],
    coverage: { score: 91, covered: ['主线', '结构', '差异点'], missing: [] },
  },
]

const creationSession = {
  id: 'readme-session',
  status: 'reviewing',
  revision: 7,
  current_stage: 'concepts',
  updated_at: timestamp,
  draft: { form: creationForm, concepts, stages: {} },
}

const outlineItems = [{
  id: 'volume-1',
  parent_id: null,
  node_type: 'volume',
  title: '第一卷 消失的航路',
  status: 'in_progress',
  sort_order: 1,
  children: [{
    id: 'outline-23',
    parent_id: 'volume-1',
    node_type: 'chapter',
    title: '第23章 灯塔重燃',
    status: 'in_progress',
    sort_order: 23,
    children: [
      { id: 'section-23-1', parent_id: 'outline-23', node_type: 'section', title: '潮洞潜入', status: 'completed', sort_order: 1, children: [] },
      { id: 'section-23-2', parent_id: 'outline-23', node_type: 'section', title: '灯芯的名字', status: 'in_progress', sort_order: 2, children: [] },
      { id: 'section-23-3', parent_id: 'outline-23', node_type: 'section', title: '雾中应答', status: 'pending', sort_order: 3, children: [] },
    ],
  }],
}]

const chapters = [
  { id: 'chapter-23', title: '第23章 灯塔重燃', word_count: 3286, current_version: 4, outline_node_id: 'outline-23', outline_title: '第23章 灯塔重燃', outline_status: 'in_progress', outline_node_type: 'chapter', outline_path: ['第一卷 消失的航路', '第23章 灯塔重燃'], summary_text: '沈禾和陆准潜入退潮后的灯塔基座，确认灯芯保存着被抹除的城市真名。', key_events: ['发现灯芯真名', '旧航路短暂复苏', '沈禾失去一段童年记忆'] },
  { id: 'chapter-22', title: '第22章 失温海图', word_count: 3018, current_version: 3, outline_node_id: null, outline_title: null, outline_status: 'completed', outline_node_type: 'chapter', outline_path: ['第一卷 消失的航路', '第22章 失温海图'] },
  { id: 'chapter-21', title: '第21章 雾门之后', word_count: 2964, current_version: 2, outline_node_id: null, outline_title: null, outline_status: 'completed', outline_node_type: 'chapter', outline_path: ['第一卷 消失的航路', '第21章 雾门之后'] },
  { id: 'chapter-20', title: '第20章 无声潮汐', word_count: 3142, current_version: 3, outline_node_id: null, outline_title: null, outline_status: 'completed', outline_node_type: 'chapter', outline_path: ['第一卷 消失的航路', '第20章 无声潮汐'] },
].map((item) => ({ ...item, project_id: project.id, created_at: timestamp, updated_at: timestamp }))

const chapterDetail = {
  ...chapters[0],
  snapshot_count: 4,
  content: `退潮后，灯塔露出了水下的第十三层台阶。

沈禾把铜制测潮仪贴在石门上。指针没有指向海，反而缓慢转向她的心口。门后传来灯芯燃烧的轻响，像有人在雾里一遍遍读着她的名字。

“我们还有十七分钟。”陆准站在涨潮线旁，手里的风灯被海雾压得只剩一点蓝光，“潮水一回来，这里的路就会忘记我们。”

沈禾没有回头。她在石门中央摸到一道新刻痕，那是一个早已从所有海图上消失的城市真名。也是她母亲留给她的最后一个坐标。

她将手掌按进刻痕。灯塔在地底醒来，整片雾海随之亮了一瞬。`,
}

const snapshots = [
  { id: 'snapshot-4', chapter_id: 'chapter-23', version_number: 4, word_count: 3286, trigger_type: 'manual_save', created_at: timestamp },
  { id: 'snapshot-3', chapter_id: 'chapter-23', version_number: 3, word_count: 3154, trigger_type: 'ai_insert', created_at: '2026-07-16T07:40:00.000Z' },
  { id: 'snapshot-2', chapter_id: 'chapter-23', version_number: 2, word_count: 2840, trigger_type: 'manual_save', created_at: '2026-07-15T13:20:00.000Z' },
]

const operation = {
  id: 'readme-cataloging-operation',
  source_kind: 'cataloging',
  title: '《雾海拾光》作品建档',
  status: 'running',
  health_status: 'active',
  phase: '逐章建档与叙事账本更新',
  current_message: '正在读取第 138 章「潮痕遗址」，并更新角色、世界观与叙事账本',
  progress: { mode: 'determinate', current: 138, total: 600, percent: 23 },
  model_source: modelId,
  tool_mode: 'local_cli_mcp',
  next_action: '继续等待；已完成章节会逐章保存，不受总运行时长限制。',
  resume_url: `/project/${project.id}?view=cataloging`,
  can_pause: true,
  can_cancel: true,
  can_retry: false,
  elapsed_seconds: 18864,
  last_activity_at: new Date().toISOString(),
}

const gettingStartedEmpty = {
  free_models: [{ id: 'opencode/deepseek-v4-flash-free', display_name: 'DeepSeek V4 Flash', recommended: true }],
  recommended_model: 'opencode/deepseek-v4-flash-free',
  platform_supported: true,
  configured: false,
  configured_model: null,
  is_global_default: false,
  needs_setup: true,
  has_detected_models: false,
  has_usable_models: false,
  recommended_action: '准备 OpenCode 并完成一次真实对话验证',
  global_model: null,
  activation_job: null,
}

const gettingStartedReady = {
  ...gettingStartedEmpty,
  configured: true,
  configured_model: 'opencode/deepseek-v4-flash-free',
  is_global_default: true,
  needs_setup: false,
  has_detected_models: true,
  has_usable_models: true,
  recommended_action: '开始创作',
  global_model: { provider: 'opencode_cli', model: 'opencode/deepseek-v4-flash-free' },
}

const envelope = (data) => ({ code: 0, message: 'ok', data })

async function fulfill(route, data, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(envelope(data)),
  })
}

async function installApiMock(page, scene) {
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname.replace(/^\/api\/v1/, '')
    const method = request.method()

    if (pathname === '/operations') {
      return fulfill(route, { items: scene === 'task-center' ? [operation] : [], total: scene === 'task-center' ? 1 : 0 })
    }
    if (pathname === '/config/getting-started') {
      return fulfill(route, scene === 'quick-start' ? gettingStartedEmpty : gettingStartedReady)
    }
    if (pathname === '/config/models') {
      return fulfill(route, { items: scene === 'quick-start' ? [] : [model], total: scene === 'quick-start' ? 0 : 1 })
    }
    if (pathname === '/projects' && method === 'GET') {
      return fulfill(route, { items: scene === 'quick-start' || scene === 'novel-creation' ? [] : [project], total: scene === 'quick-start' || scene === 'novel-creation' ? 0 : 1 })
    }
    if (pathname === `/projects/${project.id}` && method === 'GET') {
      return fulfill(route, project)
    }
    if (pathname === '/novel-creation/presets') {
      return fulfill(route, catalog)
    }
    if (pathname === '/novel-creation/sessions' && method === 'GET') {
      return fulfill(route, { sessions: scene === 'novel-creation' ? [creationSession] : [] })
    }
    if (pathname === `/novel-creation/sessions/${creationSession.id}` && method === 'GET') {
      return fulfill(route, creationSession)
    }
    if (pathname === `/projects/${project.id}/outline`) {
      return fulfill(route, { items: outlineItems, flat: outlineItems, total: 5 })
    }
    if (pathname === `/projects/${project.id}/chapters`) {
      return fulfill(route, { items: chapters, total: chapters.length })
    }
    if (pathname === `/projects/${project.id}/chapters/chapter-23/snapshots`) {
      return fulfill(route, { items: snapshots, total: snapshots.length })
    }
    if (pathname === `/projects/${project.id}/chapters/chapter-23`) {
      return fulfill(route, chapterDetail)
    }
    if (pathname === '/ai/system-assistant/conversations' && method === 'GET') {
      return fulfill(route, { items: [], total: 0 })
    }

    return fulfill(route, {})
  })
}

async function preparePage(context, scene) {
  const page = await context.newPage()
  await installApiMock(page, scene)
  return page
}

async function settle(page) {
  await page.addStyleTag({ content: `
    *, *::before, *::after {
      animation: none !important;
      transition: none !important;
      scroll-behavior: auto !important;
      caret-color: transparent !important;
    }
  ` })
  await page.evaluate(() => document.fonts.ready)
  await page.waitForTimeout(250)
}

async function capture(page, filename) {
  const filePath = path.join(outputDir, filename)
  await settle(page)
  await page.screenshot({ path: filePath, fullPage: false, animations: 'disabled' })
  const image = await readFile(filePath)
  assert.ok(image.length > 20_000, `${filename} is unexpectedly small`)
  assert.equal(image.readUInt32BE(16), 1440, `${filename} width`)
  assert.equal(image.readUInt32BE(20), 900, `${filename} height`)
  return { filename, bytes: image.length }
}

async function run() {
  const publicFixture = JSON.stringify({ project, model, catalog, creationForm, concepts, chapters, chapterDetail, operation })
  for (const marker of ['C:\\\\Users\\\\', 'AppData', '@qq.com', 'api_key', '2891474276']) {
    assert.equal(publicFixture.includes(marker), false, `public fixture contains sensitive marker: ${marker}`)
  }

  await mkdir(outputDir, { recursive: true })
  const server = await createServer({
    root: frontendRoot,
    logLevel: 'error',
    server: { host: '127.0.0.1', port: 4175, strictPort: false },
  })
  await server.listen()
  const baseUrl = server.resolvedUrls?.local?.[0]
  assert.ok(baseUrl, 'Vite did not expose a local URL')

  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: 'light',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
  })
  await context.addInitScript(() => {
    localStorage.setItem('siming-theme', 'wenfang')
    localStorage.setItem('siming_getting_started_deferred', 'true')
    localStorage.setItem('siming_sidebar_collapsed', 'false')
    localStorage.setItem('siming_ai_panel_collapsed', 'true')

    class StableEventSource extends EventTarget {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSED = 2
      constructor(url) {
        super()
        this.url = String(url)
        this.readyState = StableEventSource.OPEN
        this.withCredentials = false
        queueMicrotask(() => this.onopen?.(new Event('open')))
      }
      close() { this.readyState = StableEventSource.CLOSED }
      onopen = null
      onmessage = null
      onerror = null
    }
    Object.defineProperty(window, 'EventSource', { configurable: true, value: StableEventSource })
  })

  const results = []
  try {
    const quickStart = await preparePage(context, 'quick-start')
    await quickStart.goto(new URL('/getting-started', baseUrl).href, { waitUntil: 'domcontentloaded' })
    await quickStart.getByRole('heading', { name: '从一句故事想法开始' }).waitFor()
    results.push(await capture(quickStart, 'quick-start.png'))
    await quickStart.close()

    const creation = await preparePage(context, 'novel-creation')
    await creation.goto(new URL(`/novel-creation?session=${creationSession.id}`, baseUrl).href, { waitUntil: 'domcontentloaded' })
    await creation.getByRole('heading', { name: '先选故事发动机' }).waitFor()
    await creation.locator('.creation-concept-card').nth(2).waitFor()
    results.push(await capture(creation, 'novel-creation.png'))
    await creation.close()

    const workspace = await preparePage(context, 'project-workspace')
    await workspace.goto(new URL(`/project/${project.id}`, baseUrl).href, { waitUntil: 'domcontentloaded' })
    await workspace.locator('.writer-content-input').waitFor()
    await workspace.locator('.project-workspace-title').filter({ hasText: project.title }).waitFor()
    await workspace.getByRole('button', { name: '收起项目导航' }).click()
    await workspace.addStyleTag({ content: '.global-operation-badge-floating { display: none !important; }' })
    results.push(await capture(workspace, 'project-workspace.png'))
    await workspace.close()

    const taskCenter = await preparePage(context, 'task-center')
    await taskCenter.goto(new URL('/dashboard', baseUrl).href, { waitUntil: 'domcontentloaded' })
    await taskCenter.getByText(project.title, { exact: true }).first().waitFor()
    await taskCenter.getByRole('button', { name: /全局任务中心/ }).click()
    await taskCenter.getByText(operation.title, { exact: true }).waitFor()
    await taskCenter.getByLabel('已完成 138，共 600').waitFor()
    results.push(await capture(taskCenter, 'task-center.png'))
    await taskCenter.close()
  } finally {
    await context.close()
    await browser.close()
    await server.close()
  }

  for (const result of results) {
    process.stdout.write(`${result.filename}: 1440x900, ${result.bytes} bytes\n`)
  }
}

run().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
