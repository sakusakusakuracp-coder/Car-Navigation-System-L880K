package com.l880k.locationbridge.protocol

import org.json.JSONObject
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.EOFException

/** 4バイトのネットワーク順本文長 + UTF-8 JSONを扱う。 */
object FrameCodec {
    const val MAX_FRAME_BYTES = 65_536

    /** 長さ付きJSONフレームを読み、本文をJSONObjectへ変換する。 */
    fun read(input: DataInputStream): JSONObject? {
        val size = try { input.readInt() } catch (_: EOFException) { return null }
        require(size in 1..MAX_FRAME_BYTES) { "JSONフレームの本文長が不正です" }
        val body = ByteArray(size)
        input.readFully(body)
        return JSONObject(String(body, Charsets.UTF_8))
    }

    @Synchronized
    /** JSON本文に長さを付け、同時送信を防ぎながら出力する。 */
    fun write(output: DataOutputStream, message: JSONObject) {
        val body = message.toString().toByteArray(Charsets.UTF_8)
        require(body.isNotEmpty() && body.size <= MAX_FRAME_BYTES) { "JSONフレームが大きすぎます" }
        output.writeInt(body.size)
        output.write(body)
        output.flush()
    }
}
