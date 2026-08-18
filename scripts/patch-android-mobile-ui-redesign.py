from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


app_path = Path("mobile/android/app/src/main/java/com/siming/mobile/ui/SimingApp.kt")
text = app_path.read_text(encoding="utf-8")

text = replace_once(
    text,
    '''private enum class RootTab(val label: String, val icon: ImageVector) {
    Create("AI 立项", Icons.Outlined.AutoAwesome),
    Library("作品", Icons.AutoMirrored.Outlined.LibraryBooks),
    Sync("同步", Icons.Outlined.Sync),
    Settings("设置", Icons.Outlined.Settings),
}''',
    '''private enum class RootTab(val label: String, val icon: ImageVector) {
    Library("作品", Icons.AutoMirrored.Outlined.LibraryBooks),
    Create("立项", Icons.Outlined.AutoAwesome),
    Sync("同步", Icons.Outlined.Sync),
    Settings("设置", Icons.Outlined.Settings),
}''',
    "root tab order",
)
text = replace_once(
    text,
    '    var rootTab by rememberSaveable { mutableStateOf(RootTab.Create) }',
    '    var rootTab by rememberSaveable { mutableStateOf(RootTab.Library) }',
    "default root tab",
)

library_start = text.index("private fun LibraryScreen(")
library_end = text.index("@Composable\nprivate fun ProjectCard(", library_start)
library = text[library_start:library_end]
old_library_actions = '''            item {
                ScreenHeading(
                    kicker = "LOCAL-FIRST LIBRARY",
                    title = "作品库",
                    detail = "从零立项、导入现有小说、继续写作都从这里开始；导入后可直接进入作品建档与导出。",
                )
            }
            item {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = onStartAiCreation) {
                        Icon(Icons.Outlined.AutoAwesome, null)
                        Spacer(Modifier.width(7.dp))
                        Text("AI 立项")
                    }
                    OutlinedButton(onClick = { showCreate = true }) {
                        Icon(Icons.Outlined.Add, null)
                        Spacer(Modifier.width(7.dp))
                        Text("空白作品")
                    }
                    OutlinedButton(
                        onClick = {
                            onPickText { name, text ->
                                viewModel.importNovel(name, text, onOpenProject)
                            }
                        },
                    ) {
                        Icon(Icons.Outlined.FileOpen, null)
                        Spacer(Modifier.width(7.dp))
                        Text("导入已有小说")
                    }
                }
            }'''
new_library_actions = '''            item {
                Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    Text("作品", style = MaterialTheme.typography.headlineSmall)
                    Text(
                        if (projects.isEmpty()) "创建或导入你的第一部小说" else "${projects.size} 部作品 · 继续上次的创作",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            item {
                LibraryActionPanel(
                    onStartAiCreation = onStartAiCreation,
                    onCreateBlank = { showCreate = true },
                    onImportNovel = {
                        onPickText { name, text ->
                            viewModel.importNovel(name, text, onOpenProject)
                        }
                    },
                )
            }'''
library = replace_once(library, old_library_actions, new_library_actions, "library actions")
library = replace_once(
    library,
    '''                    ProjectCard(
                        project,
                        localOnly = connection == null,
                        onClick = { onOpenProject(project.projectId) },
                        onDelete = { deleteTarget = project },
                    )''',
    '''                    MobileProjectCard(
                        project = project,
                        localOnly = connection == null,
                        onClick = { onOpenProject(project.projectId) },
                        onDelete = { deleteTarget = project },
                    )''',
    "library project card",
)
text = text[:library_start] + library + text[library_end:]

project_start = text.index("private fun ProjectScreen(")
project_end = text.index("@Composable\nprivate fun RecordList(", project_start)
project = text[project_start:project_end]
project = replace_once(
    project,
    '    var section by rememberSaveable(project.projectId) { mutableStateOf("assistant") }',
    '    var section by rememberSaveable(project.projectId) { mutableStateOf("chapter") }\n    var lastReferenceSection by rememberSaveable(project.projectId) { mutableStateOf("outline") }',
    "project default section",
)
project = replace_once(
    project,
    '''        floatingActionButton = {
            if (section !in setOf("assistant", "tools")) {''',
    '''        bottomBar = {
            ProjectPrimaryNavigation(
                selected = section,
                preferredReferenceSection = lastReferenceSection,
                onSelected = { section = it },
            )
        },
        floatingActionButton = {
            if (section !in setOf("assistant", "tools")) {''',
    "project bottom navigation",
)
project = replace_once(
    project,
    '''        Column(Modifier.padding(padding).fillMaxSize()) {
            ProjectSectionNavigation(selected = section, onSelected = { section = it })
            when (section) {''',
    '''        Column(Modifier.padding(padding).fillMaxSize()) {
            if (section in projectReferenceSections.map { it.first }) {
                ProjectReferenceNavigation(
                    selected = section,
                    onSelected = {
                        section = it
                        lastReferenceSection = it
                    },
                )
            }
            when (section) {''',
    "project reference navigation",
)
text = text[:project_start] + project + text[project_end:]

old_heading = '''@Composable
internal fun ScreenHeading(kicker: String, title: String, detail: String) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(kicker, color = SimingCinnabar, fontSize = 10.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.5.sp)
        Text(title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
        Text(detail, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}'''
new_heading = '''@Suppress("UNUSED_PARAMETER")
@Composable
internal fun ScreenHeading(kicker: String, title: String, detail: String) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(title, style = MaterialTheme.typography.titleLarge)
        if (detail.isNotBlank()) {
            Text(
                detail,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}'''
text = replace_once(text, old_heading, new_heading, "screen heading")
app_path.write_text(text, encoding="utf-8")

tools_path = Path("mobile/android/app/src/main/java/com/siming/mobile/ui/ProjectToolsPanel.kt")
tools = tools_path.read_text(encoding="utf-8")
tools = replace_once(
    tools,
    '''            ScreenHeading(
                kicker = "PROJECT TOOLBOX",
                title = "作品工具",
                detail = "导入后的建档、全书导出和作品维护集中在这里，减少在多个页面之间来回寻找功能。",
            )''',
    '''            ScreenHeading(
                kicker = "",
                title = "作品工具",
                detail = "建档、导出和作品维护集中在这里。",
            )''',
    "project tools heading",
)
tools_path.write_text(tools, encoding="utf-8")

print("Android mobile UI redesign shell patch applied")
