package org.example.anam.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.RemoteInput
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread


class ReplyReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != AnamConfig.ACTION_REPLY) return

        val replyText = RemoteInput.getResultsFromIntent(intent)
            ?.getCharSequence(AnamConfig.KEY_TEXT_REPLY)
            ?.toString()
            ?.trim()
            .orEmpty()

        if (replyText.isEmpty()) {
            broadcastResult(context, "Empty reply — nothing sent.")
            return
        }
        val notifId = intent.getIntExtra(
            AnamConfig.EXTRA_NOTIFICATION_ID, AnamConfig.NOTIFICATION_ID
        )
        AnamNotifier.postMessage(context, "You: $replyText", notificationId = notifId)

        val pending = goAsync()
        thread(name = "anam-reply-post") {
            val result = postReply(replyText)
            Log.i(TAG, result)
            broadcastResult(context, result)
            pending.finish()
        }
    }

    private fun postReply(text: String): String {
        return try {
            val body = JSONObject()
                .put("device", "versa2")
                .put("text", text)
                .toString()

            val conn = URL(AnamConfig.REPLY_ENDPOINT).openConnection() as HttpURLConnection
            try {
                conn.requestMethod = "POST"
                conn.connectTimeout = 10_000
                conn.readTimeout = 10_000
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                if (AnamConfig.API_KEY.isNotEmpty()) {
                    conn.setRequestProperty("Authorization", "Bearer ${AnamConfig.API_KEY}")
                }
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }

                val code = conn.responseCode
                val stream = if (code in 200..299) conn.inputStream else conn.errorStream
                val response = stream?.bufferedReader()?.use(BufferedReader::readText).orEmpty()
                "POST $code: ${response.take(200)}"
            } finally {
                conn.disconnect()
            }
        } catch (e: Exception) {
            "POST failed: ${e.javaClass.simpleName}: ${e.message}"
        }
    }

    private fun broadcastResult(context: Context, result: String) {
        context.sendBroadcast(
            Intent(AnamConfig.ACTION_POST_RESULT)
                .setPackage(context.packageName)
                .putExtra(AnamConfig.EXTRA_RESULT_TEXT, result)
        )
    }

    companion object {
        private const val TAG = "AnamReplyReceiver"
    }
}
