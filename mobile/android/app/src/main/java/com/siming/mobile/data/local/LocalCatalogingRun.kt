package com.siming.mobile.data.local

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/** Durable chapter operation, plan and atomic application receipt, separate from sync replicas. */
@Entity(tableName = "local_cataloging_runs", indices = [Index(value = ["projectId", "chapterId"])])
data class LocalCatalogingRun(
    @PrimaryKey val id: String,
    val projectId: String,
    val chapterId: String,
    val chapterVersion: Int,
    val contentHash: String,
    val status: String = "running",
    val sourceFingerprint: String,
    val sourceJson: String,
    val candidatesJson: String = "[]",
    val transcriptJson: String = "[]",
    val changesJson: String? = null,
    val error: String? = null,
    val model: String,
    val attempt: Int = 1,
    val syncState: String = "not_ready",
    val createdAt: String,
    val updatedAt: String,
)
