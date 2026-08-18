import { expect, test, type Page, type Route } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

const model = {
  id: 'opencode-ready',
  provider: 'opencode_cli',
  default_model: 'opencode/deepseek-v4-flash-free',
  is_global_default: true,
  readiness_status: 'ready',
  readiness_message: '真实对话已通过',
  is_usable: true,
  provider_type: 'local_cli',
}

const project = {
  id: 'p1',
  title: '河谷温室异色记录',
  description: '公共温室花色异常的轻悬疑故事',
  created_at: '2026-07-20T08:00:00Z',
  updated_at: '2026-07-27T08:00:00Z',
}

const outlineNodes = [
  {
    id: 'volume-1', project_id: 'p1', parent_id: null, node_type: 'volume', title: '第一卷 花展前的异常花期',
    summary: '周遥发现公共温室的蓝花一夜变白。', status: 'in_progress', sort_order: 0, linked_characters: [],
    created_at: '2026-07-20T08:00:00Z', updated_at: '2026-07-27T08:00:00Z',
    children: [
      {
        id: 'outline-1', project_id: 'p1', parent_id: 'volume-1', node_type: 'chapter', title: '第一章 一夜变白的蓝花',
        summary: '植物学实习生周遥发现不同花圃的蓝花同时变色。', status: 'completed', sort_order: 0, linked_characters: [],
        created_at: '2026-07-20T08:00:00Z', updated_at: '2026-07-27T08:00:00Z', children: [],
      },
      {
        id: 'outline-2', project_id: 'p1', parent_id: 'volume-1', node_type: 'chapter', title: '第二章 消失的土壤样本留下错误标签',
        summary: '周遥在样本柜里发现原始土壤样本被替换。', status: 'in_progress', sort_order: 1, linked_characters: [],
        created_at: '2026-07-20T08:00:00Z', updated_at: '2026-07-27T08:00:00Z', children: [],
      },
    ],
  },
]

const flatOutline = [outlineNodes[0], ...outlineNodes[0].children]

const chapters = [
  {
    id: 'chapter-1', project_id: 'p1', outline_node_id: 'outline-1',
    title: '第一章 一夜变白的蓝花与一份错位的值班表', word_count: 2186, current_version: 3,
    outline_title: '第一章 一夜变白的蓝花', outline_status: 'completed', outline_node_type: 'chapter',
    outline_path: ['第一卷 花展前的异常花期', '第一章 一夜变白的蓝花'],
    summary_text: '周遥发现蓝花异常，并在值班表中找到被涂改的温室编号。',
    key_events: ['花色异常', '错位值班表', '土壤编号'],
    created_at: '2026-07-20T08:00:00Z', updated_at: '2026-07-27T08:00:00Z',
  },
  {
    id: 'chapter-2', project_id: 'p1', outline_node_id: 'outline-2',
    title: '第二章 花展闭馆以后样本柜少了一只玻璃瓶', word_count: 2042, current_version: 2,
    outline_title: '第二章 消失的土壤样本', outline_status: 'in_progress', outline_node_type: 'chapter',
    outline_path: ['第一卷 花展前的异常花期', '第二章 消失的土壤样本'],
    created_at: '2026-07-20T08:00:00Z', updated_at: '2026-07-27T08:00:00Z',
  },
]

const operations = [
  {
    id: 'review-new', source_id: 'session-1', source_kind: 'novel_creation', project_id: 'p1', title: '新书立项 · 最终审阅',
    status: 'waiting_user', health_status: 'active', phase: 'final_review', current_message: '最终审阅已经保存，等待你的确认',
    progress: { mode: 'indeterminate' }, model_source: 'opencode_cli:opencode/deepseek-v4-flash-free',
    outcome: 'waiting_user', result_summary: '立项内容已经生成', result: { outcome: 'waiting_user', summary: '立项内容已经生成' },
    attention: { kind: 'confirmation', title: '最终内容等待确认', message: '请审阅后创建正式作品。', action_label: '前往审阅', action_url: '/novel-creation?session=session-1&stage=final_review' },
    resume_url: '/novel-creation?session=session-1', can_pause: false, can_cancel: true, can_retry: false,
    elapsed_seconds: 5400, last_activity_at: '2026-07-25T08:00:00Z', created_at: '2026-07-25T06:00:00Z', updated_at: '2026-07-25T08:00:00Z',
  },
  {
    id: 'review-old', source_id: 'session-1', source_kind: 'novel_creation', project_id: 'p1', title: '新书立项 · 最终审阅',
    status: 'failed', health_status: 'stalled', phase: 'final_review', current_message: '上一次尝试未完成', progress: { mode: 'indeterminate' },
    can_pause: false, can_cancel: false, can_retry: false, elapsed_seconds: 300,
    created_at: '2026-07-24T06:00:00Z', updated_at: '2026-07-24T06:05:00Z',
  },
  {
    id: 'archive-1', source_id: 'chapter-1', source_kind: 'cataloging', project_id: 'p1', title: '作品建档 · 第一章',
    status: 'running', health_status: 'active', phase: 'chapter_archive', current_message: '正在提取角色状态与伏笔',
    progress: { mode: 'determinate', current: 2, total: 5, percent: 40 }, can_pause: true, can_cancel: true, can_retry: false,
    elapsed_seconds: 68, last_activity_at: '2026-07-27T09:59:30Z', created_at: '2026-07-27T09:58:52Z', updated_at: '2026-07-27T09:59:30Z',
  },
]

const creationPresets = {
  categories: [
    {
      id: 'suspense',
      label: '悬疑推理',
      description: '证据链、认知差与持续升级的谜团',
      themes: [{ id: 'suspense:social', label: '近未来社会派' }],
      defaults: {
        world_tone: '技术克制，规则透明',
        story_structure: '三层谜团逐步揭示',
        pacing: '每章推进一条可验证线索',
        writing_style: '冷静、清晰、有画面感',
        special_requirements: ['伏笔可回看'],
        avoid: ['空降真相'],
      },
    },
    {
      id: 'science-fiction',
      label: '科幻未来',
      description: '技术变化与人的选择',
      themes: [{ id: 'science-fiction:near-future', label: '近未来城市' }],
      defaults: {
        world_tone: '可信技术与现实生活并存',
        story_structure: '单主线推进',
        pacing: '紧凑',
        writing_style: '专业克制',
        special_requirements: [],
        avoid: [],
      },
    },
  ],
  platforms: ['暂不确定', '起点中文网'],
  audiences: ['成年大众', '青年读者'],
  length_options: [
    { id: 'medium', label: '中篇', words: 200000, chapters: 80 },
    { id: 'long', label: '长篇', words: 600000, chapters: 240 },
  ],
  stage_order: ['constraints', 'concepts', 'world_style', 'characters', 'locations', 'macro_outline', 'opening_outline', 'final_review'],
  stage_labels: {
    constraints: '创作约束', concepts: '创意方向', world_style: '文风与世界观', characters: '角色与关系',
    locations: '地点与势力', macro_outline: '全书卷纲', opening_outline: '前3章细纲', final_review: '最终审阅',
  },
}

const authorBrief = '河谷镇的植物学实习生周遥发现公共温室里的蓝花一夜变白。'
const authorOutline = '第一卷：周遥调查花色变化的原因。\n第二卷：异常花期波及年度展览。\n结局：周遥公开被调换的土壤试剂检测报告。'
const lockedRequirements = ['主角姓名必须是周遥', '花展在七天后举行', '全书固定规划为六卷', '不得改变公开检测报告的结局']

const creationForm = {
  brief: authorBrief,
  preset_id: 'suspense',
  theme_id: 'suspense:social',
  genre: '悬疑推理',
  target_audience: '成年大众',
  platform: '暂不确定',
  target_words: 600000,
  target_chapters: 240,
  world_tone: '技术克制，规则透明',
  story_structure: '三层谜团逐步揭示',
  pacing: '每章推进一条可验证线索',
  writing_style: '冷静、清晰、有画面感',
  special_requirements: ['伏笔可回看'],
  avoid: ['空降真相'],
}

const authorConcept = {
  id: 'author-concept',
  source_index: 0,
  title: '温室异色记录',
  subtitle: '作者方案整理稿',
  logline: '周遥必须在年度花展前查出是谁调换了温室的土壤试剂。',
  protagonist_seed: { name: '周遥', identity: '植物学实习生', goal: '找回原始土壤样本', lack: '不敢质疑导师的判断' },
  world_hook: '河谷镇依靠公共温室维持四季花展',
  core_conflict: '公开试剂真相会让周遥失去实习资格',
  story_engine: '每核对一块花圃，就出现一条被改写的养护记录',
  opening_hook: '周遥发现公共温室的蓝花一夜变白',
  differentiators: ['作者专名已保留', '六卷结构已锁定'],
  risks: ['需要持续校验时间线'],
  coverage: { score: 96, covered: ['主角', '世界规则', '结局'], missing: [] },
}

const authorWorkbenchSession = {
  id: 'author-workbench',
  status: 'reviewing',
  revision: 7,
  current_stage: 'world_style',
  updated_at: '2026-07-27T09:55:00Z',
  runs: [{
    id: 'run-failed', session_id: 'author-workbench', stage: 'world_style', status: 'failed',
    operation_id: 'operation-failed', model_source: 'opencode_cli:opencode/deepseek-v4-flash-free', attempt: 2,
    result_mode: 'repaired', current_message: '模型结构修复后仍缺少世界规则',
    warning: '原阶段草稿未被覆盖', next_action: '检查要求后重新生成文风与世界观',
  }],
  last_error: {
    failure_class: 'invalid_response', message: '文风与世界观生成未完成',
    next_action: '作者原始设定和当前草稿均已保留，可以安全重试。', run_id: 'run-failed',
    failed_stage: 'world_style', failed_stage_label: '文风与世界观',
  },
  stage_flow: {
    attention_stage: 'world_style', recommended_stage: 'world_style', pending_confirmations: ['world_style'],
    items: {
      world_style: { stage: 'world_style', label: '文风与世界观', status: 'generated', can_view: true, can_generate: true, can_confirm: true, blocked_by: [], actions: ['refine', 'confirm'], next_stage: 'characters' },
      characters: { stage: 'characters', label: '角色与关系', status: 'pending', can_view: false, can_generate: false, can_confirm: false, blocked_by: [{ stage: 'world_style', label: '文风与世界观', reason: '等待确认' }], actions: [], next_stage: 'locations' },
      locations: { stage: 'locations', label: '地点与势力', status: 'pending', can_view: false, can_generate: false, can_confirm: false, blocked_by: [{ stage: 'characters', label: '角色与关系', reason: '等待确认' }], actions: [], next_stage: 'macro_outline' },
      macro_outline: { stage: 'macro_outline', label: '全书卷纲', status: 'pending', can_view: false, can_generate: false, can_confirm: false, blocked_by: [{ stage: 'locations', label: '地点与势力', reason: '等待确认' }], actions: [], next_stage: 'opening_outline' },
      opening_outline: { stage: 'opening_outline', label: '前3章细纲', status: 'pending', can_view: false, can_generate: false, can_confirm: false, blocked_by: [{ stage: 'macro_outline', label: '全书卷纲', reason: '等待确认' }], actions: [], next_stage: 'final_review' },
      final_review: { stage: 'final_review', label: '最终审阅', status: 'pending', can_view: false, can_generate: false, can_confirm: false, blocked_by: [{ stage: 'opening_outline', label: '前3章细纲', reason: '等待确认' }], actions: [] },
    },
  },
  draft: {
    schema_version: 3,
    creation_mode: 'author_led',
    author_brief: authorBrief,
    author_outline: authorOutline,
    locked_requirements: lockedRequirements,
    form: creationForm,
    concepts: [authorConcept],
    selected_concept_id: authorConcept.id,
    quick_mode: false,
    stages: {
      world_style: {
        status: 'generated',
        source: 'model',
        data: {
          world_tone: '明亮、克制的现代河谷小镇',
          writing_style: '有限视角，证据与感官细节并行',
          story_structure: '围绕花色异常展开三层谜团',
          pacing: '每章推进一条证据并留下可回看的钩子',
          worldbuilding: [{ title: '温室轮作表', dimension: '管理规则', content: '每块花圃按季节轮换品种，试剂和土壤样本都必须双人登记。' }],
        },
      },
    },
  },
}

const runningCreationSession = {
  id: 'running-creation',
  status: 'drafting',
  revision: 3,
  current_stage: 'concepts',
  updated_at: '2026-07-27T09:58:00Z',
  runs: [{
    id: 'run-running', session_id: 'running-creation', stage: 'concepts', status: 'running',
    operation_id: 'operation-running', model_source: 'opencode_cli:opencode/deepseek-v4-flash-free', attempt: 1,
    input_revision: 3, current_message: '正在忠实整理作者方案...',
  }],
  draft: {
    schema_version: 3,
    creation_mode: 'author_led',
    author_brief: authorBrief,
    author_outline: authorOutline,
    locked_requirements: lockedRequirements,
    form: creationForm,
    concepts: [],
    stages: {},
  },
}

const recoveryCreationSession = {
  id: 'recovery-creation',
  status: 'reviewing',
  revision: 9,
  current_stage: 'characters',
  updated_at: '2026-07-27T10:00:00Z',
  runs: [{
    id: 'run-conflict', session_id: 'recovery-creation', stage: 'characters', status: 'failed',
    operation_id: 'operation-conflict', model_source: 'deepseek:deepseek-v4-flash', attempt: 1,
    input_revision: 7, current_message: '任务基于版本 7，当前作者内容已更新到版本 9',
    failure_class: 'revision_conflict', next_action: '选择按原输入或最新内容重试',
    result: { candidate_available: true },
  }],
  draft: {
    schema_version: 3,
    creation_mode: 'author_led',
    author_brief: authorBrief,
    author_outline: authorOutline,
    locked_requirements: lockedRequirements,
    form: creationForm,
    concepts: [authorConcept],
    stages: {},
  },
}

const creationSessions: Record<string, typeof authorWorkbenchSession | typeof runningCreationSession | typeof recoveryCreationSession> = {
  [authorWorkbenchSession.id]: authorWorkbenchSession,
  [runningCreationSession.id]: runningCreationSession,
  [recoveryCreationSession.id]: recoveryCreationSession,
}

async function fulfill(route: Route, data: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
}

async function mockUiApi(page: Page, assistantScenario: 'running' | 'recovery' = 'running') {
  await page.clock.setFixedTime(new Date('2026-07-27T10:00:00Z'))
  await page.addInitScript(() => {
    class MockEventSource {
      static readonly CONNECTING = 0
      static readonly OPEN = 1
      static readonly CLOSED = 2
      readonly url: string
      readyState = MockEventSource.OPEN
      onopen: ((event: Event) => void) | null = null
      onerror: ((event: Event) => void) | null = null
      constructor(url: string | URL) { this.url = String(url) }
      addEventListener() {}
      removeEventListener() {}
      dispatchEvent() { return true }
      close() { this.readyState = MockEventSource.CLOSED }
    }
    Object.defineProperty(window, 'EventSource', { configurable: true, value: MockEventSource })
  })

  await page.route('**/api/v1/**', async (route) => {
    const requestUrl = new URL(route.request().url())
    const path = requestUrl.pathname
    if (path === '/api/v1/novel-creation/presets') return fulfill(route, { code: 0, data: creationPresets })
    if (path === '/api/v1/novel-creation/sessions') return fulfill(route, { code: 0, data: { sessions: [] } })
    if (path === '/api/v1/projects') return fulfill(route, { code: 0, data: { items: [project], total: 1 } })
    if (
      path === '/api/v1/ai/system-assistant/conversations'
      && requestUrl.searchParams.get('scope_type') === 'project'
    ) return fulfill(route, { code: 0, data: {
      items: [{
        id: 'conversation-project', title: '\u4e3b\u89d2\u52a8\u673a\u8c03\u6574', scope_type: 'project', scope_id: 'p1', project_id: 'p1',
        created_at: '2026-07-27T09:00:00Z', updated_at: '2026-07-27T10:00:00Z',
      }],
      total: 1,
    } })
    if (path === '/api/v1/ai/system-assistant/conversations') return fulfill(route, { code: 0, data: {
      items: [{
        id: assistantScenario === 'recovery' ? 'conversation-recovery' : 'conversation-running',
        title: assistantScenario === 'recovery' ? '角色冲突恢复' : '八卷仙侠悬疑立项',
        creation_session_id: assistantScenario === 'recovery' ? 'recovery-creation' : 'running-creation',
        user_brief: '保留主角，扩写为八卷。', created_at: '2026-07-27T09:00:00Z', updated_at: '2026-07-27T10:00:00Z',
      }],
      total: 1,
    } })
    if (path === '/api/v1/ai/system-assistant/conversations/conversation-recovery') return fulfill(route, { code: 0, data: {
      conversation: {
        id: 'conversation-recovery', title: '角色冲突恢复', creation_session_id: 'recovery-creation',
        user_brief: '保留主角，只调整反派。', created_at: '2026-07-27T09:00:00Z', updated_at: '2026-07-27T10:00:00Z',
      },
      messages: [
        {
          id: 'message-user-recovery', role: 'user', content: '主角设定不动，重做反派。',
          status: 'completed', message_type: 'text', created_at: '2026-07-27T09:59:00Z', payload: {},
        },
        {
          id: 'message-assistant-recovery', role: 'assistant', content: '任务基于版本 7，当前作者内容已更新到版本 9',
          status: 'error', message_type: 'error', created_at: '2026-07-27T10:00:00Z',
          payload: { run: recoveryCreationSession.runs[0] },
        },
      ],
    } })
    if (path === '/api/v1/ai/system-assistant/conversations/conversation-running') return fulfill(route, { code: 0, data: {
      conversation: {
        id: 'conversation-running', title: '八卷仙侠悬疑立项', creation_session_id: 'running-creation',
        user_brief: '保留主角，扩写为八卷。', created_at: '2026-07-27T09:00:00Z', updated_at: '2026-07-27T10:00:00Z',
      },
      messages: [
        {
          id: 'message-user-running', role: 'user', content: '主角保持不变，把原来的三卷改成八卷。',
          status: 'completed', message_type: 'text', created_at: '2026-07-27T09:59:00Z', payload: {},
        },
        {
          id: 'message-assistant-running', role: 'assistant', content: '正在调整主线与卷纲',
          status: 'running', message_type: 'operation', created_at: '2026-07-27T10:00:00Z',
          payload: { run: {
            id: 'run-running', session_id: 'running-creation', stage: 'macro_outline', status: 'running',
            operation_id: 'operation-running', model_source: 'opencode_cli:opencode/deepseek-v4-flash-free',
            attempt: 1, current_message: '正在调整第 3—8 卷，并保留主角设定',
          } },
        },
      ],
    } })
    if (path === '/api/v1/ai/system-assistant/conversations/conversation-project') return fulfill(route, { code: 0, data: {
      conversation: {
        id: 'conversation-project', title: '\u4e3b\u89d2\u52a8\u673a\u8c03\u6574', scope_type: 'project', scope_id: 'p1', project_id: 'p1',
        created_at: '2026-07-27T09:00:00Z', updated_at: '2026-07-27T10:00:00Z',
      },
      messages: [
        {
          id: 'message-project-user', role: 'user', content: '\u8c03\u6574\u4e3b\u89d2\u52a8\u673a\uff0c\u4f46\u4e0d\u6539\u53d8\u5df2\u9501\u5b9a\u7684\u7ed3\u5c40\u3002',
          status: 'completed', message_type: 'text', created_at: '2026-07-27T09:59:00Z', payload: {},
        },
        {
          id: 'message-project-assistant', role: 'assistant', content: '\u5df2\u8c03\u6574\u4e3b\u89d2\u52a8\u673a\uff0c\u9501\u5b9a\u7ed3\u5c40\u4fdd\u6301\u4e0d\u53d8\u3002',
          status: 'completed', message_type: 'artifact_change', created_at: '2026-07-27T10:00:00Z', payload: {},
        },
      ],
    } })
    if (path === '/api/v1/novel-creation/sessions/running-creation/artifacts') return fulfill(route, { code: 0, data: {
      session_id: 'running-creation',
      revision: 3,
      artifacts: [
        { artifact: 'constraints', label: '作品定位', status: 'confirmed', source: 'author', revision: 3, locked_paths: ['/genre'], flow: { can_view: true, can_generate: false, can_confirm: false, blocked_by: [] } },
        { artifact: 'concepts', label: '创意方案', status: 'confirmed', source: 'author', revision: 3, locked_paths: ['/protagonist'], flow: { can_view: true, can_generate: true, can_confirm: false, blocked_by: [] } },
        { artifact: 'world_style', label: '文风与世界观', status: 'confirmed', source: 'model', revision: 3, locked_paths: [], flow: { can_view: true, can_generate: true, can_confirm: false, blocked_by: [] } },
        { artifact: 'characters', label: '角色与关系', status: 'confirmed', source: 'author', revision: 3, locked_paths: ['/characters/0'], flow: { can_view: true, can_generate: true, can_confirm: false, blocked_by: [] } },
        { artifact: 'locations', label: '地点与势力', status: 'confirmed', source: 'model', revision: 3, locked_paths: [], flow: { can_view: true, can_generate: true, can_confirm: false, blocked_by: [] } },
        { artifact: 'macro_outline', label: '主线与卷纲', status: 'generated', source: 'assistant', revision: 3, locked_paths: [], running_operation: { id: 'run-running', stage: 'macro_outline', status: 'running', current_message: '正在调整第 3—8 卷' }, flow: { can_view: true, can_generate: true, can_confirm: true, blocked_by: [] } },
        { artifact: 'opening_outline', label: '开篇细纲', status: 'stale', source: 'model', revision: 3, stale_reason: '上游阶段“主线与卷纲”已修改', locked_paths: [], checkpoint_count: 1, can_undo: true, flow: { can_view: true, can_generate: true, can_confirm: false, blocked_by: [], soft_dependencies: [{ stage: 'macro_outline', label: '主线与卷纲', reason: 'not_confirmed', message: '仍可生成' }] } },
        { artifact: 'final_review', label: '完整性检查', status: 'pending', source: 'unknown', revision: 3, locked_paths: [], flow: { can_view: false, can_generate: false, can_confirm: false, blocked_by: [{ stage: 'opening_outline', label: '开篇细纲', reason: '等待确认' }] } },
      ],
    } })
    if (path === '/api/v1/novel-creation/sessions/recovery-creation/artifacts') return fulfill(route, { code: 0, data: {
      session_id: 'recovery-creation',
      revision: 9,
      artifacts: [
        { artifact: 'constraints', label: '作品定位', status: 'confirmed', source: 'author', revision: 9, locked_paths: ['/genre'], flow: { can_view: true, can_generate: false, can_confirm: false, blocked_by: [] } },
        { artifact: 'concepts', label: '创意方案', status: 'confirmed', source: 'author', revision: 9, locked_paths: ['/protagonist'], flow: { can_view: true, can_generate: true, can_confirm: false, blocked_by: [] } },
        {
          artifact: 'characters', label: '角色与关系', status: 'conflict', stored_status: 'confirmed', source: 'author', revision: 9,
          locked_paths: ['/characters/0'],
          conflict: { run_id: 'run-conflict', message: '任务基于版本 7，当前版本为 9', candidate_available: true, input_revision: 7, current_revision: 9 },
          flow: { can_view: true, can_generate: true, can_confirm: false, blocked_by: [], soft_dependencies: [] },
        },
        { artifact: 'macro_outline', label: '主线与卷纲', status: 'stale', source: 'model', revision: 9, stale_reason: '角色与关系已由作者修改', locked_paths: [], flow: { can_view: true, can_generate: true, can_confirm: false, blocked_by: [], soft_dependencies: [] } },
      ],
    } })
    if (path === '/api/v1/novel-creation/sessions/recovery-creation/validate-consistency') return fulfill(route, {
      code: 0,
      data: {
        valid: false,
        revision: 9,
        summary: { blocking: 1, warnings: 1, total: 2 },
        issues: [
          { code: 'revision_conflict', severity: 'blocking', artifact: 'characters', message: '旧任务候选稿未覆盖作者当前角色数据' },
          { code: 'stale_artifact', severity: 'warning', artifact: 'macro_outline', message: '主线与卷纲需要重新校验' },
        ],
      },
    })
    if (path === '/api/v1/novel-creation/sessions/running-creation/validate-consistency') return fulfill(route, {
      code: 0,
      data: {
        valid: false,
        revision: 3,
        summary: { blocking: 0, warnings: 1, total: 1 },
        issues: [{ code: 'stale_artifact', severity: 'warning', artifact: 'opening_outline', message: '开篇细纲基于旧版上游数据，建议重新校验' }],
      },
    })
    if (/^\/api\/v1\/novel-creation\/sessions\/running-creation\/artifacts\/[^/]+\/versions$/.test(path)) {
      return fulfill(route, { code: 0, data: { versions: [
        {
          id: 'version-current', session_id: 'running-creation', artifact: 'constraints', revision: 3,
          status: 'confirmed', source: 'author', change_type: 'patch', parent_version_id: 'version-original',
          created_at: '2026-07-27T10:00:00Z',
        },
        {
          id: 'version-original', session_id: 'running-creation', artifact: 'constraints', revision: 1,
          status: 'generated', source: 'model', change_type: 'generate', parent_version_id: null,
          created_at: '2026-07-27T09:00:00Z',
        },
      ] } })
    }
    if (path === '/api/v1/novel-creation/artifact-versions/version-current') return fulfill(route, { code: 0, data: {
      version: { id: 'version-current', revision: 3 },
      against: { id: 'version-original', revision: 1 },
      changes: [
        { path: '/genre', action: 'replace', before: '\u4f20\u7edf\u4ed9\u4fa0', after: '\u4ed9\u4fa0\u60ac\u7591' },
        { path: '/target_words', action: 'replace', before: 120000, after: 180000 },
      ],
      change_count: 2,
      truncated: false,
    } })
    if (path === '/api/v1/novel-creation/artifact-versions/version-original') return fulfill(route, { code: 0, data: {
      version: { id: 'version-original', revision: 1 }, against: null,
      changes: [
        { path: '/genre', action: 'add', after: '\u4f20\u7edf\u4ed9\u4fa0' },
        { path: '/target_words', action: 'add', after: 120000 },
      ],
      change_count: 2,
      truncated: false,
    } })
    if (path === '/api/v1/novel-creation/sessions/running-creation/imports') return fulfill(route, { code: 0, data: { imports: [] } })
    if (path === '/api/v1/novel-creation/imports/import-preview') return fulfill(route, { code: 0, data: {
      id: 'import-preview', source_file_id: 'import-preview', session_id: 'running-creation', operation_id: 'operation-import-preview',
      filename: '仙侠悬疑八卷大纲.docx', status: 'waiting_user', input_revision: 3,
      text_length: 32876, chunk_count: 5, processed_chunks: 5,
      preview: {
        detected: { characters: 12, factions: 4, locations: 19, volumes: 8, chapter_summaries: 146 },
        artifact_counts: { world_style: 6, characters: 12, locations: 23, macro_outline: 8, opening_outline: 146 },
        available_artifacts: ['world_style', 'characters', 'locations', 'macro_outline', 'opening_outline'],
        conflicts: [
          { kind: 'existing_artifact', artifact: 'characters', status: 'confirmed' },
          { kind: 'existing_artifact', artifact: 'macro_outline', status: 'generated' },
          { kind: 'duplicate', artifact: 'locations' },
        ],
      },
    } })
    const creationSessionMatch = path.match(/^\/api\/v1\/novel-creation\/sessions\/([^/]+)$/)
    if (creationSessionMatch && route.request().method() === 'GET') {
      const creationSession = creationSessions[decodeURIComponent(creationSessionMatch[1])]
      return creationSession
        ? fulfill(route, { code: 0, data: creationSession })
        : fulfill(route, { code: 404, message: '立项草稿不存在', data: null }, 404)
    }
    if (path === '/api/v1/operations/operation-running/cancel' && route.request().method() === 'POST') {
      return fulfill(route, { code: 0, data: { status: 'cancelling' } })
    }
    if (path === '/api/v1/operations/operation-running/pause' && route.request().method() === 'POST') {
      return fulfill(route, { code: 0, data: { status: 'paused' } })
    }
    if (path === '/api/v1/operations/operation-running/continue' && route.request().method() === 'POST') {
      return fulfill(route, { code: 0, data: { status: 'running' } })
    }
    if (path === '/api/v1/config/getting-started') {
      return fulfill(route, { code: 0, data: {
        free_models: [], recommended_model: null, platform_supported: true, configured: true,
        configured_model: model.default_model, is_global_default: true, needs_setup: false,
        has_detected_models: true, has_usable_models: true,
        global_model: { provider: model.provider, model: model.default_model }, activation_job: null,
      } })
    }
    if (path === '/api/v1/config/app-info') return fulfill(route, { code: 0, data: { name: 'Siming', version: '3.2.1' } })
    if (path === '/api/v1/config/models') return fulfill(route, { code: 0, data: { items: [model], total: 1 } })
    if (path === '/api/v1/config/global-model') return fulfill(route, { code: 0, data: { provider: model.provider, model: model.default_model } })
    if (path === '/api/v1/config/content-root') return fulfill(route, { code: 0, data: { current_path: 'D:/Siming', default_path: 'D:/Siming', is_default: true, exists: true, is_empty: false, looks_like_siming_root: true } })
    if (path === '/api/v1/config/launcher') return fulfill(route, { code: 0, data: { launch_mode: 'desktop', update_channel: 'stable', restart_required: false } })
    if (path === '/api/v1/operations') return fulfill(route, { code: 0, data: { items: operations } })
    if (path === '/api/v1/projects/p1') return fulfill(route, { code: 0, data: project })
    if (path === '/api/v1/projects/p1/chapters') return fulfill(route, { code: 0, data: { items: chapters, total: chapters.length } })
    if (path === '/api/v1/projects/p1/chapters/chapter-1') return fulfill(route, { code: 0, data: { ...chapters[0], content: '清晨的玻璃温室蒙着薄雾。\n\n周遥第一次看见整排蓝花在一夜之间变成了白色。', snapshot_count: 2 } })
    if (path === '/api/v1/projects/p1/chapters/chapter-1/snapshots') return fulfill(route, { code: 0, data: { items: [
      { id: 'snapshot-2', chapter_id: 'chapter-1', version_number: 3, word_count: 2186, trigger_type: 'manual_save', created_at: '2026-07-27T08:00:00Z' },
      { id: 'snapshot-1', chapter_id: 'chapter-1', version_number: 2, word_count: 2040, trigger_type: 'ai_insert', created_at: '2026-07-26T08:00:00Z' },
    ], total: 2 } })
    if (path === '/api/v1/projects/p1/outline') return fulfill(route, { code: 0, data: { items: outlineNodes, flat: flatOutline, total: flatOutline.length } })
    if (path === '/api/v1/projects/p1/characters') return fulfill(route, { code: 0, data: { items: [{ id: 'character-1', name: '周遥', role_type: 'protagonist', current_version: 2, is_evolution_tracked: true }], total: 1 } })
    if (path === '/api/v1/projects/p1/narrative-governance') return fulfill(route, { code: 0, data: {
      foreshadowings: [], causal_edges: [], narrative_debts: [], character_states: [], quality_metrics: [], checkpoints: [],
      counts: { open_foreshadowings: 0, open_causal_edges: 0, open_debts: 0 },
    } })
    return fulfill(route, { code: 0, data: {} })
  })
}

async function expectViewportSafe(page: Page) {
  const overflow = await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  const clippedControls = await page.evaluate(() => Array.from(document.querySelectorAll<HTMLElement>('button, input, textarea, [role="button"]'))
    .filter((element) => {
      const style = getComputedStyle(element)
      if (style.display === 'none' || style.visibility === 'hidden') return false
      const rect = element.getBoundingClientRect()
      const intersectsViewport = rect.right > 0 && rect.left < window.innerWidth && rect.bottom > 0 && rect.top < window.innerHeight
      return intersectsViewport && rect.width > 0 && rect.height > 0 && (rect.left < -1 || rect.right > window.innerWidth + 1)
    })
    .map((element) => element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 40)))
  expect(clippedControls).toEqual([])
}

async function expectNoSeriousAccessibilityViolations(page: Page) {
  const finishAnimations = () => page.evaluate(() => {
    for (const animation of document.getAnimations()) {
      try {
        animation.finish()
      } catch {
        animation.cancel()
      }
    }
  })
  await finishAnimations()
  // Ant Design may enqueue the modal's enter transition one frame after it is
  // mounted. Let that frame settle, then finish the newly-created animation so
  // axe measures final colors instead of the translucent transition midpoint.
  await page.waitForTimeout(200)
  await finishAnimations()
  const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()
  expect(result.violations.filter((item) => ['serious', 'critical'].includes(item.impact || ''))).toEqual([])
}

async function expectVisualSnapshot(page: Page, name: string) {
  if (!process.env.CI) {
    await expect(page).toHaveScreenshot(name, { animations: 'disabled' })
  }
}

async function expectFullPageVisualSnapshot(page: Page, name: string) {
  if (!process.env.CI) {
    // Chromium stitches full-page captures in viewport-height tiles. Keep the
    // intentionally off-screen accessibility link out of that stitching pass.
    const skipLink = page.locator('.siming-skip-link')
    await skipLink.evaluate((element) => { (element as HTMLElement).style.visibility = 'hidden' })
    try {
      await expect(page).toHaveScreenshot(name, { animations: 'disabled', fullPage: true })
    } finally {
      await skipLink.evaluate((element) => { (element as HTMLElement).style.removeProperty('visibility') })
    }
  }
}

const viewports = [
  { name: '1920x1080', width: 1920, height: 1080 },
  { name: '1400x900', width: 1400, height: 900 },
  { name: '1280x720', width: 1280, height: 720 },
  { name: '800x600', width: 800, height: 600 },
]

for (const viewport of viewports) {
  test(`keeps core writing views usable at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await mockUiApi(page)

    await page.goto('/project/p1', { waitUntil: 'networkidle' })
    await expect(page.locator('.writer-editor-title')).toContainText('第一章 一夜变白的蓝花')
    await expect(page.getByText('已完成', { exact: true }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: /章节操作/ })).toBeVisible()
    if (viewport.width === 1920) {
      await page.getByRole('button', { name: /章节操作/ }).click()
      await page.getByRole('menuitem', { name: /删除本章/ }).click()
      await expect(page.getByRole('dialog', { name: /第一章 一夜变白的蓝花/ })).toBeVisible()
      await page.getByRole('button', { name: /取\s*消/ }).click()
    }
    await expectViewportSafe(page)
    await expectVisualSnapshot(page, `writer-${viewport.name}.png`)

    await page.goto('/project/p1?view=outline', { waitUntil: 'networkidle' })
    await expect(page.getByLabel('搜索大纲')).toBeVisible()
    await expectViewportSafe(page)
    await expectVisualSnapshot(page, `outline-${viewport.name}.png`)

    await page.getByRole('button', { name: /任务中心/ }).click()
    await expect(page.getByRole('heading', { name: /待你处理/ })).toBeVisible()
    await expect(page.getByText('历史尝试 1')).toBeVisible()
    await expect(page.getByText(/^最近活动 \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/).first()).toBeVisible()
    await expect(page.getByText(/最近活动.*小时前/)).toHaveCount(0)
    await expect(page.locator('.ant-drawer-content-wrapper')).toHaveCSS('transform', 'none')
    await expectViewportSafe(page)
    await expectVisualSnapshot(page, `task-center-${viewport.name}.png`)

    if (viewport.width === 1920 || viewport.width === 800) {
      await page.getByRole('button', { name: '全部标为已读' }).click()
      await expect(page.locator('.global-operation-trigger')).toHaveAttribute('aria-label', /0 项未读提醒/)
      await expect(page.getByRole('button', { name: '全部标为已读' })).toBeDisabled()
      await expect(page.getByRole('heading', { name: '待你处理' })).toBeVisible()
      await expectViewportSafe(page)
      await expectVisualSnapshot(page, `task-center-read-${viewport.name}.png`)
    }

    if (viewport.width === 1920 || viewport.width === 800) await expectNoSeriousAccessibilityViolations(page)
  })
}

test('keeps onboarding, model settings and governance visually focused', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  await mockUiApi(page)

  await page.goto('/getting-started', { waitUntil: 'networkidle' })
  await expect(page.getByRole('heading', { name: 'OpenCode 已可用，再完成一步即可启用完整 Agent' })).toBeVisible()
  await expectViewportSafe(page)
  await expectVisualSnapshot(page, 'quick-start-ready-1920x1080.png')

  await page.goto('/settings', { waitUntil: 'networkidle' })
  await expect(page.getByText('AI 已准备好')).toBeVisible()
  await expectViewportSafe(page)
  await expectVisualSnapshot(page, 'model-settings-1920x1080.png')

  await page.goto('/project/p1?view=governance', { waitUntil: 'networkidle' })
  await expect(page.getByText('还没有可治理的叙事记录')).toBeVisible()
  await expectViewportSafe(page)
  await expectVisualSnapshot(page, 'governance-empty-1920x1080.png')
  await expectNoSeriousAccessibilityViolations(page)
})

test('keeps the workspace global-model control usable in the compact assistant panel', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 600 })
  await mockUiApi(page)
  await page.goto('/project/p1', { waitUntil: 'networkidle' })
  await page.getByRole('button', { name: '展开项目助手' }).click()

  await expect(page.getByRole('combobox', { name: '全局模型' })).toBeVisible()
  await expect(page.locator('.workspace-assistant-model-select .ant-select-selection-item')).toContainText('opencode')
  await expect(page.getByRole('button', { name: '管理模型' })).toBeVisible()
  await expectViewportSafe(page)
  await expectVisualSnapshot(page, 'assistant-global-model-800x600.png')
})

const creationViewports = [
  { name: '1920x1080', width: 1920, height: 1080 },
  { name: '800x600', width: 800, height: 600 },
]

for (const viewport of creationViewports) {
  test(`restores project-scoped assistant history at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await mockUiApi(page)
    await page.addInitScript(() => {
      localStorage.setItem('siming.gui.assistant.projectId', 'p1')
    })
    await page.goto('/gui', { waitUntil: 'networkidle' })

    await expect(page.getByText(/\u4f5c\u54c1\u4e0a\u4e0b\u6587/)).toBeVisible()
    await expect(page.getByText(/\u4e3b\u89d2\u52a8\u673a\u8c03\u6574/).first()).toBeVisible()
    await expect(page.getByText(/\u5df2\u8c03\u6574\u4e3b\u89d2\u52a8\u673a/)).toBeVisible()
    await expectViewportSafe(page)
    await expectNoSeriousAccessibilityViolations(page)
    await expectVisualSnapshot(page, `assistant-project-history-${viewport.name}.png`)
  })

  test(`keeps immutable artifact history reviewable at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await mockUiApi(page)
    await page.goto('/gui?creationSession=running-creation', { waitUntil: 'networkidle' })

    if (viewport.width === 800) {
      await page.getByRole('button', { name: '作品资料' }).click()
    }
    await page.locator('button[aria-label$="\u7248\u672c\u5386\u53f2"]').first().click()
    await expect(page.getByRole('dialog', { name: /\u7248\u672c\u5386\u53f2/ })).toBeVisible()
    await expect(page.getByText('/genre')).toBeVisible()
    await expect(page.getByText(/\u539f\uff1a\u4f20\u7edf\u4ed9\u4fa0/)).toBeVisible()
    await expect(page.getByText(/\u65b0\uff1a\u4ed9\u4fa0\u60ac\u7591/)).toBeVisible()
    await page.locator('.gui-chat-version-item').nth(1).click()
    await expect(page.getByRole('button', { name: /\u6062\u590d\u6b64\u7248\u672c/ })).toBeEnabled()
    await page.locator('.ant-modal-body').evaluate((element) => { element.scrollTop = 0 })
    await expectViewportSafe(page)
    await expectNoSeriousAccessibilityViolations(page)
    await expectVisualSnapshot(page, `assistant-artifact-history-${viewport.name}.png`)
  })

  test(`keeps the material import preview usable at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await mockUiApi(page)
    await page.goto('/gui?creationSession=running-creation&import=import-preview', { waitUntil: 'networkidle' })

    await expect(page.getByText('资料导入')).toBeVisible()
    await expect(page.getByRole('heading', { name: '仙侠悬疑八卷大纲.docx' })).toBeVisible()
    await expect(page.getByText('人物 12')).toBeVisible()
    await expect(page.getByText('卷纲 8')).toBeVisible()
    await expect(page.getByText('冲突 3')).toBeVisible()
    await page.getByRole('button', { name: '预览并选择导入' }).click()
    await expect(page.getByText('导入预览 · 仙侠悬疑八卷大纲.docx')).toBeVisible()
    await expect(page.getByText(/已处理 5\/5 个分块/)).toBeVisible()
    await expect(page.getByText(/文风与世界观 · 6 项/)).toBeVisible()
    await expect(page.getByText(/开篇细纲（需至少3章摘要） · 146 项/)).toBeVisible()
    await expect(page.getByText('发现 3 处可能冲突')).toBeVisible()
    await expect(page.getByRole('button', { name: '应用所选数据' })).toBeEnabled()
    await expectViewportSafe(page)
    await expectNoSeriousAccessibilityViolations(page)
    await expectVisualSnapshot(page, `assistant-import-preview-${viewport.name}.png`)
  })

  test(`keeps the conversational creation task controllable at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await mockUiApi(page)
    await page.goto('/gui?creationSession=running-creation', { waitUntil: 'networkidle' })

    if (viewport.width === 800) {
      await page.getByRole('button', { name: '作品资料' }).click()
    }
    await expect(page.getByText('立项任务')).toBeVisible()
    await expect(page.getByText('0 个错误 · 1 个提醒')).toBeVisible()
    await expect(page.getByRole('heading', { name: '主线与卷纲' })).toBeVisible()
    await expect(page.getByText('正在调整第 3—8 卷，并保留主角设定')).toBeVisible()
    await expect(page.getByText('模型：opencode_cli:opencode/deepseek-v4-flash-free')).toBeVisible()
    await expect(page.getByRole('complementary', { name: '作品资料' })).toBeVisible()
    await expect(page.getByText('上游阶段“主线与卷纲”已修改')).toBeVisible()
    await expect(page.getByRole('button', { name: '撤销开篇细纲最近一次修改' })).toBeVisible()
    await expect(page.getByText(/可先生成/)).toBeVisible()
    await expect(page.getByRole('button', { name: '停止' })).toBeEnabled()
    await expect(page.getByRole('button', { name: '打开完整编辑器' }).first()).toBeEnabled()
    await expectViewportSafe(page)
    await expectNoSeriousAccessibilityViolations(page)
    await expectVisualSnapshot(page, `assistant-creation-running-${viewport.name}.png`)
    if (viewport.width === 800) {
      await page.getByRole('button', { name: '撤销开篇细纲最近一次修改' }).scrollIntoViewIfNeeded()
      await expect(page.getByText(/可先生成/)).toBeVisible()
      await expectViewportSafe(page)
      await expectVisualSnapshot(page, 'assistant-creation-undo-800x600.png')
    }
  })

  if (viewport.width === 1920 || viewport.width === 800) {
    test(`keeps revision-conflict recovery explicit at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize(viewport)
      await mockUiApi(page, 'recovery')
      await page.goto('/gui?creationSession=recovery-creation', { waitUntil: 'networkidle' })

      await expect(page.getByText(/任务基于版本 7.*当前作者内容已更新到版本 9/)).toBeVisible()
      const restoreInput = page.getByRole('button', { name: '放回输入框重试' })
      await expect(restoreInput).toBeEnabled()
      if (viewport.width === 800) {
        await expectVisualSnapshot(page, 'assistant-creation-retry-800x600.png')
        await page.getByRole('button', { name: '作品资料' }).click()
      }
      await expect(page.getByText('版本冲突')).toBeVisible()
      await expect(page.getByText('旧任务结果未覆盖当前内容；候选稿已保留，可按原输入或最新内容重试')).toBeVisible()
      await expect(page.getByText('角色与关系已由作者修改')).toBeVisible()
      await expectViewportSafe(page)
      await expectNoSeriousAccessibilityViolations(page)
      await expectVisualSnapshot(page, `assistant-creation-conflict-${viewport.name}.png`)
      if (viewport.width === 800) {
        await page.getByRole('button', { name: '收起作品资料' }).click()
        await expect(restoreInput).toBeInViewport()
      }
    })
  }

  test.skip(`legacy standalone new-book entry is replaced by the unified assistant at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await mockUiApi(page)
    await page.goto('/novel-creation', { waitUntil: 'networkidle' })

    const authorLedEntry = page.getByRole('button', { name: /按我的设定立项/ })
    const exploreEntry = page.getByRole('button', { name: /帮我探索创意/ })
    const importEntry = page.getByRole('button', { name: /导入已有小说/ })
    await expect(authorLedEntry).toBeVisible()
    await expect(exploreEntry).toBeVisible()
    await expect(importEntry).toBeVisible()
    await expect(page.getByText('已有设定不会被随机方案覆盖。')).toBeVisible()
    await expectViewportSafe(page)
    await expectNoSeriousAccessibilityViolations(page)
    await expectFullPageVisualSnapshot(page, `creation-path-${viewport.name}.png`)

    await importEntry.click()
    await expect(page).toHaveURL(/\/dashboard\?create=import$/)
    await page.goBack({ waitUntil: 'networkidle' })
    await authorLedEntry.click()

    const briefInput = page.getByLabel('已有故事方案')
    const outlineInput = page.getByLabel(/已有大纲/)
    const lockedInput = page.getByLabel('不可改动的设定')
    await expect(briefInput).toBeVisible()
    await briefInput.fill(authorBrief)
    await outlineInput.fill(authorOutline)
    await lockedInput.fill(lockedRequirements.join('\n'))
    await expect(page.getByText('作者原始设定优先')).toBeVisible()
    await expect(page.getByRole('button', { name: '整理为作者方案' })).toBeEnabled()
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await expectViewportSafe(page)
    await expectFullPageVisualSnapshot(page, `creation-author-intake-${viewport.name}.png`)
  })

  test.skip(`legacy standalone author-facts editor is replaced by the unified assistant at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await mockUiApi(page)
    await page.goto('/novel-creation?session=author-workbench&run=run-failed&stage=world_style', { waitUntil: 'networkidle' })

    const authorSource = page.getByRole('region', { name: '作者原始设定' })
    await expect(authorSource).toContainText(authorBrief)
    await expect(authorSource).toContainText('主角姓名必须是周遥')
    await expect(page.getByText('作者原始设定持续生效')).toBeVisible()
    const refineButton = page.getByRole('button', { name: '让 AI 按要求调整' })
    await expect(refineButton).toBeEnabled()
    await refineButton.click()

    const refineDialog = page.getByRole('dialog', { name: '让 AI 调整：文风与世界观' })
    await expect(refineDialog).toBeVisible()
    await refineDialog.evaluate(async (element) => {
      await Promise.all(element.getAnimations({ subtree: true }).map((animation) => animation.finished.catch(() => undefined)))
    })
    await expect(refineDialog.getByText('只修改当前阶段')).toBeVisible()
    await refineDialog.getByPlaceholder(/例如：改成六卷结构/).fill('保留周遥和温室花展；将全书调整为六卷，但不要改变结局。')
    await expect(refineDialog.getByRole('button', { name: '按要求调整' })).toBeEnabled()
    await expectViewportSafe(page)
    await expectNoSeriousAccessibilityViolations(page)
    await expectVisualSnapshot(page, `creation-author-refine-${viewport.name}.png`)
    await refineDialog.getByRole('button', { name: /取\s*消/ }).click()

    const failedOutcome = page.getByText('本轮立项任务失败')
    await failedOutcome.scrollIntoViewIfNeeded()
    await expect(failedOutcome).toBeVisible()
    await expect(page.getByText('原阶段草稿未被覆盖')).toBeVisible()
    await expect(page.getByText('文风与世界观生成未完成')).toBeVisible()
    await expect(page.getByRole('button', { name: '重试“文风与世界观”' })).toBeVisible()
    await expectViewportSafe(page)
    await expectVisualSnapshot(page, `creation-author-failed-${viewport.name}.png`)
  })

  test.skip(`legacy standalone running-task view is replaced by the unified assistant at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await mockUiApi(page)
    await page.goto('/novel-creation?session=running-creation&run=run-running', { waitUntil: 'networkidle' })

    await expect(page.getByText('正在忠实整理作者方案...')).toBeVisible()
    await expect(page.getByText('实际模型：opencode_cli:opencode/deepseek-v4-flash-free')).toBeVisible()
    await expect(page.getByText('尝试次数：1')).toBeVisible()
    await expect(page.getByRole('button', { name: '新建立项' })).toBeDisabled()
    const cancelButton = page.getByRole('button', { name: '取消任务' })
    await expect(cancelButton).toBeEnabled()
    await expectViewportSafe(page)
    await expectVisualSnapshot(page, `creation-author-running-${viewport.name}.png`)

    const pauseRequest = page.waitForRequest((request) => (
      new URL(request.url()).pathname === '/api/v1/operations/operation-running/pause'
      && request.method() === 'POST'
    ))
    await page.getByRole('button', { name: /暂停/ }).click()
    await pauseRequest
    const pausedOutcome = page.getByText('本轮立项任务已暂停')
    await expect(pausedOutcome).toBeVisible()
    await expect(page.getByText('任务已暂停；检查点和已有草稿均已保留')).toBeVisible()
    await pausedOutcome.scrollIntoViewIfNeeded()
    await expectViewportSafe(page)
    await expectVisualSnapshot(page, `creation-author-paused-${viewport.name}.png`)

    const continueRequest = page.waitForRequest((request) => (
      new URL(request.url()).pathname === '/api/v1/operations/operation-running/continue'
      && request.method() === 'POST'
    ))
    await page.getByRole('button', { name: /继续任务/ }).click()
    await continueRequest
    await expect(page.getByText('正在从最近检查点继续')).toBeVisible()

    const cancelRequest = page.waitForRequest((request) => (
      new URL(request.url()).pathname === '/api/v1/operations/operation-running/cancel'
      && request.method() === 'POST'
    ))
    await cancelButton.click()
    await cancelRequest
    await expect(page.getByRole('button', { name: '正在取消' })).toBeDisabled()
    await expect(page.getByText('正在取消任务；已保存的草稿不会丢失...')).toBeVisible()
  })
}

test.describe('Windows reduced-motion compatibility', () => {
  test.use({ reducedMotion: 'reduce' })

  test('keeps the provider dropdown visible, positioned and selectable', async ({ page }) => {
    await page.setViewportSize({ width: 800, height: 600 })
    // Keep an explicit page-level emulation for system Chrome/WebView2 runs;
    // the describe-level setting covers Playwright's bundled Chromium.
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await mockUiApi(page)
    await page.goto('/settings', { waitUntil: 'networkidle' })

    expect(await page.evaluate(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true)
    await page.getByRole('button', { name: '添加配置' }).click()
    const dialog = page.getByRole('dialog', { name: '添加模型配置' })
    const provider = dialog.getByRole('combobox', { name: '提供商' })
    await provider.click()

    const dropdown = page.locator('.ant-select-dropdown:visible')
    await expect(dropdown).toBeVisible()
    const bounds = await dropdown.boundingBox()
    expect(bounds).not.toBeNull()
    expect(bounds!.x).toBeGreaterThanOrEqual(0)
    expect(bounds!.y).toBeGreaterThanOrEqual(0)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(800)
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(600)
    await expectVisualSnapshot(page, 'provider-dropdown-reduced-motion-800x600.png')

    await dropdown.locator('.ant-select-item-option-content', { hasText: 'OpenAI' }).click()
    await expect(dialog.locator('.ant-select-selection-item').first()).toHaveText('OpenAI')
  })
})
