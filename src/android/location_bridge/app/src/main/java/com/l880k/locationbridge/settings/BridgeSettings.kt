package com.l880k.locationbridge.settings

import android.content.Context

data class BridgeSettings(
    val port: Int = 8765,
    val sharedToken: String = "",
    val maxAgeMs: Long = 2_000L,
    val bridgeDelayBoundMs: Long = 100L,
    val providerName: String = "l880k_test"
) {
    companion object {
        /** SharedPreferencesから位置連携サービスの設定を読み込む。 */
        fun load(context: Context): BridgeSettings {
            val prefs = context.getSharedPreferences("bridge", Context.MODE_PRIVATE)
            return BridgeSettings(
                port = prefs.getInt("port", 8765),
                sharedToken = prefs.getString("shared_token", "") ?: "",
                maxAgeMs = prefs.getLong("max_age_ms", 2_000L),
                bridgeDelayBoundMs = prefs.getLong("bridge_delay_bound_ms", 100L),
                providerName = prefs.getString("provider_name", "l880k_test") ?: "l880k_test"
            )
        }
    }
}
