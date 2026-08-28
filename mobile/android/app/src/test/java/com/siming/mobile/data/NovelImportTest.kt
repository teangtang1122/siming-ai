package com.siming.mobile.data

import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.charset.Charset
import java.util.Base64
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class NovelImportTest {
    @Test
    fun sharedPcEncodingFixturesDecodeIdenticallyWithoutReplacementCharacters() {
        val fixtureFile = listOf(
            File("../../../contracts/novel-import-encoding-fixtures.json"),
            File("../../contracts/novel-import-encoding-fixtures.json"),
            File("contracts/novel-import-encoding-fixtures.json"),
        ).first(File::isFile)
        val fixture = Json.parseToJsonElement(fixtureFile.readText(Charsets.UTF_8)) as JsonObject

        (fixture["cases"] as JsonArray).map { it as JsonObject }.forEach { case ->
            val name = case.string("name")
            val decoded = NovelFileDecoder.decode(
                case.string("filename"),
                Base64.getDecoder().decode(case.string("base64")),
            )

            assertEquals(case.string("expected_encoding"), decoded.encoding, "$name encoding")
            assertEquals(case.string("text"), decoded.text, "$name text")
            assertFalse(decoded.text.contains('\uFFFD'), "$name contains a replacement character")
        }
    }

    @Test
    fun decodesUtf8AndUtf8Bom() {
        val text = "第一章 风起\n这里是正文。"
        val plain = TxtImportDecoder.decode(text.toByteArray(Charsets.UTF_8))
        val bom = TxtImportDecoder.decode(
            byteArrayOf(0xef.toByte(), 0xbb.toByte(), 0xbf.toByte()) +
                text.toByteArray(Charsets.UTF_8),
        )

        assertEquals(text, plain.text)
        assertEquals("UTF-8", plain.encoding)
        assertEquals(text, bom.text)
        assertEquals("UTF-8 BOM", bom.encoding)
    }

    @Test
    fun decodesMarkdownThroughTheTextDecoder() {
        val markdown = "# 第一章 风起\n\n这是 **Markdown** 正文。"

        val decoded = NovelFileDecoder.decode("novel.MD", markdown.toByteArray(Charsets.UTF_8))

        assertEquals(markdown, decoded.text)
        assertEquals("UTF-8", decoded.encoding)
    }

    @Test
    fun decodesGb18030WithoutReplacementCharacters() {
        val text = "第一章 风起\n陆糖看见归墟阵重新亮起。"
        val decoded = TxtImportDecoder.decode(text.toByteArray(Charset.forName("GB18030")))

        assertEquals(text, decoded.text)
        assertEquals("GB18030", decoded.encoding)
        assertFalse(decoded.text.contains('\uFFFD'))
    }

    @Test
    fun decodesRepresentativeBig5NovelsLikePc() {
        val samples = listOf(
            "第一章 風起\n夜色籠罩古城，劍客踏入酒館。",
            "序章\n天地玄黃，宇宙洪荒。日月盈昳，辰宿列張。",
            "楔子\n少女凝視遠方，雨落青石長街。",
            "尾聲\n雲海散盡，故人終於歸來。",
            "春眠不覺曉，處處聞啼鳥。夜來風雨聲，花落知多少。",
        )

        samples.forEach { text ->
            val decoded = TxtImportDecoder.decode(text.toByteArray(Charset.forName("Big5")))
            assertEquals(text, decoded.text)
            assertEquals("Big5", decoded.encoding)
            assertFalse(decoded.text.contains('\uFFFD'))
        }
    }

    @Test
    fun decodesUtf16LittleEndianWithoutBom() {
        val text = "第一章 风起\n这是 UTF-16 正文。"
        val decoded = TxtImportDecoder.decode(text.toByteArray(Charsets.UTF_16LE))

        assertEquals(text, decoded.text)
        assertEquals("UTF-16LE", decoded.encoding)
    }

    @Test
    fun decodesDocxDocumentParagraphsAndIgnoresTableCellParagraphsLikePc() {
        val xml = """
            <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:r><w:t>第一章 风起</w:t></w:r></w:p>
                <w:p><w:r><w:t>风从城门吹来</w:t><w:tab/><w:t>继续</w:t><w:br/><w:t>换行</w:t></w:r></w:p>
                <w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格内容不属于 document.paragraphs</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
              </w:body>
            </w:document>
        """.trimIndent()
        val decoded = NovelFileDecoder.decode("novel.DOCX", docx(xml))

        assertEquals("DOCX", decoded.encoding)
        assertEquals("第一章 风起\n\n风从城门吹来\t继续\n换行", decoded.text)
        assertFalse(decoded.text.contains("表格内容"))
        val chapters = NovelImportSplitter.split(decoded.text)
        assertEquals(1, chapters.size)
        assertEquals("第一章 风起", chapters.single().title)
        assertTrue(chapters.single().content.contains("继续"))
    }

    @Test
    fun rejectsInvalidDocxInsteadOfImportingCorruptedText() {
        val error = assertFailsWith<IllegalArgumentException> {
            NovelFileDecoder.decode("broken.docx", "not a zip".toByteArray())
        }

        assertTrue(error.message.orEmpty().contains("DOCX"))
    }

    @Test
    fun splitsStandardChineseChapterTitles() {
        val text = """
            第一章 风起
            第一章正文。

            第二章 云涌
            第二章正文。
        """.trimIndent()

        val chapters = NovelImportSplitter.split(text)

        assertEquals(2, chapters.size)
        assertEquals(listOf("第一章 风起", "第二章 云涌"), chapters.map { it.title })
        assertTrue(chapters[0].content.startsWith("第一章正文"))
        assertTrue(chapters[1].content.startsWith("第二章正文"))
    }

    @Test
    fun splitsMarkdownHeadingsWithoutKeepingHeadingMarkersInTitles() {
        val text = """
            # 第一章 风起

            第一章有 **加粗** 正文。

            ## 第二章 云涌

            第二章保留 [链接](https://example.com) 标记。
        """.trimIndent()

        val chapters = NovelImportSplitter.split(text)

        assertEquals(2, chapters.size)
        assertEquals(listOf("第一章 风起", "第二章 云涌"), chapters.map { it.title })
        assertTrue(chapters[0].content.contains("**加粗**"))
        assertTrue(chapters[1].content.contains("[链接](https://example.com)"))
    }

    @Test
    fun ignoresSentenceLikeChapterPrefixesInsideBody() {
        val text = """
            第一章 风起！
            第一章正文。这里仍然属于正文，不是新章节。

            第二章 云涌
            第二章正文继续。
        """.trimIndent()

        val chapters = NovelImportSplitter.split(text)

        assertEquals(2, chapters.size)
        assertEquals(listOf("第一章 风起！", "第二章 云涌"), chapters.map { it.title })
        assertTrue(chapters.first().content.contains("第一章正文"))
    }

    @Test
    fun fallbackSplitDoesNotCreateEmptyChapters() {
        val text = "正文".repeat(3_000)
        val chapters = NovelImportSplitter.split(text)

        assertEquals(2, chapters.size)
        assertTrue(chapters.all { it.content.isNotBlank() })
    }

    private fun docx(documentXml: String): ByteArray {
        val output = ByteArrayOutputStream()
        ZipOutputStream(output).use { archive ->
            archive.putNextEntry(ZipEntry("word/document.xml"))
            archive.write(documentXml.toByteArray(Charsets.UTF_8))
            archive.closeEntry()
        }
        return output.toByteArray()
    }

    private fun JsonObject.string(name: String): String =
        (get(name) as? JsonPrimitive)?.contentOrNull.orEmpty()
}
