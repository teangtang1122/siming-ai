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
    const option = { id: 'deepseek-flash' }

    expect(defaultSafetyLimits('deepseek', 'deepseek-flash', option)).toMatchObject({
      context_window_tokens: 1_000_000,
      context_safety_margin_tokens: 512,
      context_profile_source: 'deepseek_model_docs_2026_09_11',
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
    const option = { id: 'deepseek-flash' }

    expect(usesDocumentedModelCatalog(
      'deepseek',
      'https://proxy.example/v1',
    )).toBe(false)
    expect(defaultSafetyLimits(
      'deepseek',
      'deepseek-flash',
      option,
      'https://proxy.example/v1',
    )).toMatchObject({
      context_window_tokens: undefined,
      context_profile_source: undefined,
    })
  })

  it('leaves future DeepSeek capacity unverified with conservative output defaults', () => {
    expect(defaultSafetyLimits('deepseek', 'deepseek-future-test-model')).toMatchObject({
      context_window_tokens: undefined,
      context_profile_source: undefined,
      max_output_tokens: 16_000,
    })
  })
})

describe('provider model discovery', () => {
  it.each(['deepseek', 'gemini', 'openai', 'anthropic', 'qwen'])('keeps new %s models and their metadata', (provider) => {
    const options = [{ id: `${provider}-future-test-model`, display_name: 'New Model',
      context_window_tokens: 128_000, max_output_tokens: 8_000, capacity_source: 'provider_metadata' }]
    expect(normalizeProviderModelOptions(provider, options)).toEqual(options)
  })

  it.each(['deepseek', 'gemini'])('does not replace an empty %s response with a static list', (provider) => {
    expect(normalizeProviderModelOptions(provider, [])).toEqual([])
    expect(fallbackModelOptions(provider)).toEqual([])
  })
})
