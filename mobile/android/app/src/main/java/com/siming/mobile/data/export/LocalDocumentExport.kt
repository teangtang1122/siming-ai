package com.siming.mobile.data.export

import android.graphics.Paint
import android.graphics.Typeface
import android.graphics.pdf.PdfDocument
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import java.io.ByteArrayOutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

internal data class ExportParagraph(val text: String, val heading: Int = 0)

/** Android owns document rendering; the caller supplies the canonical reading order. */
internal fun exportDocx(paragraphs: List<ExportParagraph>): ByteArray {
    val output = ByteArrayOutputStream()
    ZipOutputStream(output).use { zip ->
        fun entry(name: String, content: String) {
            zip.putNextEntry(ZipEntry(name))
            zip.write(content.toByteArray(Charsets.UTF_8))
            zip.closeEntry()
        }
        entry("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>""")
        entry("_rels/.rels", """<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="document" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>""")
        entry("word/_rels/document.xml.rels", """<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>""")
        entry("word/styles.xml", """<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:spacing w:after="120" w:line="360" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:eastAsia="Microsoft YaHei"/><w:sz w:val="22"/></w:rPr></w:style></w:styles>""")
        entry("word/document.xml", buildString {
            append("""<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>""")
            paragraphs.forEach { paragraph ->
                append("<w:p>")
                if (paragraph.heading > 0) append("<w:pPr><w:keepNext/><w:outlineLvl w:val=\"${paragraph.heading - 1}\"/></w:pPr>")
                append("<w:r>")
                if (paragraph.heading > 0) append("<w:rPr><w:b/><w:sz w:val=\"${if (paragraph.heading == 1) 36 else 28}\"/></w:rPr>")
                append("<w:t xml:space=\"preserve\">").append(xmlText(paragraph.text)).append("</w:t></w:r></w:p>")
            }
            append("<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/><w:pgMar w:top=\"1134\" w:bottom=\"1134\" w:left=\"1134\" w:right=\"1134\"/></w:sectPr></w:body></w:document>")
        })
    }
    return output.toByteArray()
}

private fun xmlText(text: String) = buildString {
    text.forEach { char -> when (char) {
        '&' -> append("&amp;"); '<' -> append("&lt;"); '>' -> append("&gt;")
        else -> if (char >= ' ' || char in "\t\n\r") append(char)
    } }
}

internal fun exportPdf(paragraphs: List<ExportParagraph>): ByteArray {
    val output = ByteArrayOutputStream()
    val document = PdfDocument()
    try {
        var pageNumber = 0
        var page: PdfDocument.Page? = null
        var y = 48f
        fun nextPage() {
            page?.let(document::finishPage)
            page = document.startPage(PdfDocument.PageInfo.Builder(595, 842, ++pageNumber).create())
            y = 48f
        }
        nextPage()
        paragraphs.forEach { paragraph ->
            val paint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
                textSize = when (paragraph.heading) { 1 -> 20f; 2 -> 15f; else -> 11f }
                typeface = Typeface.create("sans-serif", if (paragraph.heading > 0) Typeface.BOLD else Typeface.NORMAL)
            }
            val text = paragraph.text.ifEmpty { " " }
            val layout = StaticLayout.Builder.obtain(text, 0, text.length, paint, 499)
                .setAlignment(Layout.Alignment.ALIGN_NORMAL).setLineSpacing(0f, 1.5f).setIncludePad(false).build()
            var firstLine = 0
            while (firstLine < layout.lineCount) {
                val top = layout.getLineTop(firstLine)
                var lastLine = firstLine
                while (lastLine < layout.lineCount && layout.getLineBottom(lastLine) - top <= 794f - y) lastLine++
                if (lastLine == firstLine) { nextPage(); continue }
                val height = layout.getLineBottom(lastLine - 1) - top
                requireNotNull(page).canvas.apply {
                    save(); clipRect(48f, y, 547f, y + height)
                    translate(48f, y - top); layout.draw(this); restore()
                }
                y += height
                firstLine = lastLine
                if (firstLine < layout.lineCount) nextPage()
            }
            y += 6f
        }
        page?.let(document::finishPage)
        document.writeTo(output)
    } finally {
        document.close()
    }
    return output.toByteArray()
}
