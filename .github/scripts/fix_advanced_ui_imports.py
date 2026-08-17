from pathlib import Path

path = Path("mobile/android/app/src/main/java/com/siming/mobile/ui/AdvancedAuthoringDialogs.kt")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "import androidx.compose.foundation.layout.width\n",
    "import androidx.compose.foundation.layout.width\nimport androidx.compose.foundation.layout.weight\n",
    1,
)
for unused in (
    "import androidx.compose.material3.OutlinedButton\n",
    "import kotlinx.serialization.json.JsonElement\n",
    "import kotlinx.serialization.json.jsonArray\n",
    "import kotlinx.serialization.json.jsonObject\n",
    "import kotlinx.serialization.json.jsonPrimitive\n",
):
    text = text.replace(unused, "")
path.write_text(text, encoding="utf-8")
