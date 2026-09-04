package com.siming.mobile.data.agent

/**
 * Persists a rejected native batch before applying its terminal admission policy.
 *
 * An over-capacity assistant transaction has not been shown back to the provider,
 * so it must stay DELIVERED across the thrown turn error.  The next successful
 * provider step is the only place that may mark it consumed and create receipts.
 */
internal suspend fun persistRejectedMobileNativeToolBatch(
    conversationStore: MobileAssistantConversationStore,
    projectId: String,
    turnContext: MobileAssistantTurnContext,
    transaction: MobileToolTransaction,
    admission: MobileNativeToolBatchAdmission,
    overCapacityDetail: String,
    afterPersist: suspend (MobileTurnToolRuntimeState) -> Unit,
): MobileTurnToolRuntimeState {
    require(!admission.accepted && admission.reason != null) {
        "只有已拒绝的原生工具批次可以进入拒绝持久化路径"
    }
    val runtime = conversationStore.recordDeliveredToolTransaction(
        projectId = projectId,
        turnContext = turnContext,
        transaction = transaction,
    )
    afterPersist(runtime)
    if (admission.reason == MobileNativeToolBudgetContract.NATIVE_ASSISTANT_TRANSACTION_OVER_CAPACITY) {
        throw MobileConversationContextException(
            MobileConversationContextErrorCode.PROTOCOL_INVALID,
            overCapacityDetail,
        )
    }
    return runtime
}
