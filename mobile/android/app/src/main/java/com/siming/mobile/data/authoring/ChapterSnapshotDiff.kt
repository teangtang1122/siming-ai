package com.siming.mobile.data.authoring

import kotlinx.serialization.json.*

/** Line matching with the desktop SequenceMatcher tie order and popular-line rule. */
internal fun chapterSnapshotDiff(from: JsonObject, to: JsonObject): JsonObject {
    fun lines(text: String) = text.replace("\r\n", "\n").map { if (it in "\r\u000b\u000c\u001c\u001d\u001e\u0085\u2028\u2029") '\n' else it }.joinToString("").let {
        if (it.isEmpty()) emptyList() else it.split('\n').let { rows -> if (rows.last().isEmpty()) rows.dropLast(1) else rows }
    }
    val a = lines(from.text("content")); val b = lines(to.text("content"))
    val index = b.indices.groupBy { b[it] }.filterValues { b.size < 200 || it.size <= b.size / 100 + 1 }
    data class Match(val a: Int, val b: Int, val size: Int)
    val matches = mutableListOf<Match>()
    val pending = ArrayDeque<IntArray>().apply { add(intArrayOf(0, a.size, 0, b.size)) }
    while (pending.isNotEmpty()) {
        val (alo, ahi, blo, bhi) = pending.removeLast()
        var bestA = alo; var bestB = blo; var bestSize = 0
        var previous = emptyMap<Int, Int>()
        for (i in alo until ahi) {
            val current = mutableMapOf<Int, Int>()
            index[a[i]].orEmpty().forEach { j -> if (j in blo until bhi) {
                val size = (previous[j - 1] ?: 0) + 1
                current[j] = size
                if (size > bestSize) { bestA = i - size + 1; bestB = j - size + 1; bestSize = size }
            } }
            previous = current
        }
        while (bestA > alo && bestB > blo && a[bestA - 1] == b[bestB - 1]) { bestA--; bestB--; bestSize++ }
        while (bestA + bestSize < ahi && bestB + bestSize < bhi && a[bestA + bestSize] == b[bestB + bestSize]) bestSize++
        if (bestSize > 0) {
            matches += Match(bestA, bestB, bestSize)
            if (alo < bestA && blo < bestB) pending.add(intArrayOf(alo, bestA, blo, bestB))
            if (bestA + bestSize < ahi && bestB + bestSize < bhi) pending.add(intArrayOf(bestA + bestSize, ahi, bestB + bestSize, bhi))
        }
    }
    val blocks = mutableListOf<Match>()
    matches.sortedWith(compareBy<Match> { it.a }.thenBy { it.b }).forEach { match ->
        val last = blocks.lastOrNull()
        if (last != null && last.a + last.size == match.a && last.b + last.size == match.b)
            blocks[blocks.lastIndex] = last.copy(size = last.size + match.size)
        else blocks += match
    }
    blocks += Match(a.size, b.size, 0)
    val changes = mutableListOf<JsonObject>()
    fun change(type: String, i: Int, endI: Int, j: Int, endJ: Int) {
        changes += buildJsonObject {
            put("type", type); put("from_start", i); put("from_end", endI); put("to_start", j); put("to_end", endJ)
            put("from_lines", JsonArray(a.subList(i, endI).map(::JsonPrimitive)))
            put("to_lines", JsonArray(b.subList(j, endJ).map(::JsonPrimitive)))
        }
    }
    var i = 0; var j = 0
    for (match in blocks) {
        val type = when { i < match.a && j < match.b -> "replace"; i < match.a -> "delete"; j < match.b -> "insert"; else -> "" }
        if (type.isNotEmpty()) change(type, i, match.a, j, match.b)
        if (match.size > 0) change("equal", match.a, match.a + match.size, match.b, match.b + match.size)
        i = match.a + match.size; j = match.b + match.size
    }
    return buildJsonObject {
        put("from_snapshot", JsonObject(from - "content")); put("to_snapshot", JsonObject(to - "content"))
        put("changes", JsonArray(changes)); put("total_changes", changes.count { it.text("type") != "equal" })
    }
}
