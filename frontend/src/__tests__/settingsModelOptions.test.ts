import { describe, expect, it } from 'vitest'

import {
  DEFAULT_CLI_ARGS,
  defaultSafetyLimits,
  fallbackModelOptions,
  normalizeProviderModelOptions,
  usesDocumentedModelCatalog,
} from '../features/localModels/settingsModelOptions'

describe('local CLI default arguments', () => {
  it('never suggests bypass, yolo, trust, or auto-approval flags', () => {
    const defaults = Object.values(DEFAULT_CLI_ARGS).join(' ').toLowerCase()

    expect(defaults).not.toContain('bypasspermissions')
    expect(defaults).not.toContain('dangerously-bypass')
    expect(defaults).not.toContain('dangerously-skip')
    expect(defaults).not.toContain('--approve-mcps')
    expect(defaults).not.toContain('--trust')
    expect(defaults).not.toContain('--yolo')
    expect(defaults).not.toContain('"--auto"')
  })
})

describe('model capacity defaults', () => {
  it('prefills documented DeepSeek capacity without a manual profile', () => {
    const option = fallbackModelOptions('deepseek').find(
      (item) => item.id === 'deepseek-v4-flash',
    )

    expect(defaultSafetyLimits('deepseek', 'deepseek-v4-flash', option)).toMatchObject({
      context_window_tokens: 1_000_000,
      context_safety_margin_tokens: 512,
      context_profile_source: 'deepseek_model_docs_2026_08_30',
      max_output_tokens: 384_000,
    })
  })

  it('preserves provider capacity metadata while normalizing Gemini ids', () => {
    const [option] = normalizeProviderModelOptions('gemini', [{
      id: 'models/gemini-provider-model',
      display_name: 'Provider Model',
      context_window_tokens: 256_000,
      max_output_tokens: 16_000,
      safety_margin_tokens: 1_024,
      capacity_source: 'provider_models_api',
    }])

    expect(option).toEqual({
      id: 'gemini-provider-model',
      display_name: 'Provider Model',
      context_window_tokens: 256_000,
      max_output_tokens: 16_000,
      safety_margin_tokens: 1_024,
      capacity_source: 'provider_models_api',
    })
  })

  it('does not invent capacity for an opaque CLI model', () => {
    expect(defaultSafetyLimits('codex_cli', 'codex-cli')).toMatchObject({
      context_window_tokens: undefined,
      context_profile_source: undefined,
    })
  })

  it('does not reuse official capacity for the same model name on a proxy', () => {
    const option = fallbackModelOptions('deepseek').find(
      (item) => item.id === 'deepseek-v4-flash',
    )

    expect(usesDocumentedModelCatalog(
      'deepseek',
      'https://proxy.example/v1',
    )).toBe(false)
    expect(defaultSafetyLimits(
      'deepseek',
      'deepseek-v4-flash',
      option,
      'https://proxy.example/v1',
    )).toMatchObject({
      context_window_tokens: undefined,
      context_profile_source: undefined,
    })
  })
})
