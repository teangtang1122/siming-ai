import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Empty,
  Input,
  InputNumber,
  Progress,
  Select,
  Segmented,
  Space,
  Tag,
  Typography,
  message,
  Row,
  Col,
  Statistic,
  Spin,
} from 'antd'
import {
  ThunderboltOutlined,
  NodeIndexOutlined,
  RiseOutlined,
  FieldTimeOutlined,
  BulbOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import { apiClient } from '../api/client'
import { PersistentOutcome } from '../components/interaction'
import { useModelOptions } from '../hooks/useModelOptions'
import {
  type ApiResponse,
  type ChunkProgress,
  type DeconstructPageProps,
  type DeconstructReport,
  type PreviewData,
  type ReportSummary,
  PLOT_TYPE_LABEL,
  PLOT_TYPE_COLOR,
  IMPORTANCE_COLOR,
  INTENSITY_COLOR,
  phaseLabel,
  reportPercent,
  formatSeconds,
  deconstructReportOutcome,
} from './deconstruct'

const { Title, Text, Paragraph } = Typography
const { TextArea } = Input

function DeconstructPage({ projectId }: DeconstructPageProps) {
  const [text, setText] = useState('')
  const [sourceMode, setSourceMode] = useState<'chapters' | 'manual'>('chapters')
  const [selectedChapterIds, setSelectedChapterIds] = useState<string[]>([])
  const [analyzing, setAnalyzing] = useState(false)
  const [model, setModel] = useState<string | undefined>()
  const [includeGoldenThree, setIncludeGoldenThree] = useState(true)
  const [includeRhythm, setIncludeRhythm] = useState(true)
  const [includePatterns, setIncludePatterns] = useState(true)
  const [mapConcurrency, setMapConcurrency] = useState(4)
  const [preview, setPreview] = useState<PreviewData | null>(null)
  const [progressReport, setProgressReport] = useState<DeconstructReport | null>(null)
  const [result, setResult] = useState<DeconstructReport | null>(null)
  const [reports, setReports] = useState<ReportSummary[]>([])
  const [reduceStreamText, setReduceStreamText] = useState('')
  const [reduceStreaming, setReduceStreaming] = useState(false)
  const [streamDisconnected, setStreamDisconnected] = useState(false)
  const { modelOptions, defaultModel, loading: modelsLoading } = useModelOptions()

  const appendReduceStatus = (line: string) => {
    setReduceStreamText((prev) => {
      const next = prev ? `${prev}\n${line}` : line
      return next.length > 24000 ? next.slice(-24000) : next
    })
  }

  const fetchPreview = useCallback(async () => {
    try {
      const res = await apiClient.get<ApiResponse<PreviewData>>(`/projects/${projectId}/deconstruct/preview`)
      const data = res.data.data
      setPreview(data)
      setSelectedChapterIds((current) => (
        current.length > 0 ? current : data.chapters.map((chapter) => chapter.id)
      ))
      if (data.can_deconstruct && !text) {
        setText(data.combined_text)
      }
    } catch (err: any) {
      message.error(err.message || '获取预览失败')
    }
  }, [projectId, text])

  const fetchReports = useCallback(async () => {
    try {
      const res = await apiClient.get<ApiResponse<{ items: ReportSummary[]; total: number }>>(
        `/projects/${projectId}/deconstruct/reports`,
      )
      setReports(res.data.data.items || [])
    } catch {
      setReports([])
    }
  }, [projectId])

  const loadReport = useCallback(async (reportId: string, silent = false) => {
    try {
      const res = await apiClient.get<ApiResponse<DeconstructReport>>(`/projects/${projectId}/deconstruct/${reportId}`)
      const report = res.data.data
      setResult(report.status === 'completed' ? report : null)
      setProgressReport(report)
      setReduceStreamText('')
      setReduceStreaming(false)
      setStreamDisconnected(false)
      if (!silent) {
        message.success('已加载持久化拆书报告')
      }
    } catch (err: any) {
      message.error(err.message || '加载拆书报告失败')
    }
  }, [projectId])

  useEffect(() => {
    fetchPreview()
    fetchReports()
  }, [fetchPreview, fetchReports])

  useEffect(() => {
    if (result || progressReport || analyzing) return
    const latestCompleted = reports.find((report) => report.status === 'completed')
    if (latestCompleted) {
      loadReport(latestCompleted.id, true)
    }
  }, [reports, result, progressReport, analyzing, loadReport])

  useEffect(() => {
    if (!model && defaultModel) {
      setModel(defaultModel)
    }
  }, [model, defaultModel])

  useEffect(() => {
    const reportId = progressReport?.id
    if (!streamDisconnected || !reportId || ['completed', 'failed', 'cancelled'].includes(progressReport.status)) {
      return
    }
    let cancelled = false
    const poll = async () => {
      try {
        const res = await apiClient.get<ApiResponse<DeconstructReport>>(`/projects/${projectId}/deconstruct/${reportId}`)
        if (cancelled) return
        const report = res.data.data
        setProgressReport(report)
        if (report.status === 'completed') {
          setResult(report)
          setAnalyzing(false)
          setStreamDisconnected(false)
          void fetchReports()
        } else if (report.status === 'failed' || report.status === 'cancelled') {
          setAnalyzing(false)
          setStreamDisconnected(false)
        }
      } catch {
        // Keep polling. A broken page connection must not be treated as a failed backend task.
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 3000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [fetchReports, progressReport?.id, progressReport?.status, projectId, streamDisconnected])


  const selectedChapters = useMemo(() => {
    const ids = new Set(selectedChapterIds)
    return (preview?.chapters || []).filter((chapter) => ids.has(chapter.id))
  }, [preview?.chapters, selectedChapterIds])

  const selectedWords = useMemo(
    () => selectedChapters.reduce((sum, chapter) => sum + (chapter.word_count || 0), 0),
    [selectedChapters],
  )

  const activeWordCount = sourceMode === 'chapters' ? selectedWords : text.length
  const estimatedChunks = Math.max(0, Math.ceil(activeWordCount / 2400))
  const finalResult = result?.status === 'completed' ? result : null
  const plotTypeTimeline = useMemo(
    () => [...(finalResult?.plot_nodes || [])].sort((a, b) => a.position_pct - b.position_pct),
    [finalResult?.plot_nodes],
  )
  const chunkResults = useMemo(
    () => [...(progressReport?.chunk_results || [])].sort((a, b) => a.index - b.index),
    [progressReport?.chunk_results],
  )
  const firstThreeChapterNames = useMemo(
    () => (preview?.chapters || []).slice(0, 3).map((chapter, index) => `${index + 1}. ${chapter.title}`),
    [preview?.chapters],
  )
  const reportOutcome = useMemo(() => deconstructReportOutcome(progressReport), [progressReport])

  const handleAnalyze = async () => {
    if (sourceMode === 'chapters' && selectedChapterIds.length === 0) {
      message.warning('请选择要拆书的章节')
      return
    }
    if (sourceMode === 'manual' && (!text.trim() || text.trim().length < 100)) {
      message.warning('请粘贴至少100个字符的小说文本')
      return
    }

    setAnalyzing(true)
    setResult(null)
    setProgressReport(null)
    setReduceStreamText('')
    setReduceStreaming(false)
    setStreamDisconnected(false)

    const payload = sourceMode === 'chapters'
      ? {
          chapter_ids: selectedChapterIds,
          model: model || defaultModel || undefined,
          analysis_mode: 'fast',
          include_golden_three: includeGoldenThree,
          include_characters: false,
          include_outline: false,
          include_worldbuilding: false,
          include_rhythm: includeRhythm,
          include_patterns: includePatterns,
          map_concurrency: mapConcurrency,
        }
      : {
          text: text.trim(),
          model: model || defaultModel || undefined,
          analysis_mode: 'fast',
          include_golden_three: includeGoldenThree,
          include_characters: false,
          include_outline: false,
          include_worldbuilding: false,
          include_rhythm: includeRhythm,
          include_patterns: includePatterns,
          map_concurrency: mapConcurrency,
        }

    let activeReportId: string | undefined
    apiClient.stream(
      `/projects/${projectId}/deconstruct/stream`,
      payload,
      (raw: string) => {
        try {
          const event = JSON.parse(raw)
          switch (event.type) {
            case 'init':
              activeReportId = event.report_id
              setProgressReport({
                id: event.report_id,
                title: event.title,
                status: 'processing',
                phase: 'queued',
                total_chunks: event.total_chunks,
                completed_chunks: 0,
                failed_chunks: 0,
                map_concurrency: event.map_concurrency,
                map_model: undefined,
                reduce_model: undefined,
                analysis_mode: 'fast',
                total_words: event.total_words,
                chunk_results: Array.from({ length: event.total_chunks }, (_, i) => ({
                  index: i, status: 'pending' as const, summary: '', characters: [], events: [], highlights: [],
                })),
                logs: [],
                created_at: new Date().toISOString(),
              })
              break

            case 'map_start':
              setProgressReport((prev) => prev ? { ...prev, phase: 'map', map_concurrency: event.map_concurrency } : null)
              break

            case 'map_chunk_start':
              setProgressReport((prev) => {
                if (!prev) return null
                const next = [...(prev.chunk_results || [])]
                const idx = next.findIndex((c) => c.index === event.index)
                const current: ChunkProgress = idx >= 0 ? next[idx] : { index: event.index, status: 'pending' }
                const entry: ChunkProgress = {
                  ...current,
                  index: event.index,
                  status: 'streaming',
                  streaming: true,
                  summary: current.summary || '模型正在流式输出...',
                }
                if (idx >= 0) next[idx] = entry
                else next.push(entry)
                return { ...prev, chunk_results: next }
              })
              break

            case 'map_token':
              setProgressReport((prev) => {
                if (!prev) return null
                const next = [...(prev.chunk_results || [])]
                const idx = next.findIndex((c) => c.index === event.index)
                const current: ChunkProgress = idx >= 0 ? next[idx] : { index: event.index, status: 'streaming' }
                const streamText = `${current.stream_text || ''}${event.content || ''}`
                const entry: ChunkProgress = {
                  ...current,
                  index: event.index,
                  status: 'streaming',
                  streaming: true,
                  summary: current.summary || '模型正在流式输出...',
                  stream_text: streamText.length > 24000 ? streamText.slice(-24000) : streamText,
                }
                if (idx >= 0) next[idx] = entry
                else next.push(entry)
                return { ...prev, chunk_results: next }
              })
              break

            case 'map_retry':
              setProgressReport((prev) => {
                if (!prev) return null
                const next = [...(prev.chunk_results || [])]
                const idx = next.findIndex((c) => c.index === event.index)
                const current: ChunkProgress = idx >= 0 ? next[idx] : { index: event.index, status: 'streaming' }
                const retryText = `\n\n[系统：第 ${event.attempt || 2} 次输出，上一轮 ${event.error || 'parse_failed'}，正在要求模型返回更短的完整JSON]\n\n`
                const entry: ChunkProgress = {
                  ...current,
                  index: event.index,
                  status: 'streaming',
                  streaming: true,
                  summary: '模型输出解析失败，正在自动重试...',
                  stream_text: `${current.stream_text || ''}${retryText}`,
                }
                if (idx >= 0) next[idx] = entry
                else next.push(entry)
                return { ...prev, chunk_results: next }
              })
              break

            case 'map_repair_start':
              setProgressReport((prev) => {
                if (!prev) return null
                const next = [...(prev.chunk_results || [])]
                const idx = next.findIndex((c) => c.index === event.index)
                const current: ChunkProgress = idx >= 0 ? next[idx] : { index: event.index, status: 'streaming' }
                const repairText = `\n\n[系统：检测到 ${event.error || 'parse_failed'}，正在用短请求修复JSON格式，不重新拆文本]\n\n`
                const entry: ChunkProgress = {
                  ...current,
                  index: event.index,
                  status: 'streaming',
                  streaming: true,
                  summary: '正在修复JSON格式...',
                  stream_text: `${current.stream_text || ''}${repairText}`,
                }
                if (idx >= 0) next[idx] = entry
                else next.push(entry)
                return { ...prev, chunk_results: next }
              })
              break

            case 'map_repair_done':
              setProgressReport((prev) => {
                if (!prev) return null
                const next = [...(prev.chunk_results || [])]
                const idx = next.findIndex((c) => c.index === event.index)
                const current: ChunkProgress = idx >= 0 ? next[idx] : { index: event.index, status: 'streaming' }
                const repairText = event.status === 'success'
                  ? '\n[系统：JSON修复成功]\n'
                  : `\n[系统：JSON修复失败：${event.error || 'repair_failed'}]\n`
                const entry: ChunkProgress = {
                  ...current,
                  index: event.index,
                  stream_text: `${current.stream_text || ''}${repairText}`,
                }
                if (idx >= 0) next[idx] = entry
                else next.push(entry)
                return { ...prev, chunk_results: next }
              })
              break

            case 'map_chunk':
              setProgressReport((prev) => {
                if (!prev) return null
                const next = [...(prev.chunk_results || [])]
                const current = next.find((c) => c.index === event.index)
                const entry: ChunkProgress = {
                  index: event.index,
                  status: event.status,
                  summary: event.summary,
                  characters: event.characters,
                  events: event.events,
                  highlights: event.highlights,
                  pacing: event.pacing,
                  narrative_mode: event.narrative_mode,
                  error: event.error,
                  raw: event.raw,
                  streaming: false,
                  stream_text: current?.stream_text,
                }
                const idx = next.findIndex((c) => c.index === event.index)
                if (idx >= 0) next[idx] = entry
                else next.push(entry)
                return { ...prev, chunk_results: next }
              })
              break

            case 'map_progress':
              setProgressReport((prev) => prev ? {
                ...prev,
                completed_chunks: event.completed,
                failed_chunks: event.failed,
                elapsed_seconds: event.elapsed_seconds,
                avg_seconds_per_chunk: event.avg_seconds_per_chunk,
                estimated_remaining_seconds: event.estimated_remaining_seconds,
              } : null)
              break

            case 'map_complete':
              setProgressReport((prev) => prev ? {
                ...prev,
                phase: 'reduce',
                completed_chunks: event.completed,
                failed_chunks: event.failed,
                elapsed_seconds: event.elapsed_seconds,
              } : null)
              break

            case 'reduce_start':
              setReduceStreaming(true)
              appendReduceStatus('正在启动自动合并...')
              break

            case 'reduce_section_start':
              setReduceStreaming(true)
              appendReduceStatus(`[合并] 开始：${event.label || event.section}`)
              break

            case 'reduce_section_complete':
              appendReduceStatus(`[合并] 完成：${event.label || event.section}`)
              break

            case 'reduce_section_error':
              appendReduceStatus(`[合并] 失败：${event.label || event.section}：${event.error || '未知错误'}`)
              break

            case 'reduce_token':
              setReduceStreamText((prev) => prev + event.content)
              break

            case 'reduce_complete': {
              setReduceStreaming(false)
              setStreamDisconnected(false)
              const report = event as DeconstructReport
              setResult(report)
              setProgressReport(report)
              setAnalyzing(false)
              fetchReports()
              break
            }

            case 'reduce_error':
              setAnalyzing(false)
              setReduceStreaming(false)
              setProgressReport((prev) => prev ? {
                ...prev,
                status: 'failed',
                phase: 'failed',
                reduce_error: event.message || '合并阶段出错',
              } : prev)
              break

            case 'error':
              setAnalyzing(false)
              setReduceStreaming(false)
              setProgressReport((prev) => prev ? {
                ...prev,
                status: 'failed',
                phase: 'failed',
                error: event.message || '拆书任务失败',
              } : prev)
              break

            case 'done':
              break
          }
        } catch { /* ignore parse errors for partial chunks */ }
      },
      (err) => {
        setReduceStreaming(false)
        if (activeReportId) {
          setStreamDisconnected(true)
        } else {
          setAnalyzing(false)
          message.error(err.message || '拆书任务连接失败')
        }
      },
    )
  }

  const handleRerunFailed = async () => {
    const report = finalResult || progressReport
    if (!report?.id || !report.failed_chunks) {
      message.info('没有需要重跑的失败分块')
      return
    }

    setAnalyzing(true)
    setReduceStreamText('')
    setReduceStreaming(false)
    setStreamDisconnected(false)
    const payload = {
      model: model || defaultModel || undefined,
      analysis_mode: 'fast',
      include_golden_three: includeGoldenThree,
      include_characters: false,
      include_outline: false,
      include_worldbuilding: false,
      include_rhythm: includeRhythm,
      include_patterns: includePatterns,
      map_concurrency: mapConcurrency,
    }

    apiClient.stream(
      `/projects/${projectId}/deconstruct/${report.id}/rerun-failed/stream`,
      payload,
      (raw: string) => {
        try {
          const event = JSON.parse(raw)
          switch (event.type) {
            case 'init':
              setProgressReport((prev) => prev ? {
                ...prev,
                status: 'processing',
                phase: 'map',
                map_concurrency: event.map_concurrency,
                map_model: undefined,
                reduce_model: undefined,
                analysis_mode: 'fast',
              } : null)
              break
            case 'map_chunk_start':
              setProgressReport((prev) => {
                if (!prev) return null
                const next = [...(prev.chunk_results || [])]
                const idx = next.findIndex((c) => c.index === event.index)
                const current: ChunkProgress = idx >= 0 ? next[idx] : { index: event.index, status: 'pending' }
                const entry: ChunkProgress = {
                  ...current,
                  status: 'streaming',
                  streaming: true,
                  stream_text: '',
                  summary: '正在重跑失败分块...',
                }
                if (idx >= 0) next[idx] = entry
                else next.push(entry)
                return { ...prev, chunk_results: next }
              })
              break
            case 'map_token':
              setProgressReport((prev) => {
                if (!prev) return null
                const next = [...(prev.chunk_results || [])]
                const idx = next.findIndex((c) => c.index === event.index)
                const current: ChunkProgress = idx >= 0 ? next[idx] : { index: event.index, status: 'streaming' }
                const streamText = `${current.stream_text || ''}${event.content || ''}`
                const entry: ChunkProgress = {
                  ...current,
                  status: 'streaming',
                  streaming: true,
                  stream_text: streamText.length > 24000 ? streamText.slice(-24000) : streamText,
                }
                if (idx >= 0) next[idx] = entry
                else next.push(entry)
                return { ...prev, chunk_results: next }
              })
              break
            case 'map_repair_start':
              setProgressReport((prev) => {
                if (!prev) return null
                const next = [...(prev.chunk_results || [])]
                const idx = next.findIndex((c) => c.index === event.index)
                const current: ChunkProgress = idx >= 0 ? next[idx] : { index: event.index, status: 'streaming' }
                const entry: ChunkProgress = {
                  ...current,
                  status: 'streaming',
                  streaming: true,
                  summary: '正在修复JSON格式...',
                  stream_text: `${current.stream_text || ''}\n[系统：正在修复JSON格式]\n`,
                }
                if (idx >= 0) next[idx] = entry
                else next.push(entry)
                return { ...prev, chunk_results: next }
              })
              break
            case 'map_chunk':
              setProgressReport((prev) => {
                if (!prev) return null
                const next = [...(prev.chunk_results || [])]
                const current = next.find((c) => c.index === event.index)
                const entry: ChunkProgress = {
                  index: event.index,
                  status: event.status,
                  summary: event.summary,
                  characters: event.characters,
                  events: event.events,
                  highlights: event.highlights,
                  pacing: event.pacing,
                  narrative_mode: event.narrative_mode,
                  error: event.error,
                  raw: event.raw,
                  streaming: false,
                  stream_text: current?.stream_text,
                }
                const idx = next.findIndex((c) => c.index === event.index)
                if (idx >= 0) next[idx] = entry
                else next.push(entry)
                return { ...prev, chunk_results: next }
              })
              break
            case 'map_progress':
              setProgressReport((prev) => prev ? {
                ...prev,
                completed_chunks: event.completed,
                failed_chunks: event.failed,
                elapsed_seconds: event.elapsed_seconds,
                avg_seconds_per_chunk: event.avg_seconds_per_chunk,
                estimated_remaining_seconds: event.estimated_remaining_seconds,
              } : null)
              break
            case 'map_complete':
              setProgressReport((prev) => prev ? {
                ...prev,
                phase: 'reduce',
                completed_chunks: event.completed,
                failed_chunks: event.failed,
                elapsed_seconds: event.elapsed_seconds,
              } : null)
              break
            case 'reduce_start':
              setReduceStreaming(true)
              appendReduceStatus('正在启动自动合并...')
              break

            case 'reduce_section_start':
              setReduceStreaming(true)
              appendReduceStatus(`[合并] 开始：${event.label || event.section}`)
              break

            case 'reduce_section_complete':
              appendReduceStatus(`[合并] 完成：${event.label || event.section}`)
              break

            case 'reduce_section_error':
              appendReduceStatus(`[合并] 失败：${event.label || event.section}：${event.error || '未知错误'}`)
              break

            case 'reduce_token':
              setReduceStreamText((prev) => prev + event.content)
              break
            case 'reduce_complete': {
              setReduceStreaming(false)
              setStreamDisconnected(false)
              const nextReport = event as DeconstructReport
              setResult(nextReport)
              setProgressReport(nextReport)
              setAnalyzing(false)
              fetchReports()
              break
            }
            case 'reduce_error':
            case 'error':
              setAnalyzing(false)
              setReduceStreaming(false)
              setProgressReport((prev) => prev ? {
                ...prev,
                status: 'failed',
                phase: 'failed',
                error: event.message || '重跑失败块失败',
              } : prev)
              break
            case 'done':
              break
          }
        } catch { /* ignore */ }
      },
      () => {
        setReduceStreaming(false)
        setStreamDisconnected(true)
      },
    )
  }

  const setAllChapters = () => {
    setSelectedChapterIds((preview?.chapters || []).map((chapter) => chapter.id))
  }

  const toggleChapter = (chapterId: string, checked: boolean) => {
    setSelectedChapterIds((current) => (
      checked
        ? Array.from(new Set([...current, chapterId]))
        : current.filter((id) => id !== chapterId)
    ))
  }

  return (
    <div style={{ padding: 16, maxWidth: 1320, margin: '0 auto' }}>
      <Title level={4} style={{ marginTop: 0 }}>
        <ThunderboltOutlined /> 拆书分析
      </Title>

      {reports.length > 0 && (
        <Card title="已持久化拆书报告" size="small" style={{ marginBottom: 16 }}>
          <Space wrap>
            {reports.slice(0, 6).map((report) => (
              <Button key={report.id} size="small" onClick={() => loadReport(report.id)}>
                {report.status === 'completed' ? '已完成' : phaseLabel(report.phase)} · {(report.total_words || 0).toLocaleString()}字 · {new Date(report.created_at || '').toLocaleString('zh-CN')}
              </Button>
            ))}
          </Space>
        </Card>
      )}

      {preview && (
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="已有章节" value={preview.total_chapters} suffix="章" />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="总字数" value={preview.total_words.toLocaleString()} suffix="字" />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic
                title={sourceMode === 'chapters' ? '选中字数' : '文本长度'}
                value={activeWordCount.toLocaleString()}
                suffix="字"
              />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small">
              <Statistic title="预计分析块数" value={estimatedChunks} suffix="块" />
            </Card>
          </Col>
        </Row>
      )}

      <Row gutter={16} align="top">
        <Col xs={24} xl={15}>
          <Card title="分析范围" style={{ marginBottom: 16 }}>
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              <Segmented
                value={sourceMode}
                onChange={(value) => setSourceMode(value as 'chapters' | 'manual')}
                options={[
                  { value: 'chapters', label: '选择章节' },
                  { value: 'manual', label: '粘贴文本' },
                ]}
              />

              {sourceMode === 'chapters' ? (
                <>
                  <Space wrap>
                    <Tag color="blue">已选 {selectedChapterIds.length} 章</Tag>
                    <Tag>{selectedWords.toLocaleString()} 字</Tag>
                    <Button size="small" onClick={setAllChapters}>全选</Button>
                    <Button size="small" onClick={() => setSelectedChapterIds([])}>清空</Button>
                  </Space>
                  <div style={{ maxHeight: 360, overflow: 'auto', border: '1px solid #f0f0f0', borderRadius: 6 }}>
                    {(preview?.chapters || []).map((chapter, index) => (
                      <div
                        key={chapter.id}
                        style={{
                          display: 'flex',
                          gap: 10,
                          padding: '8px 12px',
                          borderBottom: index === (preview?.chapters.length || 0) - 1 ? 'none' : '1px solid #f5f5f5',
                        }}
                      >
                        <Checkbox
                          checked={selectedChapterIds.includes(chapter.id)}
                          onChange={(event) => toggleChapter(chapter.id, event.target.checked)}
                        />
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <Space size={6} wrap>
                            <Text strong>{index + 1}. {chapter.title}</Text>
                            <Tag>{(chapter.word_count || 0).toLocaleString()} 字</Tag>
                          </Space>
                          {chapter.preview && (
                            <Paragraph ellipsis={{ rows: 1 }} type="secondary" style={{ marginBottom: 0, fontSize: 12 }}>
                              {chapter.preview}
                            </Paragraph>
                          )}
                        </div>
                      </div>
                    ))}
                    {(!preview || preview.chapters.length === 0) && (
                      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无章节" style={{ padding: 24 }} />
                    )}
                  </div>
                </>
              ) : (
                <TextArea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder="粘贴小说全文或片段到这里..."
                  autoSize={{ minRows: 10, maxRows: 18 }}
                  showCount
                />
              )}
            </Space>
          </Card>

          <Card title="分析设置" style={{ marginBottom: 16 }}>
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              <Space wrap>
                <Select
                  value={model}
                  onChange={setModel}
                  allowClear
                  showSearch
                  loading={modelsLoading}
                  optionFilterProp="label"
                  placeholder={modelOptions.length ? '选择AI模型/API' : '请先在系统设置配置模型'}
                  notFoundContent={modelsLoading ? '加载模型中...' : '暂无已配置模型'}
                  style={{ width: 260 }}
                  options={modelOptions}
                />
                <Space size={6}>
                  <Text>并发块数</Text>
                  <InputNumber
                    min={1}
                    max={12}
                    value={mapConcurrency}
                    disabled={analyzing}
                    onChange={(value) => setMapConcurrency(Number(value || 1))}
                    style={{ width: 92 }}
                  />
                </Space>
                <Button
                  type="primary"
                  icon={<ThunderboltOutlined />}
                  loading={analyzing}
                  onClick={handleAnalyze}
                  size="large"
                >
                  开始拆书并自动合并
                </Button>
              </Space>
              <Alert
                type="info"
                showIcon
                message="拆书默认使用快速方案"
                description="分块阶段只提取极简读法事实卡片，失败时优先修复JSON；自动合并阶段只生成黄金三章、情节高光、节奏曲线和写作模式。角色、大纲、世界观请使用作品建档。"
              />
              <div>
                <Text strong style={{ display: 'block', marginBottom: 8 }}>拆书内容</Text>
                <Space wrap>
                  <Checkbox checked={includeGoldenThree} onChange={(event) => setIncludeGoldenThree(event.target.checked)}>
                    黄金三章
                  </Checkbox>
                  <Checkbox checked={includeRhythm} onChange={(event) => setIncludeRhythm(event.target.checked)}>
                    节奏曲线
                  </Checkbox>
                  <Checkbox checked={includePatterns} onChange={(event) => setIncludePatterns(event.target.checked)}>
                    写作模式
                  </Checkbox>
                </Space>
                {includeGoldenThree && sourceMode === 'chapters' && (
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginTop: 10 }}
                    message="黄金三章只读取作品前3章"
                    description={firstThreeChapterNames.length > 0 ? firstThreeChapterNames.join(' / ') : '当前作品还没有前三章可供分析'}
                  />
                )}
              </div>
            </Space>
          </Card>
        </Col>

        <Col xs={24} xl={9}>
          <Card
            title={<span><SyncOutlined spin={analyzing} /> 实时拆书输出</span>}
            style={{ marginBottom: 16, position: 'sticky', top: 12 }}
          >
            {progressReport ? (
              <Space direction="vertical" style={{ width: '100%' }} size={12}>
                {streamDisconnected && (
                  <Alert
                    type="info"
                    showIcon
                    role="status"
                    message="进度连接正在恢复"
                    description="后台拆书任务不会因此停止，页面正在轮询已保存的分块和检查点。"
                  />
                )}
                {reportOutcome && (
                  <PersistentOutcome
                    outcome={reportOutcome.outcome}
                    title={reportOutcome.title}
                    result={reportOutcome.result}
                  />
                )}
                <div>
                  <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Text strong>{phaseLabel(progressReport.phase)}</Text>
                    <Tag color={progressReport.status === 'failed' ? 'red' : progressReport.status === 'completed' ? 'green' : 'blue'}>
                      {progressReport.status}
                    </Tag>
                  </Space>
                  <Progress
                    percent={reportPercent(progressReport)}
                    status={progressReport.status === 'failed' ? 'exception' : progressReport.status === 'completed' ? 'success' : 'active'}
                  />
                  <Space size={6} wrap>
                    <Tag>{progressReport.completed_chunks}/{progressReport.total_chunks} 块完成</Tag>
                    {progressReport.failed_chunks > 0 && <Tag color="orange">{progressReport.failed_chunks} 块解析失败</Tag>}
                    {progressReport.map_concurrency ? <Tag>并发 {progressReport.map_concurrency}</Tag> : null}
                    <Tag>{(progressReport.total_words || 0).toLocaleString()} 字</Tag>
                    <Tag>已用 {formatSeconds(progressReport.elapsed_seconds)}</Tag>
                    {progressReport.avg_seconds_per_chunk ? (
                      <Tag>均速 {progressReport.avg_seconds_per_chunk} 秒/块</Tag>
                    ) : null}
                    {progressReport.estimated_remaining_seconds ? (
                      <Tag>预计剩余 {formatSeconds(progressReport.estimated_remaining_seconds)}</Tag>
                    ) : null}
                    {progressReport.failed_chunks > 0 && !analyzing && (
                      <Button size="small" onClick={handleRerunFailed}>
                        重跑失败块
                      </Button>
                    )}
                  </Space>
                </div>

                {(progressReport.phase === 'reduce' || reduceStreaming) && (
                  <Alert
                    type="info"
                    showIcon
                    message="正在自动合并分块结果，生成读法分析报告"
                    description={
                      reduceStreamText ? (
                        <pre style={{
                          maxHeight: 200,
                          overflow: 'auto',
                          fontSize: 11,
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-all',
                          background: '#f5f5f5',
                          padding: 8,
                          borderRadius: 4,
                          marginTop: 8,
                          marginBottom: 0,
                        }}>
                          {reduceStreamText}
                        </pre>
                      ) : undefined
                    }
                  />
                )}
                {progressReport.error && (
                  <Alert type="error" showIcon message={progressReport.error} />
                )}

                <div>
                  <Text strong>任务日志</Text>
                  <div style={{ marginTop: 6, maxHeight: 120, overflow: 'auto' }}>
                    {(progressReport.logs || []).slice(-8).map((log, index) => (
                      <div key={`${log.time}-${index}`} style={{ fontSize: 12, marginBottom: 4 }}>
                        <Tag color={log.level === 'error' ? 'red' : log.level === 'warning' ? 'orange' : 'blue'}>
                          {new Date(log.time).toLocaleTimeString('zh-CN')}
                        </Tag>
                        <Text>{log.message}</Text>
                      </div>
                    ))}
                  </div>
                </div>

                <div>
                  <Text strong>分块模型输出</Text>
                  <div style={{ marginTop: 8, maxHeight: 520, overflow: 'auto' }}>
                    {chunkResults.length === 0 ? (
                      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="等待模型输出" />
                    ) : (
                      chunkResults.map((chunk) => (
                        <div
                          key={chunk.index}
                          style={{
                            padding: 10,
                            border: '1px solid #f0f0f0',
                            borderRadius: 6,
                            marginBottom: 8,
                            background: chunk.status === 'pending' ? '#fafafa' : '#fff',
                          }}
                        >
                          <Space size={6} wrap style={{ marginBottom: 6 }}>
                            <Tag color={chunk.status === 'completed' ? 'green' : chunk.status === 'failed' ? 'red' : chunk.status === 'streaming' ? 'blue' : 'default'}>
                              第 {chunk.index + 1} 块
                            </Tag>
                            {chunk.streaming && <Tag color="processing">流式输出中</Tag>}
                            {chunk.pacing && <Tag>{chunk.pacing}</Tag>}
                            {chunk.narrative_mode && <Tag>{chunk.narrative_mode}</Tag>}
                          </Space>
                          {chunk.summary ? (
                            <Paragraph style={{ marginBottom: 6, fontSize: 12 }}>{chunk.summary}</Paragraph>
                          ) : (
                            <Text type="secondary" style={{ fontSize: 12 }}>等待分析...</Text>
                          )}
                          {(chunk.characters || []).length > 0 && (
                            <div style={{ marginBottom: 4 }}>
                              <Text type="secondary" style={{ fontSize: 12 }}>角色：</Text>
                              {(chunk.characters || []).map((name) => <Tag key={name}>{name}</Tag>)}
                            </div>
                          )}
                          {(chunk.events || []).length > 0 && (
                            <div style={{ marginBottom: 4 }}>
                              <Text type="secondary" style={{ fontSize: 12 }}>事件：</Text>
                              {(chunk.events || []).slice(0, 3).map((event, index) => (
                                <Paragraph key={`${event}-${index}`} style={{ marginBottom: 2, fontSize: 12 }}>
                                  {event}
                                </Paragraph>
                              ))}
                            </div>
                          )}
                          {chunk.stream_text && (
                            <details open={chunk.streaming}>
                              <summary style={{ cursor: 'pointer', fontSize: 12, color: '#1677ff' }}>
                                {chunk.streaming ? '实时原文输出' : '模型原文输出'}
                              </summary>
                              <pre style={{
                                maxHeight: 220,
                                overflow: 'auto',
                                fontSize: 11,
                                whiteSpace: 'pre-wrap',
                                wordBreak: 'break-all',
                                background: '#fafafa',
                                padding: 8,
                              }}>
                                {chunk.stream_text}
                              </pre>
                            </details>
                          )}
                          {chunk.raw && (
                            <details>
                              <summary style={{ cursor: 'pointer', fontSize: 12, color: '#1677ff' }}>原始JSON</summary>
                              <pre style={{ maxHeight: 180, overflow: 'auto', fontSize: 11, background: '#fafafa', padding: 8 }}>
                                {JSON.stringify(chunk.raw, null, 2)}
                              </pre>
                            </details>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </Space>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="开始拆书后，这里会实时显示进度和模型输出" />
            )}
          </Card>
        </Col>
      </Row>

      {analyzing && !finalResult && (
        <Card style={{ marginBottom: 16, textAlign: 'center' }}>
          <Spin size="large" />
          <Paragraph style={{ marginTop: 12 }}>拆书任务正在后台运行，可以在右侧查看每个分块的模型输出。</Paragraph>
          <Progress percent={reportPercent(progressReport)} status="active" showInfo={false} />
        </Card>
      )}

      {finalResult && (
        <>
          {finalResult.golden_three && (
            <Card title="黄金三章分析" style={{ marginBottom: 16 }}>
              <Row gutter={[12, 12]}>
                <Col xs={24} md={12}>
                  <Text type="secondary">开篇钩子</Text>
                  <Paragraph>{finalResult.golden_three.hook || '未提取'}</Paragraph>
                </Col>
                <Col xs={24} md={12}>
                  <Text type="secondary">主角目标</Text>
                  <Paragraph>{finalResult.golden_three.protagonist_goal || '未提取'}</Paragraph>
                </Col>
                <Col xs={24} md={12}>
                  <Text type="secondary">核心冲突</Text>
                  <Paragraph>{finalResult.golden_three.core_conflict || '未提取'}</Paragraph>
                </Col>
                <Col xs={24} md={12}>
                  <Text type="secondary">读者期待</Text>
                  <Paragraph>{finalResult.golden_three.reader_expectation || '未提取'}</Paragraph>
                </Col>
              </Row>
              <Collapse
                size="small"
                items={[
                  {
                    key: 'functions',
                    label: '前三章功能',
                    children: (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        <Paragraph><Text strong>第一章：</Text>{finalResult.golden_three.chapter_1_function || '未提取'}</Paragraph>
                        <Paragraph><Text strong>第二章：</Text>{finalResult.golden_three.chapter_2_function || '未提取'}</Paragraph>
                        <Paragraph><Text strong>第三章：</Text>{finalResult.golden_three.chapter_3_function || '未提取'}</Paragraph>
                      </Space>
                    ),
                  },
                  {
                    key: 'suggestions',
                    label: '问题与优化建议',
                    children: (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        {(finalResult.golden_three.problems || []).map((item, index) => (
                          <Alert key={`problem-${index}`} type="warning" showIcon message={item} />
                        ))}
                        {(finalResult.golden_three.optimization_suggestions || []).map((item, index) => (
                          <Alert key={`suggestion-${index}`} type="info" showIcon message={item} />
                        ))}
                      </Space>
                    ),
                  },
                ]}
              />
            </Card>
          )}

          <Card title={<span><NodeIndexOutlined /> 情节节点时序</span>} style={{ marginBottom: 16 }}>
            {plotTypeTimeline.length > 0 ? (
              <div style={{ position: 'relative', padding: '10px 0' }}>
                <div style={{
                  position: 'relative', height: 50, background: '#f5f5f5', borderRadius: 25,
                  margin: '0 0 8px 0', overflow: 'visible',
                }}>
                  {plotTypeTimeline.map((node, index) => (
                    <div key={index} style={{
                      position: 'absolute',
                      left: `${node.position_pct}%`,
                      top: 0,
                      transform: 'translateX(-50%)',
                      textAlign: 'center',
                    }}>
                      <div style={{
                        width: 12, height: 12, borderRadius: '50%',
                        background: PLOT_TYPE_COLOR[node.type] || '#999',
                        margin: '0 auto 4px',
                      }} />
                      <Tag color={PLOT_TYPE_COLOR[node.type]} style={{ margin: 0, fontSize: 10 }}>
                        {PLOT_TYPE_LABEL[node.type] || node.type}
                      </Tag>
                    </div>
                  ))}
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#999' }}>
                  <span>0%</span><span>25%</span><span>50%</span><span>75%</span><span>100%</span>
                </div>
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未识别到情节节点" />
            )}
            {plotTypeTimeline.length > 0 && (
              <div style={{ marginTop: 12 }}>
                {plotTypeTimeline.map((node, index) => (
                  <div key={index} style={{ marginBottom: 6, padding: '6px 10px', background: '#fafafa', borderRadius: 4 }}>
                    <Space size={4}>
                      <Tag color={PLOT_TYPE_COLOR[node.type]}>{PLOT_TYPE_LABEL[node.type] || node.type}</Tag>
                      <Tag color={IMPORTANCE_COLOR[node.importance]}>{node.importance === 'high' ? '关键' : node.importance === 'medium' ? '重要' : '次要'}</Tag>
                      <Text style={{ fontSize: 12 }}>{node.description}</Text>
                    </Space>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card title={<span><RiseOutlined /> 爽点/爆点分布</span>} style={{ marginBottom: 16 }}>
            {(finalResult.highlights || []).length > 0 ? (
              <div>
                {(finalResult.highlights || []).map((highlight, index) => (
                  <div key={index} style={{ marginBottom: 6, padding: '6px 10px', borderRadius: 4, borderLeft: `3px solid ${INTENSITY_COLOR[highlight.intensity] || '#999'}` }}>
                    <Space size={4} style={{ marginBottom: 2 }}>
                      <Tag color={highlight.type === 'climax' ? 'red' : highlight.type === 'reveal' ? 'blue' : highlight.type === 'emotional' ? 'orange' : 'purple'}>
                        {highlight.type === 'climax' ? '高潮' : highlight.type === 'reveal' ? '揭示' : highlight.type === 'emotional' ? '情感' : '动作'}
                      </Tag>
                      <Tag>{highlight.intensity === 'high' ? '强' : highlight.intensity === 'medium' ? '中' : '弱'}</Tag>
                    </Space>
                    <Paragraph style={{ marginBottom: 0, fontSize: 12 }}>{highlight.description}</Paragraph>
                  </div>
                ))}
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未识别到爽点/爆点" />
            )}
          </Card>

          {finalResult.rhythm_curve && finalResult.rhythm_curve.length > 0 && (
            <Card title={<span><FieldTimeOutlined /> 叙事节奏曲线</span>} style={{ marginBottom: 16 }}>
              <div style={{ position: 'relative', height: 80, background: '#fafafa', borderRadius: 4, margin: '10px 0' }}>
                {finalResult.rhythm_curve.map((point, index) => {
                  const paceHeight: Record<string, number> = { slow: 20, medium: 40, fast: 60, intense: 80 }
                  return (
                    <div key={index} style={{
                      position: 'absolute',
                      left: `${point.position_pct}%`,
                      bottom: 0,
                      transform: 'translateX(-50%)',
                      width: 4,
                      height: paceHeight[point.pace] || 30,
                      background: point.pace === 'intense' ? '#f5222d' : point.pace === 'fast' ? '#fa8c16' : point.pace === 'medium' ? '#1677ff' : '#52c41a',
                      borderRadius: '2px 2px 0 0',
                    }} title={`${point.position_pct}%: ${point.pace} - ${point.label || ''}`} />
                  )
                })}
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#999' }}>
                <span>0%</span><span>25%</span><span>50%</span><span>75%</span><span>100%</span>
              </div>
              <div style={{ marginTop: 8, display: 'flex', gap: 16 }}>
                <Tag color="green">舒缓</Tag>
                <Tag color="blue">中等</Tag>
                <Tag color="orange">快速</Tag>
                <Tag color="red">紧张</Tag>
              </div>
            </Card>
          )}

          {finalResult.patterns && finalResult.patterns.length > 0 && (
            <Card title={<span><BulbOutlined /> 写作模式总结</span>} style={{ marginBottom: 16 }}>
              <Collapse
                items={finalResult.patterns.map((pattern, index) => ({
                  key: String(index),
                  label: (
                    <Space>
                      <Tag color={pattern.type === 'technique' ? 'blue' : pattern.type === 'theme' ? 'purple' : 'orange'}>
                        {pattern.type === 'technique' ? '技巧' : pattern.type === 'theme' ? '主题' : '结构'}
                      </Tag>
                      <Text>{pattern.description}</Text>
                      <Tag>{pattern.frequency === 'frequent' ? '频繁' : pattern.frequency === 'moderate' ? '中等' : '少见'}</Tag>
                    </Space>
                  ),
                  children: (
                    <div>
                      {pattern.examples && pattern.examples.length > 0 && (
                        <div>
                          <Text type="secondary">示例：</Text>
                          {pattern.examples.map((example, exampleIndex) => (
                            <Tag key={exampleIndex} style={{ marginBottom: 4 }}>{example}</Tag>
                          ))}
                        </div>
                      )}
                    </div>
                  ),
                }))}
              />
            </Card>
          )}
        </>
      )}
    </div>
  )
}

export default DeconstructPage
