package com.siming.mobile

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.lifecycle.lifecycleScope
import com.google.zxing.client.android.Intents
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import com.siming.mobile.data.MAX_NOVEL_IMPORT_BYTES
import com.siming.mobile.data.MAX_PROJECT_PACKAGE_BYTES
import com.siming.mobile.data.MobileExportFile
import com.siming.mobile.data.MobileNovelImportFile
import com.siming.mobile.data.MobileProjectPackageFile
import com.siming.mobile.data.PROJECT_PACKAGE_MEDIA_TYPE
import com.siming.mobile.data.sha256File
import com.siming.mobile.ui.MainViewModel
import com.siming.mobile.ui.PortraitCaptureActivity
import com.siming.mobile.ui.SimingApp
import com.siming.mobile.ui.SimingTheme
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()

    private val qrScanner = registerForActivityResult(ScanContract()) { result ->
        result.contents?.let(viewModel::acceptPairingQr)
    }


private var pendingExport: MobileExportFile? = null
private val exportSaver = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
    val file = pendingExport
    pendingExport = null
    val uri = result.data?.data
    if (file == null) return@registerForActivityResult
    if (result.resultCode != Activity.RESULT_OK || uri == null) {
        file.deleteTemporarySource()
        return@registerForActivityResult
    }
    lifecycleScope.launch {
        runCatching {
            withContext(Dispatchers.IO) {
                contentResolver.openOutputStream(uri, "w")?.use { output ->
                    val bytes = file.bytes
                    if (bytes != null) {
                        output.write(bytes)
                    } else {
                        File(requireNotNull(file.sourceFilePath)).inputStream().buffered().use { input ->
                            input.copyTo(output, 1024 * 1024)
                        }
                    }
                } ?: error("无法打开导出位置")
            }
        }.onSuccess { viewModel.reportNotice("已导出：${file.filename}") }
            .onFailure { viewModel.reportError(it.message ?: "导出文件写入失败") }
        file.deleteTemporarySource()
    }
}

private fun MobileExportFile.deleteTemporarySource() {
    if (deleteSourceAfterSave) sourceFilePath?.let(::File)?.delete()
}

private fun saveExport(file: MobileExportFile) {
    pendingExport = file
    exportSaver.launch(
        Intent(Intent.ACTION_CREATE_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType(file.mimeType)
            .putExtra(Intent.EXTRA_TITLE, file.filename),
    )
}

private var importCallback: ((MobileNovelImportFile) -> Unit)? = null
private var projectPackageCallback: ((MobileProjectPackageFile) -> Unit)? = null

    private suspend fun readImportFile(uri: Uri): MobileNovelImportFile = withContext(Dispatchers.IO) {
        val name = contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val index = cursor.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
            if (index >= 0 && cursor.moveToFirst()) cursor.getString(index) else null
        } ?: "导入作品.txt"
        val bytes = contentResolver.openInputStream(uri)?.use { input ->
            val output = ByteArrayOutputStream()
            val buffer = ByteArray(32 * 1024)
            var total = 0
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                total += count
                require(total <= MAX_NOVEL_IMPORT_BYTES) {
                    "单个导入文件不能超过 20 MiB"
                }
                output.write(buffer, 0, count)
            }
            output.toByteArray()
        } ?: error("无法读取选择的文件")
        MobileNovelImportFile(name, bytes)
    }

    private val importPicker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        val callback = importCallback
        importCallback = null
        if (uri == null || callback == null) return@registerForActivityResult
        lifecycleScope.launch {
            runCatching { readImportFile(uri) }
                .onSuccess(callback)
                .onFailure { viewModel.reportError(it.message ?: "无法读取选择的文件") }
        }
    }

    private suspend fun readProjectPackageFile(uri: Uri): MobileProjectPackageFile = withContext(Dispatchers.IO) {
        val name = contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val index = cursor.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
            if (index >= 0 && cursor.moveToFirst()) cursor.getString(index) else null
        } ?: "导入作品.siming-project"
        val inbox = File(filesDir, "project-package-inbox").apply { mkdirs() }
        val destination = File(inbox, "${UUID.randomUUID()}.siming-project")
        try {
            var total = 0L
            contentResolver.openInputStream(uri)?.use { input ->
                destination.outputStream().buffered().use { output ->
                    val buffer = ByteArray(1024 * 1024)
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        total += count
                        require(total <= MAX_PROJECT_PACKAGE_BYTES) { "项目包不能超过 512 MiB" }
                        output.write(buffer, 0, count)
                    }
                }
            } ?: error("无法读取选择的项目包")
            require(total > 0L) { "选择的项目包为空" }
            MobileProjectPackageFile(name, destination, total, sha256File(destination))
        } catch (error: Exception) {
            destination.delete()
            throw error
        }
    }

    private val projectPackagePicker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        val callback = projectPackageCallback
        projectPackageCallback = null
        if (uri == null || callback == null) return@registerForActivityResult
        lifecycleScope.launch {
            runCatching { readProjectPackageFile(uri) }
                .onSuccess(callback)
                .onFailure { viewModel.reportError(it.message ?: "无法读取选择的项目包") }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            SimingTheme {
                SimingApp(
                    viewModel = viewModel,
                    onScanQr = {
                        qrScanner.launch(
                            ScanOptions()
                                .setCaptureActivity(PortraitCaptureActivity::class.java)
                                .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                                .setPrompt("扫描电脑上司命生成的一次性配对二维码")
                                .setBeepEnabled(false)
                                .setOrientationLocked(true)
                                .addExtra(Intents.Scan.SCAN_TYPE, Intents.Scan.MIXED_SCAN),
                        )
                    },
                    onPickText = { callback ->
                        importCallback = callback
                        importPicker.launch(
                            arrayOf(
                                "text/*",
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                "application/docx",
                                "application/x-docx",
                                "application/msword",
                                "application/octet-stream",
                            ),
                        )
                    },
                    onPickProjectPackage = { callback ->
                        projectPackageCallback = callback
                        projectPackagePicker.launch(
                            arrayOf(
                                PROJECT_PACKAGE_MEDIA_TYPE,
                                "application/zip",
                                "application/x-zip-compressed",
                                "application/octet-stream",
                            ),
                        )
                    },
                    onSaveExport = ::saveExport,
                )
            }
        }
    }
}
