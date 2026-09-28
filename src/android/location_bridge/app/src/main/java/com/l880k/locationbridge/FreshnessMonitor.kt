package com.l880k.locationbridge

import android.os.SystemClock
import com.l880k.locationbridge.model.ValidatedUpdate

/** Linuxの単調時計を直接使わず、Android受信後の経過時間で期限を管理する。 */
class FreshnessMonitor(private val bridgeDelayBoundMs: Long, private val maxAgeMs: Long) {
    private var receivedElapsedMs: Long = 0
    private var receivedAgeMs: Long = 0
    private var validForMs: Long = 0

    /** 受信時刻と送信時点の経過時間から、位置の有効期限を更新する。 */
    fun accept(update: ValidatedUpdate) {
        receivedElapsedMs = SystemClock.elapsedRealtime()
        receivedAgeMs = update.ageMsAtSend + bridgeDelayBoundMs
        validForMs = minOf(update.validForMs, maxAgeMs)
    }

    /** 現在時点で残っている位置の有効時間をミリ秒で返す。 */
    fun remainingMs(nowElapsedMs: Long = SystemClock.elapsedRealtime()): Long {
        if (receivedElapsedMs == 0L) return 0L
        return validForMs - receivedAgeMs - (nowElapsedMs - receivedElapsedMs)
    }

    /** 位置をAndroidへ登録してよい期限内か判定する。 */
    fun isFresh(): Boolean = remainingMs() > 0L
}
