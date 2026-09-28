import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Item {
    id: root
    Rectangle { anchors.fill: parent; color: Theme.background }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 16; spacing: 10
        RowLayout {
            Label { text: "車両情報"; font.pixelSize: 26; font.bold: true; color: Theme.text }
            Item { Layout.fillWidth: true }
            Label { text: "OBD2: " + appState.obdConnectionLabel + " / " + appState.obdPollingMode; color: Theme.muted }
            Button { text: "警告履歴"; onClicked: screenController.request_screen("warnings") }
        }
        ScrollView {
            Layout.fillWidth: true; Layout.fillHeight: true; contentWidth: availableWidth; clip: true
            ColumnLayout {
                width: parent.width; spacing: 14
                GridLayout {
                    Layout.fillWidth: true; columns: root.width < 1000 ? 2 : 4; columnSpacing: 12; rowSpacing: 12
                    Repeater {
                        model: [
                            {label: "車速", value: appState.vehicleSpeed},
                            {label: "回転数", value: appState.engineRpm + " rpm"},
                            {label: "水温", value: appState.coolantTemperature + " °C"},
                            {label: "車両電圧", value: appState.ecuVoltage + " V"},
                            {label: "吸気温", value: appState.intakeTemperature + " °C"},
                            {label: "スロットル開度", value: appState.throttlePosition + " %"},
                            {label: "エンジン負荷", value: appState.engineLoad + " %"},
                            {label: "燃料残量", value: appState.fuelLevel + " %"}]
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true; Layout.preferredWidth: 160; Layout.preferredHeight: 92
                            color: Theme.panel; radius: 4
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 12
                                Label { text: modelData.label; color: Theme.muted }
                                Label { Layout.fillWidth: true; text: modelData.value; font.pixelSize: 24; color: Theme.text; elide: Text.ElideRight }
                            }
                        }
                    }
                }
                Label { text: "システム状態"; color: Theme.text; font.pixelSize: 20 }
                Label { Layout.fillWidth: true; text: "ナビ管理: " + appState.serviceStatus; color: Theme.text; wrapMode: Text.Wrap }
                Label { Layout.fillWidth: true; text: "GPS: " + appState.gpsStatus + " / " + appState.gpsDetail; color: Theme.text; wrapMode: Text.Wrap }
                Label { text: "冷却: " + uiFeatures.fan.status; color: Theme.text }
                Label { Layout.fillWidth: true; text: "CPU " + uiFeatures.fan.temperature + " / 冷却ファン " + uiFeatures.fan.rpm + " / 出力 " + uiFeatures.fan.duty; color: Theme.text; wrapMode: Text.Wrap }
                Label { text: "常時待機系: 状態通知未接続"; color: Theme.muted }
                Label { text: "録画: " + (Object.keys(appState.cameraStates).length ? (appState.recording ? "録画中" : "待機") : "未接続") + " / " + appState.cameraStorage; color: Theme.text }
                Label { Layout.fillWidth: true; text: "クラウド: " + appState.uploadStatus + " " + appState.uploadPending; color: Theme.text; wrapMode: Text.Wrap }
                Label { text: "タイヤ空気圧: 未対応"; color: Theme.muted }
            }
        }
    }
}
