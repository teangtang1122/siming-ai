from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing marker: {label}")
    return text.replace(old, new, 1)


def remove_between(text: str, start: str, end: str, label: str) -> str:
    a = text.find(start)
    if a < 0:
        raise RuntimeError(f"missing start marker: {label}")
    b = text.find(end, a)
    if b < 0:
        raise RuntimeError(f"missing end marker: {label}")
    return text[:a] + text[b:]


rel = "frontend/src/components/GuiAssistantChat.tsx"
text = read(rel)
old_import = '''import {
  defaultInterviewRuntime,
  startNovelCreationSession,
  type InterviewRuntime,
  type InterviewQuestion,
  type InterviewQuestionAnswer,
  useNovelCreationInterviewController,
} from '../hooks/useNovelCreationInterviewController'
import {
  extractNovelInterviewErrorDetail,
  formatSystemAssistantError,
  formatNovelInterviewError,
  isNovelInterviewRetryIntent,
  NOVEL_INTERVIEW_THINKING,
} from '../utils/novelInterview'
'''
new_import = '''import { startNovelCreationSession } from '../services/novelCreationAgent'
import {
  defaultCreationAgentRuntime,
  extractCreationAgentErrorDetail,
  formatSystemAssistantError,
  type CreationAgentRuntime,
} from '../utils/creationAgent'
'''
text = replace_once(text, old_import, new_import, "legacy interview imports")
text = text.replace(
    "type ChatQuestion = InterviewQuestion\ntype QuestionAnswer = InterviewQuestionAnswer\n",
    "interface ChatQuestion { question: string; purpose?: string; options?: string[]; type?: 'single_select' | 'multi_select' | 'text' }\n",
)

novel_draft_data = '''interface NovelDraftData {
  blueprints: NovelBlueprint[]
  recommendation?: string
  enhancement_mode?: 'instant_template' | 'template_llm_hybrid' | 'template_fallback' | 'llm_required'
  questions?: Array<{ question: string; purpose?: string; options?: string[] }>
  original_brief?: string
  hint?: string
}

'''
text = replace_once(text, novel_draft_data, "", "obsolete NovelDraftData")

for state_line in (
    "  const [selectedOption, setSelectedOption] = useState<string | null>(null)\n",
    "  const [showOtherInput, setShowOtherInput] = useState(false)\n",
    "  const [otherText, setOtherText] = useState('')\n",
    "  const [showQAEditor, setShowQAEditor] = useState(false)\n",
    "  const [editingAnswers, setEditingAnswers] = useState<Record<string, string>>({})\n",
):
    text = text.replace(state_line, "")
text = text.replace(
    "  const [agentRuntimeOverride, setAgentRuntimeOverride] = useState<Partial<InterviewRuntime>>({})\n",
    "  const [agentRuntimeOverride, setAgentRuntimeOverride] = useState<Partial<CreationAgentRuntime>>({})\n",
)
text = text.replace("  const interviewModelSource = selectedModelOverride\n", "  const creationAgentModelSource = selectedModelOverride\n")
text = text.replace("    modelSource: interviewModelSource,\n", "    modelSource: creationAgentModelSource,\n")

hook_start = "  const novelInterview = useNovelCreationInterviewController({\n"
hook_end = "\n  useEffect(() => {\n    systemConversationIdRef.current = systemConversationId\n"
a = text.find(hook_start)
if a < 0:
    raise RuntimeError("legacy interview hook block missing")
b = text.find(hook_end, a)
if b < 0:
    raise RuntimeError("legacy interview hook block end missing")
simple_state = '''  const [systemSessionId, setSystemSessionId] = useState<string>()
  const [systemBrief, setSystemBrief] = useState('')
  const adoptCreationSession = useCallback((sessionId: string, brief = '') => {
    setSystemSessionId(sessionId)
    if (brief) setSystemBrief(brief)
  }, [])
  const resetCreationSession = useCallback(() => {
    setSystemSessionId(undefined)
    setSystemBrief('')
  }, [])
'''
text = text[:a] + simple_state + text[b:]
text = text.replace("adoptNovelInterviewSession", "adoptCreationSession")
text = text.replace("resetNovelInterview", "resetCreationSession")

runtime_start = "  const interviewRuntime = {\n"
runtime_end = "  // Creative slots editor state\n"
a = text.find(runtime_start)
if a < 0:
    raise RuntimeError("legacy runtime block missing")
b = text.find(runtime_end, a)
if b < 0:
    raise RuntimeError("legacy runtime block end missing")
new_runtime = '''  const creationAgentRuntime = {
    ...defaultCreationAgentRuntime(selectedModel, creationAgentModelSource),
    ...agentRuntimeOverride,
  }
  const recordAgentRuntimeError = (error: unknown) => {
    const detail = extractCreationAgentErrorDetail(error)
    const runtime = detail.runtime && typeof detail.runtime === 'object'
      ? detail.runtime as Partial<CreationAgentRuntime>
      : {}
    const failureClass = String(detail.failure_class || runtime.failure_class || '')
    setAgentRuntimeOverride({
      ...runtime,
      quota_status: failureClass === 'quota_or_rate_limit'
        ? 'exhausted_or_limited'
        : runtime.quota_status,
      failure_class: failureClass || undefined,
      next_action: String(detail.next_action || runtime.next_action || '') || undefined,
    })
  }
  const runtimeSourceLabel: Record<string, string> = {
    conversation_override: '本次对话覆盖',
    global_default: '全局默认',
    task_setting: '任务设置',
    task_setting_fallback: '任务设置回退',
    unconfigured: '未配置',
    unknown: '待确认',
  }
  const runtimeQuotaLabel = creationAgentRuntime.quota_status === 'exhausted_or_limited'
    ? '额度：已耗尽或限流'
    : '额度：未检测'
  const runtimeToolModeLabel = isLocalCliModel
    ? '工具模式：受控本机 Agent 工具桥'
    : '工具模式：Creation Agent 原生工具调用'
'''
text = text[:a] + new_runtime + text[b:]
text = text.replace("interviewRuntime", "creationAgentRuntime")
text = text.replace("interviewModelSource", "creationAgentModelSource")

# The old interview UI is one contiguous control plane. Remove it as a unit;
# deleting only its state/imports leaves dangling handlers that pass Vitest but
# fail TypeScript compilation.
text = remove_between(
    text,
    "  // ── Single-question interactive flow ──\n",
    "  // ── Creative Slots Editor ──\n",
    "legacy single-question interview control plane",
)

# Remove the dead dynamic-interview fallback. A current creation session has
# already returned through /agent-turn before this point.
dead_start = "      if (!skipAutomaticCreation && shouldUseNovelCreation(displayText, Boolean(activeProjectId))) {\n"
dead_end = "      if (/作品|项目|列表|有哪些|查看/.test(text)) {\n"
if dead_start in text:
    text = remove_between(text, dead_start, dead_end, "dead dynamic interview fallback")

# Clean interview-only reset calls that may remain in the fresh-session path.
for line in (
    "      setSelectedOption(null)\n",
    "      setShowOtherInput(false)\n",
    "      setOtherText('')\n",
    "      setShowQAEditor(false)\n",
    "      setEditingAnswers({})\n",
    "      setCurrentOptions([])\n",
):
    text = text.replace(line, "")

# Blueprint cards remain useful, but the old Q&A editor controls belong to the
# deleted interview state machine. Rewrite the action group by structural
# boundaries instead of matching each legacy button verbatim.
text = replace_once(
    text,
    '      <div className="gui-chat-blueprints">\n        {renderQAEditor()}\n',
    '      <div className="gui-chat-blueprints">\n',
    "blueprint QA editor mount",
)
blueprint_start = text.find("  const renderBlueprintCards = () => {\n")
if blueprint_start < 0:
    raise RuntimeError("renderBlueprintCards marker missing")
a = text.find("          <Space size={8}>\n", blueprint_start)
if a < 0:
    raise RuntimeError("blueprint action group start missing")
after_actions = "          </Space>\n        </div>\n        <div className={`gui-chat-blueprint-grid"
b = text.find(after_actions, a)
if b < 0:
    raise RuntimeError("blueprint action group end missing")
new_actions = '''          <Space size={8}>
            <Button size="small" onClick={() => handleSystemAssistantMessage('全部重新生成')} disabled={streaming}>
              重新生成
            </Button>
            <Button size="small" onClick={() => handleSystemAssistantMessage('强化书名、主角动机和前三章钩子')} disabled={streaming}>
              强化方案
            </Button>
          </Space>
'''
text = text[:a] + new_actions + text[b + len("          </Space>\n"):]

# Questions may still exist in historical persisted payloads, but the current
# creation experience no longer renders interactive interview widgets.
text = re.sub(r"\n\s*\{msg\.questions && msg\.questions\.length > 0 && renderQuestions\(msg\.questions\)\}", "", text)
write(rel, text)

# Current helper imports no longer come from the deleted interview hook.
for rel in (
    "frontend/src/pages/GettingStartedPage.tsx",
    "frontend/src/pages/NovelCreationWizardPage.tsx",
):
    text = read(rel)
    text = text.replace("../hooks/useNovelCreationInterviewController", "../services/novelCreationAgent")
    write(rel, text)

for rel in (
    "frontend/src/hooks/useNovelCreationInterviewController.ts",
    "frontend/src/utils/novelInterview.ts",
    "frontend/src/__tests__/novelInterview.test.ts",
    "frontend/src/__tests__/useNovelCreationInterviewController.test.tsx",
):
    p = ROOT / rel
    if p.exists():
        p.unlink()

print("frontend legacy creation interview flow removed")
