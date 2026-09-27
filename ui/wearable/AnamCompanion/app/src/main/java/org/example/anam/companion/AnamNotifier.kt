package org.example.anam.companion

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.Person
import androidx.core.app.RemoteInput


object AnamNotifier {

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                AnamConfig.CHANNEL_ID,
                "Anam messages",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Messages from Anam that can be answered from the watch"
                enableVibration(true)
            }
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(channel)
        }
    }

    fun postMessage(
        context: Context,
        messageText: String,
        senderName: String = "Anam",
        notificationId: Int = AnamConfig.NOTIFICATION_ID,
    ) {
        ensureChannel(context)

        val remoteInput = RemoteInput.Builder(AnamConfig.KEY_TEXT_REPLY)
            .setLabel("Reply to Anam")
            .build()

        val replyIntent = Intent(context, ReplyReceiver::class.java).apply {
            action = AnamConfig.ACTION_REPLY
            putExtra(AnamConfig.EXTRA_NOTIFICATION_ID, notificationId)
        }
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE
        } else {
            PendingIntent.FLAG_UPDATE_CURRENT
        }
        val replyPendingIntent =
            PendingIntent.getBroadcast(context, notificationId, replyIntent, flags)

        val replyAction = NotificationCompat.Action.Builder(
            android.R.drawable.ic_menu_send,
            "Reply to Anam",
            replyPendingIntent
        )
            .addRemoteInput(remoteInput)
            .setAllowGeneratedReplies(true)
            .setSemanticAction(NotificationCompat.Action.SEMANTIC_ACTION_REPLY)
            .build()

        val anam = Person.Builder().setName(senderName).build()
        val you = Person.Builder().setName("You").build()

        val style = NotificationCompat.MessagingStyle(you)
            .addMessage(messageText, System.currentTimeMillis(), anam)

        val notification = NotificationCompat.Builder(context, AnamConfig.CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_email)
            .setStyle(style)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .addAction(replyAction)
            .setAutoCancel(true)
            .build()

        try {
            NotificationManagerCompat.from(context)
                .notify(notificationId, notification)
        } catch (_: SecurityException) {
        }
    }
}
