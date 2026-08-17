from pathlib import Path
import re


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return text.replace(old, new, 1)


def sub_once(text: str, pattern: str, replacement: str, label: str, flags: int = 0) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return updated


# GatewayApi: governance content upsert and lifecycle status are separate PC APIs.
path = Path("mobile/android/app/src/main/java/com/siming/mobile/data/network/GatewayApi.kt")
text = path.read_text(encoding="utf-8")
anchor = '''    suspend fun saveGovernanceEntity(
        connection: GatewayConnection,
        projectId: String,
        payload: JsonObject,
    ): JsonObject = canonicalWrite(
        connection = connection,
        path = PcApiPaths.narrativeGovernanceItems(projectId),
        method = "POST",
        payload = payload,
    )
'''
addition = anchor + '''
    suspend fun updateGovernanceStatus(
        connection: GatewayConnection,
        projectId: String,
        itemType: String,
        itemId: String,
        payload: JsonObject,
    ): JsonObject = canonicalWrite(
        connection = connection,
        path = PcApiPaths.narrativeGovernanceStatus(projectId, itemType, itemId),
        method = "PATCH",
        payload = payload,
    )
'''
text = replace_once(text, anchor, addition, "Gateway governance method")
path.write_text(text, encoding="utf-8")


# Repository: filter outbox writes, preserve rich replicas, and use canonical governance lifecycle.
path = Path("mobile/android/app/src/main/java/com/siming/mobile/data/SimingRepository.kt")
text = path.read_text(encoding="utf-8")
old = '''            val response = try {
                if (entityType in GOVERNANCE_ENTITY_TYPES) {
                    api.saveGovernanceEntity(
                        connection,
                        projectId,
                        PcApiPayloads.governance(entityType, payload, entityId, create),
                    )
                } else {
                    api.saveAuthoringEntity(
                        connection = connection,
                        projectId = projectId,
                        entityType = entityType,
                        entityId = entityId,
                        create = create,
                        payload = PcApiPayloads.authoring(entityType, payload, create),
                    )
                }
            } catch (error: GatewayHttpException) {
                throw error
            } catch (_: IOException) {
                return saveOfflineEntity(projectId, entityType, entityId, payload)
            }
'''
new = '''            val response = try {
                if (entityType in GOVERNANCE_ENTITY_TYPES) {
                    var saved = api.saveGovernanceEntity(
                        connection,
                        projectId,
                        PcApiPayloads.governanceContent(entityType, payload, entityId, create),
                    )
                    val canonicalId = saved.requiredId()
                    val statusPayload = PcApiPayloads.governanceStatus(entityType, payload)
                    val desiredStatus = (statusPayload?.get("status") as? JsonPrimitive)?.content.orEmpty()
                    val serverStatus = (saved["status"] as? JsonPrimitive)?.content.orEmpty()
                    if (statusPayload != null && desiredStatus.isNotBlank() && desiredStatus != serverStatus) {
                        saved = api.updateGovernanceStatus(
                            connection,
                            projectId,
                            PcApiPayloads.governanceItemType(entityType),
                            canonicalId,
                            statusPayload,
                        )
                    }
                    saved
                } else {
                    api.saveAuthoringEntity(
                        connection = connection,
                        projectId = projectId,
                        entityType = entityType,
                        entityId = entityId,
                        create = create,
                        payload = PcApiPayloads.authoring(entityType, payload, create),
                    )
                }
            } catch (error: GatewayHttpException) {
                throw error
            } catch (_: IOException) {
                return saveOfflineEntity(projectId, entityType, entityId, payload)
            }
'''
text = replace_once(text, old, new, "repository governance write")
old = '''        val key = ReplicaEntity.key(projectId, entityType, entityId)
        val encoded = json.encodeToString(payload)
        val now = Instant.now().toString()
'''
new = '''        val key = ReplicaEntity.key(projectId, entityType, entityId)
        val encoded = json.encodeToString(payload)
        val mutationEncoded = canonicalMutationJson(projectId, entityType, entityId, encoded)
            ?: error("同步写入缺少 payload")
        val now = Instant.now().toString()
'''
text = replace_once(text, old, new, "offline mutation encoding")
# Only the two outbox payload assignments inside saveOfflineEntity should change.
segment_start = text.index("    private suspend fun saveOfflineEntity(")
segment_end = text.index("    suspend fun deleteEntity", segment_start)
segment = text[segment_start:segment_end]
if segment.count("payloadJson = encoded") != 2:
    raise RuntimeError(f"offline outbox payload assignments: {segment.count('payloadJson = encoded')}")
segment = segment.replace("payloadJson = encoded", "payloadJson = mutationEncoded", 2)
text = text[:segment_start] + segment + text[segment_end:]
helper_anchor = '''        if (dao.connection() != null) SyncScheduler.enqueue(appContext)
        return entityId
    }

    suspend fun deleteEntity'''
helper = '''        if (dao.connection() != null) SyncScheduler.enqueue(appContext)
        return entityId
    }

    private fun canonicalMutationJson(
        projectId: String,
        entityType: String,
        entityId: String,
        rawPayload: String?,
    ): String? {
        if (rawPayload == null) return null
        val source = json.parseToJsonElement(rawPayload) as? JsonObject
            ?: error("本机资料 payload 不是 JSON 对象")
        return json.encodeToString(
            PcApiPayloads.syncMutation(entityType, source, projectId, entityId),
        )
    }

    suspend fun deleteEntity'''
text = replace_once(text, helper_anchor, helper, "canonical mutation helper")
old = '''                            if (current != null) {
                                val unchanged = sha256(current.payloadJson ?: "null") ==
                                    sha256(sent.payloadJson ?: "null") && current.operation == sent.operation
                                dao.saveEntity(
                                    current.copy(
                                        revision = revision,
                                        dirty = !unchanged,
                                        conflicted = false,
                                    ),
                                )
                                if (!unchanged && dao.pendingMutation(
                                        sent.projectId,
                                        sent.entityType,
                                        sent.entityId,
                                    ) == null
                                ) {
                                    dao.saveMutation(
                                        OutboxMutation(
                                            mutationId = UUID.randomUUID().toString(),
                                            projectId = sent.projectId,
                                            entityType = sent.entityType,
                                            entityId = sent.entityId,
                                            operation = current.operation,
                                            baseRevision = revision,
                                            payloadJson = current.payloadJson,
                                            clientModifiedAt = Instant.now().toString(),
                                        ),
                                    )
                                }
                            }
'''
new = '''                            if (current != null) {
                                val currentMutation = if (current.operation == "delete") {
                                    null
                                } else {
                                    canonicalMutationJson(
                                        sent.projectId,
                                        sent.entityType,
                                        sent.entityId,
                                        current.payloadJson,
                                    )
                                }
                                val unchanged = sha256(currentMutation ?: "null") ==
                                    sha256(sent.payloadJson ?: "null") && current.operation == sent.operation
                                dao.saveEntity(
                                    current.copy(
                                        revision = revision,
                                        dirty = !unchanged,
                                        conflicted = false,
                                    ),
                                )
                                if (!unchanged && dao.pendingMutation(
                                        sent.projectId,
                                        sent.entityType,
                                        sent.entityId,
                                    ) == null
                                ) {
                                    dao.saveMutation(
                                        OutboxMutation(
                                            mutationId = UUID.randomUUID().toString(),
                                            projectId = sent.projectId,
                                            entityType = sent.entityType,
                                            entityId = sent.entityId,
                                            operation = current.operation,
                                            baseRevision = revision,
                                            payloadJson = currentMutation,
                                            clientModifiedAt = Instant.now().toString(),
                                        ),
                                    )
                                }
                            }
'''
text = replace_once(text, old, new, "outbox canonical comparison")
path.write_text(text, encoding="utf-8")


# SimingApp: generate all generic authoring fields from the shared PC contract.
path = Path("mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt")
text = path.read_text(encoding="utf-8")
import_anchor = "import com.siming.mobile.data.network.DirectApiSummary\n"
imports = import_anchor + "import com.siming.mobile.data.network.PcAuthoringContract\nimport com.siming.mobile.data.network.PcFieldKind\n"
text = replace_once(text, import_anchor, imports, "SimingApp PC contract imports")
pattern = r'''private data class FormField\(.*?\nprivate fun fieldsFor\(type: String\): List<FormField> = when \(type\) \{.*?\n    else -> emptyList\(\)\n\}\n'''
replacement = '''private data class FormField(
    val key: String,
    val label: String,
    val placeholder: String = "",
    val kind: PcFieldKind,
) {
    val multiline: Boolean
        get() = kind in setOf(
            PcFieldKind.Multiline,
            PcFieldKind.StringArray,
            PcFieldKind.JsonObject,
            PcFieldKind.JsonArray,
        )
}

private fun fieldsFor(type: String): List<FormField> =
    PcAuthoringContract.mobileFields(type).map { spec ->
        FormField(
            key = spec.key,
            label = fieldLabel(type, spec.key),
            placeholder = fieldPlaceholder(type, spec.key),
            kind = spec.kind,
        )
    }

private fun fieldLabel(type: String, key: String): String = when (type) {
    "project" -> when (key) {
        "title" -> "作品名"
        "description" -> "作品简介"
        "tags" -> "标签"
        "narrative_perspective" -> "叙事视角"
        "writing_style" -> "写作文风"
        "forbidden_sentence_patterns" -> "禁用句式"
        "rhetoric_guidelines" -> "修辞规则"
        "short_sentences" -> "短句模式"
        "custom_style_prompt" -> "自定义文风约束"
        "daily_word_goal" -> "每日字数目标"
        else -> key
    }
    "chapter" -> when (key) {
        "title" -> "章节名"
        "outline_node_id" -> "关联大纲节点 ID"
        "content" -> "正文"
        else -> key
    }
    "outline" -> when (key) {
        "title" -> "节点标题"
        "node_type" -> "节点类型"
        "parent_id" -> "父节点 ID"
        "summary" -> "计划内容"
        "status" -> "状态"
        "sort_order" -> "同级顺序"
        "characters" -> "角色与场景职责"
        "metadata" -> "大纲元数据"
        else -> key
    }
    "character" -> when (key) {
        "name" -> "角色名"
        "aliases" -> "别名"
        "role_type" -> "角色定位"
        "age" -> "年龄"
        "appearance" -> "外貌"
        "personality" -> "性格"
        "background" -> "背景"
        "abilities" -> "能力"
        "life_status" -> "生命状态"
        "current_location" -> "当前位置"
        "realm_or_level" -> "境界 / 等级"
        "physical_state" -> "身体状态"
        "mental_state" -> "心理状态"
        "current_goal" -> "当前目标"
        "active_conflict" -> "当前冲突"
        "abilities_state" -> "能力状态"
        "items_or_assets" -> "持有物 / 资产"
        "profile" -> "稳定写作锁"
        "is_evolution_tracked" -> "持续追踪角色变化"
        "change_summary" -> "本次变更摘要"
        else -> key
    }
    "world" -> when (key) {
        "title" -> "设定标题"
        "dimension" -> "维度"
        "content" -> "规则与内容"
        "sort_order" -> "顺序"
        else -> key
    }
    "foreshadowing" -> when (key) {
        "title" -> "伏笔标题"
        "description" -> "埋设与回收计划"
        "status" -> "生命周期状态"
        "importance" -> "重要度"
        "storyline" -> "故事线"
        "source_chapter_id" -> "来源章节 ID"
        "target_chapter_id" -> "计划处理章节 ID"
        "target_chapter_number" -> "计划处理章节号"
        "resolved_chapter_id" -> "实际解决章节 ID"
        "evidence" -> "发现证据"
        "resolution_note" -> "解决说明"
        "resolution_evidence" -> "解决证据"
        "verification_note" -> "复检结论"
        "closed_by" -> "关闭者"
        else -> key
    }
    "governance" -> when (key) {
        "title" -> "叙事债务标题"
        "debt_type" -> "债务类型"
        "description" -> "读者期待与兑现条件"
        "status" -> "生命周期状态"
        "priority" -> "优先级"
        "source_chapter_id" -> "来源章节 ID"
        "target_chapter_id" -> "计划处理章节 ID"
        "target_chapter_number" -> "计划处理章节号"
        "resolved_chapter_id" -> "实际解决章节 ID"
        "linked_foreshadowing_id" -> "关联伏笔 ID"
        "linked_causal_edge_id" -> "关联因果项 ID"
        "evidence" -> "发现证据"
        "resolution_note" -> "解决说明"
        "resolution_evidence" -> "解决证据"
        "verification_note" -> "复检结论"
        "closed_by" -> "关闭者"
        else -> key
    }
    else -> key
}

private fun fieldPlaceholder(type: String, key: String): String = when (type to key) {
    "project" to "tags" -> "一行一个；保存为 PC tags 数组"
    "project" to "narrative_perspective" -> "third_person / first_person"
    "project" to "writing_style" -> "与 PC 项目设置一致"
    "project" to "short_sentences" -> "true / false"
    "chapter" to "outline_node_id" -> "留空表示不关联大纲节点"
    "outline" to "node_type" -> "volume / chapter / section"
    "outline" to "parent_id" -> "留空表示根节点"
    "outline" to "status" -> "pending / in_progress / completed"
    "outline" to "characters" -> "JSON 数组，例如 [{\\\"character_id\\\":\\\"...\\\",\\\"role_in_scene\\\":\\\"protagonist\\\"}]"
    "outline" to "metadata" -> "JSON 对象，例如 {\\\"hook\\\":\\\"章末钩子\\\"}"
    "character" to "aliases" -> "一行一个"
    "character" to "role_type" -> "protagonist / supporting / antagonist / mentor / other"
    "character" to "abilities" -> "一行一个"
    "character" to "life_status" -> "active / deceased / unknown"
    "character" to "profile" -> "JSON 对象；与 PC 稳定写作锁完全同步"
    "character" to "is_evolution_tracked" -> "true / false"
    "world" to "dimension" -> "geography / history / factions / power_system / races / culture"
    "foreshadowing" to "status", "governance" to "status" -> "open / pending_review / deferred / fulfilled / abandoned"
    "foreshadowing" to "importance" -> "low / medium / high / critical"
    "governance" to "priority" -> "low / medium / high / critical"
    "governance" to "debt_type" -> "promise / setup / obligation / question"
    else -> ""
}

private fun requiredIdentityField(type: String): String = if (type == "character") "name" else "title"
'''
text = sub_once(text, pattern, replacement, "SimingApp form contract block", re.S)
# Replace editor initialization defaults with defaults that actually apply after blank fields are inserted.
pattern = r'''            when \(target\.entityType\) \{\n                "outline" -> \{.*?\n                \}\n                "world" -> \{.*?\n                \}\n                "character" -> \{.*?\n                \}\n                "foreshadowing" -> \{.*?\n                \}\n                "governance" -> \{.*?\n                \}\n            \}'''
replacement = '''            fun setDefault(key: String, value: String) {
                if (this[key].isNullOrBlank()) this[key] = value
            }
            when (target.entityType) {
                "project" -> {
                    setDefault("narrative_perspective", "third_person")
                    setDefault("writing_style", "natural")
                    setDefault("short_sentences", "false")
                    setDefault("daily_word_goal", "6000")
                }
                "outline" -> {
                    setDefault("node_type", "chapter")
                    setDefault("status", "pending")
                    setDefault("sort_order", "0")
                    setDefault("characters", "[]")
                    setDefault("metadata", "{}")
                }
                "world" -> {
                    setDefault("dimension", "culture")
                    setDefault("sort_order", "0")
                }
                "character" -> {
                    setDefault("role_type", "supporting")
                    setDefault("life_status", "active")
                    setDefault("profile", "{}")
                    setDefault("is_evolution_tracked", "true")
                }
                "foreshadowing" -> {
                    setDefault("status", "open")
                    setDefault("importance", "medium")
                }
                "governance" -> {
                    setDefault("status", "open")
                    setDefault("priority", "medium")
                    setDefault("debt_type", "promise")
                }
            }'''
text = sub_once(text, pattern, replacement, "SimingApp editor defaults", re.S)
# All field typing now comes from PcAuthoringContract; remove mobile-derived write fields.
pattern = r'''                        val mapped = if \(target\.entityType == "character"\) \{.*?\n                        \}\n                        when \(target\.entityType\) \{.*?\n                        \}\n                        viewModel\.saveRecord\('''
replacement = '''                        val mapped = canonicalFormValues(target.entityType, values)
                        viewModel.saveRecord('''
text = sub_once(text, pattern, replacement, "SimingApp typed save mapping", re.S)
old = '''                    enabled = fields.firstOrNull()?.let { values[it.key].orEmpty().isNotBlank() } ?: false,'''
new = '''                    enabled = values[requiredIdentityField(target.entityType)].orEmpty().isNotBlank(),'''
text = replace_once(text, old, new, "SimingApp required identity")
path.write_text(text, encoding="utf-8")
