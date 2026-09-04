import { describe, expect, it } from 'vitest'

import { modelContextCapacityIssueFrom } from '../services/conversationContextErrors'

describe('conversation context error remediation', () => {
  it('accepts only the stable capacity-unknown remediation envelope', () => {
    expect(modelContextCapacityIssueFrom({
      type: 'error',
      code: 'conversation_capacity_unknown',
      message: '容量未知',
      details: { remediation: 'configure_model_context_profile' },
    })).toEqual({
      code: 'conversation_capacity_unknown',
      message: '容量未知',
      remediation: 'configure_model_context_profile',
    })

    expect(modelContextCapacityIssueFrom({
      detail: {
        code: 'conversation_capacity_unknown',
        message: '容量未知',
        details: { remediation: 'retry' },
      },
    })).toBeNull()
    expect(modelContextCapacityIssueFrom(new Error('模型可能有 128k'))).toBeNull()
  })
})
