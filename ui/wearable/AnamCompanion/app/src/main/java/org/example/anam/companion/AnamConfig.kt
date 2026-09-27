package org.example.anam.companion


object AnamConfig {
    val BASE_URL = BuildConfig.ANAM_BASE_URL.trimEnd('/')
    val REPLY_ENDPOINT = "$BASE_URL/api/wearable/reply"
    val OUTBOX_ENDPOINT = "$BASE_URL/api/wearable/outbox"

    
    val API_KEY: String = BuildConfig.ANAM_API_KEY
    const val CHANNEL_ID = "anam_messages"
    const val NOTIFICATION_ID = 1001
    const val SERVICE_CHANNEL_ID = "anam_bridge"
    const val SERVICE_NOTIFICATION_ID = 42
    const val POLL_INTERVAL_MS = 60_000L          // normal cadence: every ~60s
    const val POLL_BACKOFF_MAX_MS = 15 * 60_000L  // failure backoff cap: 15 min
    const val PREFS_NAME = "anam_companion"
    const val PREF_LAST_OUTBOX_ID = "last_outbox_id"
    const val KEY_TEXT_REPLY = "anam_key_text_reply"
    const val ACTION_REPLY = "org.example.anam.companion.ACTION_REPLY"
    const val EXTRA_NOTIFICATION_ID = "anam_notification_id"
    const val ACTION_POST_RESULT = "org.example.anam.companion.ACTION_POST_RESULT"
    const val EXTRA_RESULT_TEXT = "result_text"
}
