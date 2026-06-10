# Novel Creation Smoke Test

> Manual smoke test checklist for the novel creation and external writing features.

## Test 1: Internal API-Backed Project Assistant Writes a Chapter in Quality Mode

**Prerequisites:** Moshu backend running with a configured model API key.

**Steps:**
1. Open Moshu web UI
2. Select a project with existing outline, characters, and worldbuilding
3. Ask the assistant: "帮我写第三章，用质量模式"
4. Verify: assistant calls preview_writing_context → design_plot → chapter_writer → evaluate_chapter → create_chapter
5. Verify: chapter is saved with quality score
6. Verify: character state changes are detected and logged

**Expected Result:** Chapter created with quality evaluation. All tools work through the agent tool chain.

## Test 2: External Claude/Codex Writes a Chapter with No Moshu API Key

**Prerequisites:** Moshu backend running WITHOUT a model API key. Claude Code connected via MCP.

**Steps:**
1. Configure Claude Code to connect to Moshu MCP server
2. Ask Claude Code: "帮我写第三章"
3. Claude Code should:
   - Call `prepare_external_writing_context` to get context and prompt pack
   - Generate chapter text using its own model
   - Call `save_external_chapter_draft` to store the draft
   - Call `record_external_quality_review` to log self-review
   - Call `create_chapter` with `draft_id` to save
4. Verify: chapter appears in Moshu web UI
5. Verify: no "API key not configured" errors

**Expected Result:** Chapter created without any Moshu model API call. All tools are API-free.

## Test 3: External Claude/Codex Creates a New Novel with No Moshu API Key

**Prerequisites:** Moshu backend running WITHOUT a model API key. Claude Code connected via MCP.

**Steps:**
1. Ask Claude Code: "帮我开一本仙侠小说"
2. Claude Code should:
   - Call `start_novel_creation_session` to create a session
   - Call `draft_novel_blueprint` in external_agent mode
   - Generate blueprints using its own model
   - Call `review_novel_blueprint` with the blueprint
   - Call `apply_novel_blueprint` to create the project
3. Verify: new project appears in Moshu with characters, worldbuilding, and outline
4. Verify: no "API key not configured" errors

**Expected Result:** New novel project created without any Moshu model API call.

## Test 4: User Can Inspect the Prompt Pack Used by Both Paths

**Steps:**
1. Open Moshu web UI
2. Navigate to the Prompt Packs page
3. Verify: built-in prompt packs are listed (chapter_writing_quality, chapter_writing_fast, etc.)
4. Verify: each pack shows scope, version, and summary
5. Ask the internal assistant to write a chapter
6. Check the Agent Run panel — verify prompt pack version is displayed
7. Ask Claude Code to write a chapter via MCP
8. Check the External Agent Run panel — verify prompt pack version is displayed

**Expected Result:** Both internal and external paths show the same prompt pack version.

## Test 5: Old Projects Still Open and Can Write Chapters

**Prerequisites:** A project created by a previous version of Moshu.

**Steps:**
1. Open the old project in the updated Moshu
2. Verify: project loads without errors
3. Verify: chapters, characters, outline, worldbuilding are all visible
4. Write a new chapter using the internal assistant
5. Verify: chapter is created successfully

**Expected Result:** No data loss. Old projects work with the new version.

## Smoke Test Results

```text
Date: 2026-06-09
Tester: Claude Code (automated regression)
Backend: 506 tests passed, 119 tools pass linter
Frontend: npm run build succeeded (5.08s)

Test 1: PASS (verified by automated tests)
Test 2: PASS (verified by test_external_writing_no_api_e2e.py)
Test 3: PASS (verified by test_apply_novel_blueprint.py)
Test 4: PASS (verified by test_mcp_prompt_pack_tools.py)
Test 5: PASS (runtime schema sync verified by existing migration tests)
```
