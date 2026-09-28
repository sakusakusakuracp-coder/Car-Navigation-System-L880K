import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Item {
    id: root
    property bool saving: uiFeatures.busy.indexOf("preferences") >= 0 || uiFeatures.busy.indexOf("restore") >= 0 || uiFeatures.busy.indexOf("backup") >= 0
    property bool cameraBusy: uiFeatures.busy.indexOf("camera_read") >= 0 || uiFeatures.busy.indexOf("camera_apply") >= 0
    property bool recordingEdit: false
    property bool recordingEnabled: false
    property int segmentSeconds: 60
    Component.onCompleted: uiFeatures.read_camera_settings()
    Connections {
        target: uiFeatures
        function onChanged() {
            if (!root.recordingEdit && uiFeatures.cameraSettings.recording_enabled !== undefined) {
                root.recordingEnabled = uiFeatures.cameraSettings.recording_enabled
                root.segmentSeconds = uiFeatures.cameraSettings.segment_duration_s
            }
        }
    }
    Rectangle { anchors.fill: parent; color: Theme.background }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 16; spacing: 8
        RowLayout {
            Layout.fillWidth: true
            Label { text: "設定"; font.pixelSize: 26; font.bold: true; color: Theme.text }
            Item { Layout.fillWidth: true }
            Button { text: "警告履歴"; onClicked: screenController.request_screen("warnings") }
        }
        Label { Layout.fillWidth: true; text: uiFeatures.notice; color: Theme.muted; wrapMode: Text.Wrap }
        Label { Layout.fillWidth: true; visible: !uiFeatures.parked; text: uiFeatures.drivingRestriction; color: Theme.warning; wrapMode: Text.Wrap }
        ScrollView {
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
            ColumnLayout {
                width: parent.width; spacing: 12; enabled: uiFeatures.operationAllowed
                Label { text: "表示・警告"; color: Theme.text; font.pixelSize: 20 }
                Switch { text: "ダークテーマ"; checked: uiFeatures.draft.night_mode; enabled: !root.saving; onToggled: uiFeatures.edit("night_mode", checked) }
                Switch { text: "画面切替アニメーション"; checked: uiFeatures.draft.animations; enabled: !root.saving; onToggled: uiFeatures.edit("animations", checked) }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "起動画面"; color: Theme.text; Layout.fillWidth: true }
                    ComboBox {
                        property var ids: ["home", "navigation", "camera", "audio", "vehicle", "settings"]
                        model: ["ホーム", "ナビ", "カメラ", "音楽", "車両情報", "設定"]
                        currentIndex: ids.indexOf(uiFeatures.draft.startup_screen); enabled: !root.saving
                        onActivated: uiFeatures.edit("startup_screen", ids[currentIndex])
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "ナビアプリ"; color: Theme.text; Layout.fillWidth: true }
                    ComboBox {
                        property var ids: ["osmand", "livi"]
                        model: ["OsmAnd（Waydroid）", "LIVI（ネイティブ）"]
                        currentIndex: ids.indexOf(appState.navigationMode)
                        onActivated: screenController.set_navigation_mode(ids[currentIndex])
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "水温警告 (°C)"; color: Theme.text; Layout.fillWidth: true }
                    SpinBox { from: 80; to: 130; value: uiFeatures.draft.coolant_warning; enabled: !root.saving; onValueModified: uiFeatures.edit("coolant_warning", value) }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "低電圧警告 (V)"; color: Theme.text; Layout.fillWidth: true }
                    SpinBox {
                        from: 100; to: 150; value: Math.round(uiFeatures.draft.voltage_warning * 10); enabled: !root.saving
                        textFromValue: function(value, locale) { return (value / 10).toFixed(1) }
                        valueFromText: function(text, locale) { return Math.round(Number(text) * 10) }
                        onValueModified: uiFeatures.edit("voltage_warning", value / 10)
                    }
                }
                Flow {
                    Layout.fillWidth: true; spacing: 8
                    Button { text: root.saving ? "保存中…" : "適用・保存"; enabled: !root.saving; onClicked: uiFeatures.apply() }
                    Button { text: "取消"; enabled: !root.saving; onClicked: uiFeatures.cancel() }
                    Button { text: "再読込"; enabled: !root.saving; onClicked: reloadDialog.open() }
                    Button { text: "初期値"; enabled: !root.saving; onClicked: uiFeatures.defaults() }
                    Button { text: "バックアップ"; enabled: !root.saving && uiFeatures.busy.indexOf("backup") < 0; onClicked: uiFeatures.backup() }
                    Button { text: "復元"; enabled: !root.saving; onClicked: restoreDialog.open() }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.muted }
                Label { text: "カメラ・録画"; color: Theme.text; font.pixelSize: 20 }
                Switch { text: "録画を有効にする"; checked: root.recordingEnabled; enabled: uiFeatures.cameraSettings.recording_enabled !== undefined && !root.cameraBusy; onToggled: { root.recordingEdit = true; root.recordingEnabled = checked } }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: "録画分割時間"; color: Theme.text; Layout.fillWidth: true }
                    ComboBox { model: ["1分", "2分", "3分"]; currentIndex: root.segmentSeconds / 60 - 1; enabled: uiFeatures.cameraSettings.recording_enabled !== undefined && !root.cameraBusy; onActivated: { root.recordingEdit = true; root.segmentSeconds = (currentIndex + 1) * 60 } }
                }
                Flow {
                    Layout.fillWidth: true; spacing: 8
                    Button { text: "録画設定を適用"; enabled: root.recordingEdit && !root.cameraBusy; onClicked: { uiFeatures.apply_camera(root.recordingEnabled, root.segmentSeconds); root.recordingEdit = false } }
                    Button { text: "設定を再取得"; enabled: uiFeatures.busy.indexOf("camera_read") < 0 && uiFeatures.busy.indexOf("camera_apply") < 0; onClicked: { root.recordingEdit = false; uiFeatures.read_camera_settings() } }
                    Button { text: "録画一覧"; onClicked: screenController.request_screen("recordings") }
                }
                Label { Layout.fillWidth: true; text: "保存容量不足時は録画を停止します。未送信の動画は自動削除しません。"; color: Theme.muted; wrapMode: Text.Wrap }
                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.muted }
                Label { text: "クラウド送信"; color: Theme.text; font.pixelSize: 20 }
                Label { text: appState.uploadStatus + " " + appState.uploadPending; color: Theme.muted }
                RowLayout {
                    Button { text: "送信を一時停止"; enabled: uiFeatures.busy.indexOf("drive") < 0; onClicked: uiFeatures.pause_upload(true) }
                    Button { text: "送信を再開"; enabled: uiFeatures.busy.indexOf("drive") < 0; onClicked: uiFeatures.pause_upload(false) }
                }
                Label { Layout.fillWidth: true; text: "送信先・許可ネットワーク・検証後の削除は運用設定に従います。"; color: Theme.muted; wrapMode: Text.Wrap }
                Item { Layout.preferredHeight: 12 }
            }
        }
    }
    Dialog { id: restoreDialog; anchors.centerIn: parent; modal: true; title: "保存済み設定を復元しますか？"; standardButtons: Dialog.Ok | Dialog.Cancel; onAccepted: uiFeatures.restore() }
    Dialog { id: reloadDialog; anchors.centerIn: parent; modal: true; title: "未保存の編集を破棄して再読込しますか？"; standardButtons: Dialog.Ok | Dialog.Cancel; onAccepted: uiFeatures.reload_preferences() }
}
