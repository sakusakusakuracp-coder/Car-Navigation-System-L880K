package com.l880k.locationbridge

import android.location.Location
import android.location.LocationManager
import android.os.SystemClock
import com.l880k.locationbridge.model.ValidatedUpdate
import java.time.Instant

/** 管理対象のテスト位置プロバイダだけを準備・登録・解放する。 */
class LocationPublisher(private val locationManager: LocationManager, private val providerName: String) {
    private var prepared = false

    /** 管理対象のテスト位置プロバイダを作成または再利用する。 */
    fun prepare() {
        if (!prepared) {
            try {
                locationManager.addTestProvider(providerName, false, false, false, false, true, true, true,
                    LocationManager.POWER_LOW, LocationManager.ACCURACY_FINE)
            } catch (_: IllegalArgumentException) {
                // 既に自分が作ったproviderは再利用する。
            }
            locationManager.setTestProviderEnabled(providerName, true)
            prepared = true
        }
    }

    /** 検証済み位置をAndroidのテストプロバイダへ登録する。 */
    fun register(update: ValidatedUpdate, remainingMs: Long): Result<Unit> = runCatching {
        require(remainingMs > 0L) { "位置の有効期間が切れています" }
        prepare()
        val location = Location(providerName).apply {
            latitude = update.latitude
            longitude = update.longitude
            accuracy = update.accuracy
            time = runCatching { Instant.parse(update.message.optString("observed_at_utc")).toEpochMilli() }
                .getOrDefault(System.currentTimeMillis())
            elapsedRealtimeNanos = (SystemClock.elapsedRealtimeNanos() - update.ageMsAtSend * 1_000_000L)
                .coerceAtLeast(0L)
            if (update.message.optBoolean("speed_valid", false)) {
                speed = update.message.optDouble("speed_mps").toFloat()
            }
            if (update.message.optBoolean("bearing_valid", false)) {
                bearing = update.message.optDouble("bearing_deg").toFloat()
            }
        }
        locationManager.setTestProviderLocation(providerName, location)
    }

    /** 自分が準備したテストプロバイダを無効化して削除する。 */
    fun release(): Result<Unit> = runCatching {
        if (prepared) {
            locationManager.setTestProviderEnabled(providerName, false)
            locationManager.removeTestProvider(providerName)
            prepared = false
        }
    }

    /** テストプロバイダが登録済みか返す。 */
    fun isPrepared(): Boolean = prepared
}
