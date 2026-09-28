package com.l880k.locationbridge

import com.l880k.locationbridge.model.ValidatedUpdate
import org.json.JSONObject

/** Linuxから受けた位置を、値・接続世代・連番の順で検査する。 */
class PayloadValidator {
    fun validate(
        message: JSONObject,
        expectedBridgeBootId: String,
        expectedConnectionId: String,
        expectedEpoch: Long,
        lastTxSequence: Long
    ): Result<ValidatedUpdate> = runCatching {
        require(message.optString("bridge_boot_id") == expectedBridgeBootId) { "旧bridge_boot_idです" }
        require(message.optString("connection_id") == expectedConnectionId) { "旧connection_idです" }
        require(message.optLong("delivery_epoch", -1L) == expectedEpoch) { "旧delivery_epochです" }
        val txSequence = message.optLong("tx_sequence", -1L)
        require(txSequence > lastTxSequence) { "古いtx_sequenceです" }
        val position = message.getJSONObject("position")
        val latitude = position.getDouble("latitude_deg")
        val longitude = position.getDouble("longitude_deg")
        val accuracy = position.getDouble("horizontal_accuracy_m").toFloat()
        require(latitude in -90.0..90.0 && longitude in -180.0..180.0) { "緯度経度が範囲外です" }
        require(accuracy.isFinite() && accuracy > 0f) { "水平精度が不正です" }
        require(position.optBoolean("publishable", false)) { "publishable=falseです" }
        val age = position.optLong("age_ms_at_send", -1L)
        val validFor = position.optLong("valid_for_ms", -1L)
        require(age >= 0 && validFor > age) { "位置の期限が不正です" }
        ValidatedUpdate(
            position, latitude, longitude, accuracy, age, validFor,
            position.optString("estimate_id"), position.optString("method"), true
        )
    }
}
