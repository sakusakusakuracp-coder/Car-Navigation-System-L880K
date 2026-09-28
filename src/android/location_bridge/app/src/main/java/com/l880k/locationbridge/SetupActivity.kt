package com.l880k.locationbridge

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.app.Activity

/** 設定・権限・常駐サービスの状態を確認する最小画面。 */
class SetupActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val status = TextView(this).apply {
            text = "05 Android位置情報連携\n\n模擬位置の許可はAndroidの設定で確認してください。"
            textSize = 18f
            setPadding(32, 32, 32, 32)
        }
        val settingsButton = Button(this).apply {
            text = "Androidの開発者向け設定を開く"
            setOnClickListener { startActivity(Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)) }
        }
        val startButton = Button(this).apply {
            text = "位置情報連携サービスを開始"
            setOnClickListener {
                startForegroundService(Intent(this@SetupActivity, ReceiverService::class.java))
                status.text = "05 Android位置情報連携\n\nサービスを起動しました。Linuxからの接続を待機中です。"
            }
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(status)
            addView(settingsButton)
            addView(startButton)
        })
    }
}
