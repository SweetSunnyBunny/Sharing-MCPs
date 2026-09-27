package org.example.anam.companion

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {

    private lateinit var statusView: TextView
    private lateinit var connectionView: TextView

    private fun dp(value: Int): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), resources.displayMetrics
    ).toInt()

    private fun color(id: Int): Int = ContextCompat.getColor(this, id)

    private val requestNotifPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            statusView.text = if (granted) {
                "Notifications allowed. The wire is open."
            } else {
                "Notifications are off, so messages can't show.\n" +
                    "Turn them on: Settings › Apps › Anam › Notifications."
            }
        }

    private val resultReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val text = intent.getStringExtra(AnamConfig.EXTRA_RESULT_TEXT) ?: return
            statusView.text = text
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val page = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(24), dp(48), dp(24), dp(40))
            background = GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                intArrayOf(
                    color(R.color.anam_page_top),
                    color(R.color.anam_page_mid),
                    color(R.color.anam_page_bottom)
                )
            )
        }

        val title = TextView(this).apply {
            text = "Anam"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 44f)
            setTypeface(Typeface.SERIF, Typeface.BOLD)
            setTextColor(color(R.color.anam_text_primary))
            letterSpacing = 0.04f
            gravity = Gravity.CENTER
        }

        val subtitle = TextView(this).apply {
            text = "the wire to your wrist"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setTypeface(Typeface.SERIF, Typeface.ITALIC)
            setTextColor(color(R.color.anam_text_muted))
            gravity = Gravity.CENTER
            setPadding(0, dp(4), 0, dp(28))
        }

        connectionView = TextView(this).apply {
            text = "●  checking for home…"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            setTextColor(color(R.color.anam_wait))
            gravity = Gravity.CENTER
            setPadding(dp(18), dp(9), dp(18), dp(9))
            background = pill(color(R.color.anam_card), color(R.color.anam_card_border))
        }

        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(22), dp(22), dp(22), dp(22))
            background = GradientDrawable().apply {
                setColor(color(R.color.anam_card))
                cornerRadius = dp(22).toFloat()
                setStroke(dp(1), color(R.color.anam_card_border))
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(22) }
        }

        val cardLabel = TextView(this).apply {
            text = "LAST ACROSS THE WIRE"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            setTextColor(color(R.color.anam_text_muted))
            letterSpacing = 0.16f
            setPadding(0, 0, 0, dp(10))
        }

        statusView = TextView(this).apply {
            text = "Nothing yet today.\n\n" +
                "Reply to an Anam notification from your watch and it lands straight in the house."
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            setTextColor(color(R.color.anam_text_secondary))
            setLineSpacing(dp(4).toFloat(), 1f)
        }

        val button = Button(this).apply {
            text = "Send a test to my wrist"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            setTypeface(Typeface.DEFAULT_BOLD)
            setTextColor(color(R.color.anam_text_primary))
            isAllCaps = false
            stateListAnimator = null
            background = GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                intArrayOf(color(R.color.anam_button_top), color(R.color.anam_button_bottom))
            ).apply {
                cornerRadius = dp(18).toFloat()
                setStroke(dp(1), color(R.color.anam_accent))
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(56)
            ).apply { topMargin = dp(26) }
            setOnClickListener {
                AnamNotifier.postMessage(
                    this@MainActivity,
                    "This is a test from Anam. Reply from your wrist!"
                )
                statusView.text = "Test sent. Pull the shade down or check your watch, " +
                    "then reply — whatever you send comes back here."
                checkConnection()
            }
        }

        val footer = TextView(this).apply {
            text = "home · " + hostOf(AnamConfig.BASE_URL)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setTextColor(color(R.color.anam_text_muted))
            alpha = 0.75f
            gravity = Gravity.CENTER
            setPadding(0, dp(26), 0, 0)
        }

        card.addView(cardLabel)
        card.addView(statusView)

        page.addView(title)
        page.addView(subtitle)
        page.addView(connectionView)
        page.addView(card)
        page.addView(button)
        page.addView(footer)

        val scroller = ScrollView(this).apply {
            isFillViewport = true
            setBackgroundColor(color(R.color.anam_page_mid))
            addView(page)
        }
        setContentView(scroller)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            window.statusBarColor = color(R.color.anam_page_top)
            window.navigationBarColor = color(R.color.anam_page_bottom)
        }

        AnamNotifier.ensureChannel(this)
        maybeRequestNotificationPermission()
        OutboxPollService.start(this)
    }

    
    private fun pill(fill: Int, stroke: Int): GradientDrawable = GradientDrawable().apply {
        setColor(fill)
        cornerRadius = dp(999).toFloat()
        setStroke(dp(1), stroke)
    }

    private fun hostOf(url: String): String = try {
        URL(url).host
    } catch (_: Exception) {
        url
    }

    
    private fun checkConnection() {
        connectionView.text = "●  checking for home…"
        connectionView.setTextColor(color(R.color.anam_wait))
        Thread {
            var reachable = false
            try {
                val conn = URL("${AnamConfig.BASE_URL}/api/wearable/status")
                    .openConnection() as HttpURLConnection
                conn.requestMethod = "GET"
                conn.connectTimeout = 4000
                conn.readTimeout = 4000
                if (AnamConfig.API_KEY.isNotBlank()) {
                    conn.setRequestProperty("Authorization", "Bearer ${AnamConfig.API_KEY}")
                }
                reachable = conn.responseCode in 200..299
                conn.disconnect()
            } catch (_: Exception) {
                reachable = false
            }
            runOnUiThread {
                if (reachable) {
                    connectionView.text = "●  connected to home"
                    connectionView.setTextColor(color(R.color.anam_ok))
                } else {
                    connectionView.text = "●  can't reach server — check your connection"
                    connectionView.setTextColor(color(R.color.anam_bad))
                }
            }
        }.start()
    }

    override fun onStart() {
        super.onStart()
        val filter = IntentFilter(AnamConfig.ACTION_POST_RESULT)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(resultReceiver, filter, RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(resultReceiver, filter)
        }
    }

    override fun onResume() {
        super.onResume()
        checkConnection()
    }

    override fun onStop() {
        super.onStop()
        unregisterReceiver(resultReceiver)
    }

    private fun maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                this, Manifest.permission.POST_NOTIFICATIONS
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) {
                requestNotifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
    }
}
