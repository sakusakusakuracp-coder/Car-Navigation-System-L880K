package com.l880k.locationbridge

import com.l880k.locationbridge.protocol.FrameCodec
import org.json.JSONObject
import java.io.DataOutputStream

/** 04へ受信・登録・失効結果を段階別に返す。 */
class StatusReporter(
    private val output: () -> DataOutputStream?,
    private val ids: () -> Map<String, Any?>
) {
    /** 位置の受信・登録結果を処理段階付きの確認応答として返す。 */
    fun ack(state: String, reason: String = "", extra: Map<String, Any?> = emptyMap()) {
        val json = JSONObject()
        json.put("event", "bridge.ack")
        ids().forEach { (key, value) -> if (value != null) json.put(key, value) }
        json.put("state", state)
        if (reason.isNotEmpty()) json.put("reason", reason)
        extra.forEach { (key, value) -> if (value != null) json.put(key, value) }
        output()?.let { FrameCodec.write(it, json) }
    }

    /** Android側の常駐サービス状態をLinux側へ通知する。 */
    fun status(state: String, reason: String = "", extra: Map<String, Any?> = emptyMap()) {
        val json = JSONObject()
        json.put("event", "bridge.status")
        ids().forEach { (key, value) -> if (value != null) json.put(key, value) }
        json.put("state", state)
        if (reason.isNotEmpty()) json.put("reason", reason)
        extra.forEach { (key, value) -> if (value != null) json.put(key, value) }
        output()?.let { FrameCodec.write(it, json) }
    }
}
