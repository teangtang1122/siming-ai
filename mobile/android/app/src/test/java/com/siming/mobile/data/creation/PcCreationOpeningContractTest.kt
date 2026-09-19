package com.siming.mobile.data.creation

import com.siming.mobile.data.network.DirectApiClient
import com.siming.mobile.data.network.DirectApiConfig
import java.io.File
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test
import kotlin.test.assertFailsWith

class PcCreationOpeningContractTest {
    private val rawContract = listOf(File("app/src/main/assets/pc_workspace_prompt_contract.json"),
        File("src/main/assets/pc_workspace_prompt_contract.json")).first { it.isFile }.readText(Charsets.UTF_8)
    private val entities = PcCreationEntityContract(Json.parseToJsonElement(rawContract).jsonObject.getValue("creation").jsonObject)
    private val contract = entities.opening
    private val fixture = Json.parseToJsonElement(checkNotNull(javaClass.classLoader?.getResourceAsStream("creation_opening_outline.json"))
        .bufferedReader(Charsets.UTF_8).use { it.readText() }).jsonObject

    @Test
    fun sharedBodyAndParentRejectionsMatchPc() {
        val data = fixture.getValue("data").jsonObject
        contract.validate(data, fixture.getValue("volume_index").jsonArray)
        fixture.getValue("invalid_cases").jsonArray.forEach { raw ->
            val case = raw.jsonObject
            val field = case.getValue("field").jsonPrimitive.content
            val index = case.getValue("index").jsonPrimitive.int
            val key = case.getValue("key").jsonPrimitive.content
            val rows = data.getValue(field).jsonArray.toMutableList()
            rows[index] = JsonObject(rows[index].jsonObject + (key to case.getValue("value")))
            val error = assertFailsWith<CreationGenerationException> {
                contract.validate(JsonObject(data + (field to JsonArray(rows))), fixture.getValue("volume_index").jsonArray)
            }
            assertEquals(case["reason"], error.diagnostic.getValue("data").jsonObject["reason"])
            assertEquals("$.data.$field[$index].$key", error.diagnostic.getValue("data").jsonObject.getValue("path").jsonPrimitive.content)
        }
    }

    @Test
    fun standaloneMaterializationPreservesThreeChapterBodiesAndNineScenesAfterVolumeReorder() {
        val source = readySession()
        val macro = source.stage("macro_outline").getValue("data").jsonObject
        val reversed = replaceStage(source, "macro_outline", JsonObject(macro + ("volumes" to JsonArray(macro.getValue("volumes").jsonArray.reversed()))))
        val rows = contract.materialize(reversed, "formal-project")
        val volumes = rows.filter { it.text("node_type") == "volume" }.associateBy { it.text("title") }
        val chapters = rows.filter { it.text("node_type") == "chapter" }
        val scenes = rows.filter { it.text("node_type") == "section" }
        assertEquals(3, chapters.size)
        assertEquals(9, scenes.size)
        assertEquals(listOf(volumes.getValue("卷一")["id"], volumes.getValue("卷一")["id"], volumes.getValue("卷二")["id"]), chapters.map { it["parent_id"] })
        (chapters + scenes).forEach { assertTrue(it.text("summary").isNotBlank()); assertEquals(it["summary"], it["planned_summary"]) }
        chapters.forEach { chapter ->
            assertEquals(3, scenes.count { it["parent_id"] == chapter["id"] })
            assertTrue(chapter.getValue("metadata_json").jsonObject.containsKey("key_events"))
            assertTrue(chapter.getValue("metadata_json").jsonObject.containsKey("chapter_hook"))
        }
    }

    @Test
    fun invalidSavedOutlineCannotBeConfirmedOrMaterialized() {
        val source = readySession()
        val data = source.stage("opening_outline").getValue("data").jsonObject
        val chapters = data.getValue("chapters").jsonArray.toMutableList()
        chapters[0] = JsonObject(chapters[0].jsonObject + ("summary" to JsonPrimitive("")))
        val broken = JsonObject(data + ("chapters" to JsonArray(chapters)))
        val agent = MobileCreationAgent(rawContract, DirectApiClient())
        assertFailsWith<CreationGenerationException> { agent.confirmStage(source, "opening_outline", broken) }
        assertFailsWith<CreationGenerationException> { agent.replaceArtifact(source, "opening_outline", broken) }
        assertFailsWith<CreationGenerationException> { contract.materialize(replaceStage(source, "opening_outline", broken), "formal-project") }
        contract.validateSaved(source)
    }

    @Test
    fun staleAndForeignVolumeIdsAreRejectedAndTitlesDoNotChooseParents() {
        val source = readySession()
        val data = source.stage("opening_outline").getValue("data").jsonObject
        val foreign = JsonObject(source + ("id" to JsonPrimitive("different-session")))
        assertFailsWith<CreationGenerationException> { contract.validate(data, contract.volumeIndex(foreign)) }
        val macro = source.stage("macro_outline").getValue("data").jsonObject
        val volumes = macro.getValue("volumes").jsonArray.toMutableList()
        volumes[0] = JsonObject(volumes[0].jsonObject + ("summary" to JsonPrimitive("卷内容已变更")))
        val changed = replaceStage(source, "macro_outline", JsonObject(macro + ("volumes" to JsonArray(volumes))))
        assertFailsWith<CreationGenerationException> { contract.validate(data, contract.volumeIndex(changed)) }
    }

    @Test
    fun pcTransferMapsExplicitLocalIdsToRemoteIdsEvenWhenRemoteOrderDiffers() {
        val source = readySession()
        val macro = contract.transferVolumes(source, source.stage("macro_outline").getValue("data").jsonObject)
        val remote = JsonArray(macro.getValue("volumes").jsonArray.mapIndexed { index, raw ->
            JsonObject(raw.jsonObject + ("id" to JsonPrimitive("remote-$index")))
        }.reversed())
        val translated = contract.transferOpening(source.stage("opening_outline").getValue("data").jsonObject, remote)
        contract.validate(translated, remote)
        assertEquals(listOf("remote-0", "remote-0", "remote-1"), translated.getValue("chapters").jsonArray.map { it.jsonObject.text("volume_id") })
        assertFailsWith<CreationGenerationException> { contract.transferOpening(fixture.getValue("data").jsonObject, remote) }
    }

    @Test
    fun generationAndRepairPromptsBothSupplyOwnedVolumeIdsAndRequireBodies() {
        val source = readySession()
        val prompts = PcCreationPromptContract(rawContract)
        val opening = source.stage("opening_outline").getValue("data").jsonObject
        val (_, generation) = prompts.stageMessages(source, "opening_outline", opening)
        val (_, repair) = prompts.repairMessages("{}", "summary is missing", "opening_outline", volumes = contract.volumeIndex(source))
        listOf(generation, repair).forEach { text ->
            assertTrue(text.contains("volume_index")); assertTrue(text.contains("summary"))
            assertTrue(text.contains(contract.volumeIndex(source).first().jsonObject.text("id")))
        }
    }

    @Test
    fun standaloneGenerationRepairsTheSameMissingBodyWithTheSameModel() = runBlocking {
        val source = readySession()
        val valid = source.stage("opening_outline").getValue("data").jsonObject
        val brokenChapters = valid.getValue("chapters").jsonArray.toMutableList()
        brokenChapters[0] = JsonObject(brokenChapters[0].jsonObject + ("summary" to JsonPrimitive("")))
        val invalid = JsonObject(valid + ("chapters" to JsonArray(brokenChapters)))
        for (succeeds in listOf(true, false)) {
            MockWebServer().use { server ->
                listOf(invalid, if (succeeds) valid else invalid).forEach { data ->
                    val chunk = buildJsonObject {
                        put("choices", buildJsonArray { add(buildJsonObject {
                            put("delta", buildJsonObject { put("role", "assistant"); put("content", buildJsonObject { put("data", data) }.toString()) })
                            put("finish_reason", "stop")
                        }) })
                    }
                    server.enqueue(MockResponse().setHeader("Content-Type", "text/event-stream")
                        .setBody("data: $chunk\n\ndata: [DONE]\n\n"))
                }
                server.start()
                val config = DirectApiConfig(displayName = "test", baseUrl = server.url("/").toString(),
                    apiKey = "test", model = "test-model", protocol = DirectApiConfig.PROTOCOL_CHAT_COMPLETIONS,
                    contextWindowTokens = 200_000, maxOutputTokens = 6_000, safetyMarginTokens = 4_096)
                val agent = MobileCreationAgent(rawContract, DirectApiClient(allowCleartextForTests = true, retryDelaysMillis = emptyList()))
                if (succeeds) {
                    val generated = agent.generateStage(source, "opening_outline", "修订前三章细纲", config)
                    assertEquals(contract.normalize(valid), generated.stage("opening_outline")["data"])
                    assertEquals(source.getValue("revision").jsonPrimitive.int + 1, generated.getValue("revision").jsonPrimitive.int)
                } else {
                    assertFailsWith<CreationGenerationException> { agent.generateStage(source, "opening_outline", "修订前三章细纲", config) }
                }
                assertEquals(2, server.requestCount)
                repeat(2) {
                    val request = server.takeRequest().body.readUtf8()
                    assertTrue(request.contains("test-model"))
                    assertTrue(request.contains(contract.volumeIndex(source).first().jsonObject.text("id")))
                }
            }
        }
        assertEquals(valid, source.stage("opening_outline")["data"])
    }

    @Test
    fun wholeStageRegenerationKeepsLockedBodyFields() {
        val baseline = fixture.getValue("data").jsonObject
        val chapters = baseline.getValue("chapters").jsonArray.toMutableList()
        chapters[0] = JsonObject(chapters[0].jsonObject + ("summary" to JsonPrimitive("改写已锁定正文")))
        val paths = JsonArray(listOf(JsonPrimitive("/chapters/0/summary")))
        contract.validateLocks(baseline, baseline, paths)
        assertFailsWith<CreationGenerationException> {
            contract.validateLocks(JsonObject(baseline + ("chapters" to JsonArray(chapters))), baseline, paths)
        }
    }

    @Test
    fun explicitPlannedBodySurvivesStandaloneMaterialization() {
        val source = readySession()
        val opening = source.stage("opening_outline").getValue("data").jsonObject
        val chapters = opening.getValue("chapters").jsonArray.toMutableList()
        val detail = "作者补充的详细章纲：核对证据、安排伏笔，并明确这一章的行动代价。"
        chapters[0] = JsonObject(chapters[0].jsonObject + ("planned_summary" to JsonPrimitive(detail)))
        val normalized = contract.normalize(JsonObject(opening + ("chapters" to JsonArray(chapters))))
        assertEquals(detail, normalized.getValue("chapters").jsonArray.first().jsonObject.text("planned_summary"))
        val records = contract.materialize(replaceStage(source, "opening_outline", normalized), "formal-project")
        assertEquals(detail, records.first { it.text("node_type") == "chapter" }.text("planned_summary"))
    }

    @Test
    fun standaloneCharacterIdsSurviveReorderAndTransferAndRejectForeignSession() {
        val characters = buildJsonObject { put("characters", buildJsonArray {
            add(buildJsonObject { put("name", "甲"); put("role_type", "protagonist") })
            add(buildJsonObject { put("name", "乙"); put("role_type", "supporting") })
        }) }
        var source = replaceStage(readySession(), "characters", characters)
        val ids = contract.characterIndex(source).map { it.jsonObject.getValue("id") }
        val data = source.stage("opening_outline").getValue("data").jsonObject
        val linked = JsonObject(data.toMutableMap().apply {
            listOf("chapters", "sections").forEach { field ->
                put(field, JsonArray(data.getValue(field).jsonArray.map { raw ->
                    JsonObject(raw.jsonObject + ("character_ids" to JsonArray(ids)))
                }))
            }
        })
        source = replaceStage(source, "opening_outline", linked)
        source = replaceStage(source, "characters", JsonObject(characters + ("characters" to JsonArray(characters.getValue("characters").jsonArray.reversed()))))
        val formalIds = ids.associate { it.jsonPrimitive.content to "formal-${it.jsonPrimitive.content}" }
        contract.materialize(source, "project", formalIds).filter { it.text("node_type") != "volume" }.forEach { row ->
            assertEquals(formalIds.values.toSet(), row.getValue("linked_characters").jsonArray.map { it.jsonObject.text("character_id") }.toSet())
        }
        val remoteCharacters = JsonArray(contract.transferCharacters(source, characters).getValue("characters").jsonArray.mapIndexed { i, raw ->
            JsonObject(raw.jsonObject + ("id" to JsonPrimitive("pc-character-$i")))
        })
        val remoteVolumes = JsonArray(contract.transferVolumes(source, source.stage("macro_outline").getValue("data").jsonObject).getValue("volumes").jsonArray.mapIndexed { i, raw ->
            JsonObject(raw.jsonObject + ("id" to JsonPrimitive("pc-volume-$i")))
        })
        val transferred = contract.transferOpening(linked, remoteVolumes, remoteCharacters)
        contract.validate(transferred, remoteVolumes, characters = remoteCharacters)
        assertEquals(setOf("pc-character-0", "pc-character-1"), transferred.getValue("sections").jsonArray.first().jsonObject.getValue("character_ids").jsonArray.map { it.jsonPrimitive.content }.toSet())
        val foreign = JsonObject(source + ("id" to JsonPrimitive("other-session")))
        assertFailsWith<CreationGenerationException> { contract.validate(linked, characters = contract.characterIndex(foreign)) }
    }

    private fun readySession(): JsonObject {
        val agent = MobileCreationAgent(rawContract, DirectApiClient())
        var source = agent.start(CreationStartInput(creationMode = "explore", brief = "主角调查旧城线索"))
        source = replaceStage(source, "macro_outline", buildJsonObject {
            put("volumes", JsonArray(fixture.getValue("volume_index").jsonArray.map { JsonObject(it.jsonObject - "id") }))
        })
        val mapping = fixture.getValue("volume_index").jsonArray.zip(contract.volumeIndex(source))
            .associate { (before, after) -> before.jsonObject.text("id") to after.jsonObject.getValue("id") }
        val opening = fixture.getValue("data").jsonObject
        val bound = JsonObject(opening + ("chapters" to JsonArray(opening.getValue("chapters").jsonArray.map { raw ->
            JsonObject(raw.jsonObject + ("volume_id" to mapping.getValue(raw.jsonObject.text("volume_id"))))
        })))
        return replaceStage(source, "opening_outline", bound)
    }

    private fun replaceStage(source: JsonObject, stage: String, data: JsonObject): JsonObject {
        val draft = source.getValue("draft").jsonObject
        val stages = draft.getValue("stages").jsonObject
        return JsonObject(source + ("draft" to JsonObject(draft + ("stages" to JsonObject(stages + (stage to buildJsonObject {
            put("status", "confirmed"); put("data", data)
        }))))))
    }
    private fun JsonObject.stage(name: String) = getValue("draft").jsonObject.getValue("stages").jsonObject.getValue(name).jsonObject
    private fun JsonObject.text(name: String) = (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
}
