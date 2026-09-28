package com.l880k.locationbridge.model

data class ValidatedUpdate(
    val message: org.json.JSONObject,
    val latitude: Double,
    val longitude: Double,
    val accuracy: Float,
    val ageMsAtSend: Long,
    val validForMs: Long,
    val estimateId: String,
    val method: String,
    val publishable: Boolean
)
