package org.example.anam.companion

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL


class OutboxPollService : Service() {

    private lateinit var handlerThread: HandlerThread
    private lateinit var handler: Handler
    private var currentDelayMs = AnamConfig.POLL_INTERVAL_MS

    private val pollTask = object : Runnable {
        override fun run() {
            val ok = pollOnce()
            currentDelayMs = if (ok) {
                AnamConfig.POLL_INTERVAL_MS
            } else {
                (currentDelayMs * 2).coerceAtMost(AnamConfig.POLL_BACKOFF_MAX_MS)
            }
            handler.postDelayed(this, currentDelayMs)
        }
    }

    override fun onCreate() {
        super.onCreate()
        ensureServiceChannel()
        startForeground(AnamConfig.SERVICE_NOTIFICATION_ID, buildServiceNotification())
        handlerThread = HandlerThread("anam-outbox-poll").also { it.start() }
        handler = Handler(handlerThread.looper)
        handler.post(pollTask)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        handlerThread.quitSafely()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    
    private fun pollOnce(): Boolean {
        val prefs = getSharedPreferences(AnamConfig.PREFS_NAME, MODE_PRIVATE)
        val lastId = prefs.getInt(AnamConfig.PREF_LAST_OUTBOX_ID, 0)
        return try {
            val conn = URL("${AnamConfig.OUTBOX_ENDPOINT}?since_id=$lastId")
                .openConnection() as HttpURLConnection
            try {
                conn.requestMethod = "GET"
                conn.connectTimeout = 10_000
                conn.readTimeout = 10_000
                if (AnamConfig.API_KEY.isNotEmpty()) {
                    conn.setRequestProperty("Authorization", "Bearer ${AnamConfig.API_KEY}")
                }
                val code = conn.responseCode
                if (code !in 200..299) {
                    Log.w(TAG, "outbox poll HTTP $code")
                    return false
                }
                val body = conn.inputStream.bufferedReader().use(BufferedReader::readText)
                val json = JSONObject(body)
                val messages = json.optJSONArray("messages") ?: return true
                var newLastId = lastId
                for (i in 0 until messages.length()) {
                    val msg = messages.getJSONObject(i)
                    val id = msg.optInt("id", 0)
                    val text = msg.optString("text", "")
                    val from = msg.optString("from_identity", "Anam").ifEmpty { "Anam" }
                    if (id <= 0 || text.isEmpty()) continue
                    AnamNotifier.postMessage(
                        this,
                        text,
                        senderName = from,
                        notificationId = OUTBOX_NOTIFICATION_BASE + (id % 500),
                    )
                    if (id > newLastId) newLastId = id
                }
                if (newLastId != lastId) {
                    prefs.edit().putInt(AnamConfig.PREF_LAST_OUTBOX_ID, newLastId).apply()
                }
                true
            } finally {
                conn.disconnect()
            }
        } catch (e: Exception) {
            Log.w(TAG, "outbox poll failed: ${e.javaClass.simpleName}: ${e.message}")
            false
        }
    }

    private fun ensureServiceChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                AnamConfig.SERVICE_CHANNEL_ID,
                "Anam bridge",
                NotificationManager.IMPORTANCE_MIN
            ).apply {
                description = "Keeps the Anam wearable connection active"
                setShowBadge(false)
            }
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }

    private fun buildServiceNotification(): android.app.Notification {
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        } else {
            PendingIntent.FLAG_UPDATE_CURRENT
        }
        val openApp = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), flags
        )
        return NotificationCompat.Builder(this, AnamConfig.SERVICE_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
            .setContentTitle("Anam wearable connection active")
            .setContentText("Listening for messages from Anam")
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setOngoing(true)
            .setContentIntent(openApp)
            .build()
    }

    companion object {
        private const val TAG = "AnamOutboxPoll"
        private const val OUTBOX_NOTIFICATION_BASE = 2000

        fun start(context: Context) {
            val intent = Intent(context, OutboxPollService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }
}
