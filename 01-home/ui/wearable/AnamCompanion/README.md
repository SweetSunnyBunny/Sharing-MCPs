# Optional Android wearable bridge

This Android phone app relays Anam notifications to a compatible paired watch
and returns inline replies to Anam. It polls the existing `/api/wearable/outbox`
route and sends replies to `/api/wearable/reply`. It is optional; the browser UI
works without it.

This is an optional Android build, not an installable app-store download. Get
the main Anam UI working first. You need an Android phone running Android 8.0
(API 26) or newer, a paired watch that supports notification replies, and an
authenticated Anam HTTPS address reachable from that phone. The computer's
`127.0.0.1` address will not reach the computer from your phone.

## 1. Install the build tools on your Windows computer

1. Install a Windows JDK 17 from [Adoptium](https://adoptium.net/temurin/releases/?version=17).
   Enable its `JAVA_HOME` and PATH options in the installer.
2. Install [Android Studio](https://developer.android.com/studio). Open its SDK
   Manager, install **Android 14 / API 34** and the Android SDK Build-Tools.
   Note the SDK location shown there; a typical location is
   `C:/Users/YourName/AppData/Local/Android/Sdk`.
3. Download the **Gradle 8.9 binary ZIP** from
   [Gradle's releases page](https://gradle.org/releases/) and extract it to
   `C:\Tools\gradle-8.9`. Check that `C:\Tools\gradle-8.9\bin\gradle.bat` exists.
   If you choose another location, substitute that path in the commands below.
4. Open a new PowerShell window in this `AnamCompanion` folder and check:

```powershell
java -version
& 'C:\Tools\gradle-8.9\bin\gradle.bat' --version
```

**Expected result:** Java 17 and Gradle 8.9 are reported. A Gradle wrapper is not
included, so there is no `gradlew` command until you add your own wrapper. The
project itself pins Android Gradle Plugin 8.5.2 and Kotlin 1.9.24.

## 2. Set your server address and key

Your Anam server must have its own `ANAM_API_KEY` configured. Use that same key
here; it is separate from provider API keys and Discord OAuth credentials.

```powershell
if (-not (Test-Path local.properties)) { Copy-Item local.properties.example local.properties }
notepad local.properties
```

Replace all three example values:

```properties
sdk.dir=C:/Users/YourName/AppData/Local/Android/Sdk
anamBaseUrl=https://your-own-anam-address.example.com
anamApiKey=YOUR-OWN-ANAM-API-KEY
```

Use the actual SDK directory from step 1, your real reachable HTTPS origin, and
your own key. Use forward slashes in `sdk.dir`. Save and close the file.

## 3. Build the phone application

From this same folder:

```powershell
& 'C:\Tools\gradle-8.9\bin\gradle.bat' :app:assembleDebug
```

The first build downloads dependencies. **Success means** `BUILD SUCCESSFUL`
appears and this file exists:

```text
app/build/outputs/apk/debug/app-debug.apk
```

## 4. Install it on your phone

1. Copy `app-debug.apk` to your own phone using USB or your own file-transfer tool.
2. Open that file on the phone and accept installation of your own build. Android
   may ask you to allow installation from the file manager you are using.
3. Open **Anam** on the phone and grant notification permission.
4. In your watch's companion app, enable notification forwarding for Anam.

## 5. Check both directions

1. In the phone app, tap **Send a test to my wrist**.
2. Confirm the notification appears on the phone and the paired watch.
3. Reply from the watch and check that the reply reaches Anam on your server.

The first notification is a local device test. A successful reply is the check
that the phone can reach and authenticate with your server. Watch reply support
depends on the phone/watch integration.

## Troubleshooting

| What happened | What to check |
| --- | --- |
| Java or Gradle is not found | Reopen PowerShell after installing Java; use the full Gradle path from step 1. |
| SDK location not found | Correct `sdk.dir` in `local.properties`; use forward slashes. |
| API 34 is missing | Install the Android 14/API 34 platform in Android Studio's SDK Manager. |
| Phone receives a notification but watch does not | Enable Anam in your watch app's notification forwarding settings. |
| Reply cannot reach the server | Check your real HTTPS address, phone connectivity and matching `ANAM_API_KEY`. A phone cannot use the computer's localhost address. |
| Address or key changed | Update `local.properties`, rebuild, and install the new APK. Those values are compiled into the app. |

The example package name is `org.example.anam.companion`. Customize it before
distributing your own app. The URL and API key are compiled into your local build;
share the source template, not an APK built with your credentials. This is the
current notification/reply client; it does not require old wake-word models or
speech-recognition AAR files.
