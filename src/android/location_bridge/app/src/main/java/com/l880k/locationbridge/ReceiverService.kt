package com.l880k.locationbridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.location.LocationManager
import android.os.Build
import android.os.IBinder
import com.l880k.locationbridge.protocol.FrameCodec
import com.l880k.locationbridge.settings.BridgeSettings
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.net.ServerSocket
import java.net.Socket
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** 04との接続を受け、位置登録・失効・結果通知を直列に実行する常駐サービス。 */
class ReceiverService : Service() {
    private val running = AtomicBoolean(false)
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var settings: BridgeSettings
    private lateinit var publisher: LocationPublisher
    private lateinit var validator: PayloadValidator
    private lateinit var freshness: FreshnessMonitor
    private var server: ServerSocket? = null
    private var client: Socket? = null
    private var output: DataOutputStream? = null
    private var bridgeBootId = ""
    private var connectionId = ""
    private var deliveryEpoch = -1L
    private var lastTxSequence = -1L
    private var receiverBootId = UUID.randomUUID().toString()

    /** Android常駐サービスと接続受付、期限監視を初期化する。 */
    override fun onCreate() {
        super.onCreate()
        settings = BridgeSettings.load(this)
        publisher = LocationPublisher(getSystemService(LocationManager::class.java), settings.providerName)
        validator = PayloadValidator()
        freshness = FreshnessMonitor(settings.bridgeDelayBoundMs, settings.maxAgeMs)
        createNotificationChannel()
        startForeground(1001, notification())
        running.set(true)
        executor.execute { acceptLoop() }
    }

    /** プロセスが終了した場合もサービスを再作成する方針を返す。 */
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    /** 接続受付と位置プロバイダを停止してサービス資源を解放する。 */
    override fun onDestroy() {
        running.set(false)
        try { server?.close() } catch (_: Exception) { }
        try { client?.close() } catch (_: Exception) { }
        publisher.release()
        executor.shutdownNow()
        super.onDestroy()
    }

    /** バインド型ではなく開始型サービスであることを示す。 */
    override fun onBind(intent: Intent?): IBinder? = null

    /** TCP接続を受け付け、接続ごとのJSONフレーム処理を開始する。 */
    private fun acceptLoop() {
        try {
            ServerSocket(settings.port).also { server = it }.use { listener ->
                while (running.get()) {
                    val accepted = listener.accept()
                    client?.close()
                    client = accepted
                    handleClient(accepted)
                }
            }
        } catch (_: Exception) {
            if (running.get()) stopSelf()
        }
    }

    /** 1接続からのフレームを順に読み、応答を返す。 */
    private fun handleClient(socket: Socket) {
        try {
            socket.use { current ->
                val input = DataInputStream(BufferedInputStream(current.getInputStream()))
                output = DataOutputStream(BufferedOutputStream(current.getOutputStream()))
                val hello = FrameCodec.read(input) ?: return
                if (hello.optString("event") != "bridge.hello" ||
                    (settings.sharedToken.isNotEmpty() && hello.optString("token") != settings.sharedToken)) {
                    StatusReporter({ output }, { ids() }).ack("REJECTED", "hello認証に失敗しました")
                    return
                }
                bridgeBootId = hello.optString("bridge_boot_id")
                connectionId = hello.optString("connection_id")
                deliveryEpoch = 0L
                lastTxSequence = -1L
                StatusReporter({ output }, { ids() }).ack("WAIT_POSITION", "Android位置連携の準備が完了しました")
                while (running.get()) {
                    val message = FrameCodec.read(input) ?: break
                    handleMessage(message)
                }
            }
        } catch (_: Exception) {
            // 切断や不正フレームは現在の接続だけを失効させ、サービスは待機を継続する。
        }
        output = null
    }

    /** 受信イベント種別を判定し、更新・失効処理へ振り分ける。 */
    private fun handleMessage(message: JSONObject) {
        when (message.optString("event")) {
            "position.update" -> handleUpdate(message)
            "position.invalidate" -> handleInvalidate(message)
            "bridge.ping" -> StatusReporter({ output }, { ids() }).ack("PONG")
        }
    }

    /** 位置更新を検証し、鮮度確認後にAndroidへ登録する。 */
    private fun handleUpdate(message: JSONObject) {
        val currentEpoch = message.optLong("delivery_epoch", -1L)
        if (currentEpoch != deliveryEpoch) {
            StatusReporter({ output }, { ids(message) }).ack("REJECTED", "配送世代が一致しません")
            return
        }
        val result = validator.validate(message, bridgeBootId, connectionId, deliveryEpoch, lastTxSequence)
        if (result.isFailure) {
            StatusReporter({ output }, { ids(message) }).ack("REJECTED", result.exceptionOrNull()?.message ?: "入力検査に失敗しました")
            return
        }
        val update = result.getOrThrow()
        freshness.accept(update)
        if (!freshness.isFresh()) {
            StatusReporter({ output }, { ids(message) }).ack("REJECTED", "位置の有効期間が切れています")
            publisher.release()
            return
        }
        lastTxSequence = message.optLong("tx_sequence")
        StatusReporter({ output }, { ids(message) }).ack("RECEIVED", extra = mapOf("position_ref" to update.estimateId))
        val registration = publisher.register(update, freshness.remainingMs())
        if (registration.isSuccess) {
            StatusReporter({ output }, { ids(message) }).ack("REGISTERED", extra = mapOf("position_ref" to update.estimateId))
            StatusReporter({ output }, { ids(message) }).status("ACTIVE", extra = mapOf("position_ref" to update.estimateId, "position_valid" to true))
        } else {
            StatusReporter({ output }, { ids(message) }).ack("FAILED", registration.exceptionOrNull()?.message ?: "位置登録に失敗しました")
        }
    }

    /** Linux側の無効化通知を受け、登録済み位置を解放する。 */
    private fun handleInvalidate(message: JSONObject) {
        val epoch = message.optLong("delivery_epoch", -1L)
        if (epoch < deliveryEpoch) return
        deliveryEpoch = epoch
        lastTxSequence = message.optLong("tx_sequence", lastTxSequence)
        val result = publisher.release()
        StatusReporter({ output }, { ids(message) }).ack(
            if (result.isSuccess) "INVALIDATED" else "FAILED",
            result.exceptionOrNull()?.message ?: message.optString("reason", "位置を無効化しました")
        )
        StatusReporter({ output }, { ids(message) }).status("WAIT_POSITION", "位置を無効化しました", mapOf("position_valid" to false))
    }

    /** 応答へ付加する起動世代、連番、セッション識別子をまとめる。 */
    private fun ids(message: JSONObject? = null): Map<String, Any?> = mapOf(
        "schema_version" to 1,
        "receiver_boot_id" to receiverBootId,
        "bridge_boot_id" to bridgeBootId,
        "connection_id" to connectionId,
        "delivery_epoch" to deliveryEpoch,
        "tx_sequence" to message?.optLong("tx_sequence")
    )

    /** Android 8以降のForeground Service通知チャンネルを作成する。 */
    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel("location_bridge", "位置情報連携", NotificationManager.IMPORTANCE_LOW)
            )
        }
    }

    /** 常駐サービスの稼働を示す通知を作成する。 */
    private fun notification(): Notification = if (Build.VERSION.SDK_INT >= 26) {
        Notification.Builder(this, "location_bridge").setContentTitle("L880K位置情報連携").setContentText("Linuxからの位置を待機中").setSmallIcon(android.R.drawable.ic_menu_mylocation).build()
    } else {
        Notification.Builder(this).setContentTitle("L880K位置情報連携").setContentText("Linuxからの位置を待機中").setSmallIcon(android.R.drawable.ic_menu_mylocation).build()
    }
}
