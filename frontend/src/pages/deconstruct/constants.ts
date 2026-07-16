/* Constants and pure helper functions for the deconstruct page. */
import type { DeconstructReport } from './types'
import type { OperationOutcome, OperationResult } from '../../components/interaction'

export const PLOT_TYPE_LABEL: Record<string, string> = {
  intro: '引入',
  development: '发展',
  turn: '转折',
  climax: '高潮',
  resolution: '收尾',
}

export const PLOT_TYPE_COLOR: Record<string, string> = {
  intro: 'blue',
  development: 'green',
  turn: 'orange',
  climax: 'red',
  resolution: 'purple',
}

export const IMPORTANCE_COLOR: Record<string, string> = {
  high: 'red',
  medium: 'orange',
  low: 'default',
}

export const INTENSITY_COLOR: Record<string, string> = {
  high: '#f5222d',
  medium: '#fa8c16',
  low: '#52c41a',
}

export const phaseLabel = (phase?: string) => {
  const labels: Record<string, string> = {
    queued: '等待开始',
    map: '分块分析',
    reduce: '自动合并',
    completed: '已完成',
    failed: '失败',
  }
  return labels[phase || ''] || phase || '未开始'
}

export const reportPercent = (report?: DeconstructReport | null) => {
  if (!report) return 0
  if (report.status === 'completed') return 100
  if (report.phase === 'reduce') return 95
  if (!report.total_chunks) return 0
  return Math.min(90, Math.floor((report.completed_chunks / report.total_chunks) * 90))
}

export const formatSeconds = (seconds?: number) => {
  if (!seconds || seconds <= 0) return '0秒'
  if (seconds < 60) return `${Math.round(seconds)}秒`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return rest ? `${minutes}分${rest}秒` : `${minutes}分`
}

export function deconstructReportOutcome(report?: DeconstructReport | null): {
  outcome: OperationOutcome
  title: string
  result: OperationResult
} | null {
  if (!report || !['completed', 'failed', 'cancelled'].includes(report.status)) return null
  const completed = report.completed_chunks || 0
  const failed = report.failed_chunks || 0
  if (report.status === 'completed') {
    return {
      outcome: failed ? 'partial_success' : 'completed_with_tools',
      title: failed ? '拆书报告已生成，部分分块需要复查' : '拆书分析已完成',
      result: {
        summary: '分块结果已经合并并保存为可再次打开的拆书报告。',
        completed: [`${completed} 个分块已完成`, '分析报告已保存'],
        incomplete: failed ? [`${failed} 个分块未成功解析`] : [],
      },
    }
  }
  if (report.status === 'cancelled') {
    return {
      outcome: 'cancelled',
      title: '拆书任务已取消',
      result: {
        summary: '已完成的分块仍然保留，可稍后重新开始。',
        completed: completed ? [`${completed} 个分块已完成`] : [],
        incomplete: ['完整报告尚未生成'],
      },
    }
  }
  return {
    outcome: completed ? 'partial_success' : 'failed',
    title: completed ? '拆书只完成了一部分' : '拆书任务失败',
    result: {
      summary: report.error || report.reduce_error || '可检查模型状态后重试失败分块。',
      completed: completed ? [`${completed} 个分块已完成`] : [],
      incomplete: ['完整报告尚未生成'],
    },
  }
}
