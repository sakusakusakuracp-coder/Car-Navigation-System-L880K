import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Rectangle {
    id: root
    height: 62
    color: Theme.panel
    clip: true
    property string currentTime: Qt.formatTime(new Date(), "hh:mm")
    property string currentDate: Qt.formatDate(new Date(), "yyyy/MM/dd")
    Timer {
        interval: 30000
        running: true
        repeat: true
        onTriggered: {
            currentTime = Qt.formatTime(new Date(), "hh:mm")
            currentDate = Qt.formatDate(new Date(), "yyyy/MM/dd")
        }
    }
    RowLayout {
        visible: !warningBand.visible
        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 8
        Label { text: "L880K NAV"; color: Theme.text; font.pixelSize: 18; font.bold: true }
        Label { visible: root.width >= 1200; text: "車載インフォテインメント"; color: Theme.muted; font.pixelSize: 12; elide: Text.ElideRight; Layout.maximumWidth: 180 }
        Item { Layout.fillWidth: true }
        ColumnLayout {
            Layout.alignment: Qt.AlignVCenter
            spacing: 0
            Label { text: currentTime; color: Theme.text; font.pixelSize: 16; font.bold: true; horizontalAlignment: Text.AlignRight }
            Label { text: currentDate; color: Theme.muted; font.pixelSize: 10; horizontalAlignment: Text.AlignRight }
        }
        StatusChip { label: "サービス"; value: appState.serviceConnectionLabel; indicatorColor: appState.serviceConnected ? Theme.positive : Theme.warning; Layout.preferredWidth: root.width < 1000 ? 100 : 140; Layout.minimumWidth: 90 }
        StatusChip { label: "OBD2"; value: appState.obdConnectionLabel; indicatorColor: appState.obdConnected ? Theme.positive : Theme.warning; Layout.preferredWidth: root.width < 1000 ? 100 : 140; Layout.minimumWidth: 90 }
        StatusChip { label: "GPS"; value: appState.gpsStatus; indicatorColor: appState.gpsCorrectionActive ? Theme.accent : (appState.gpsSignalAvailable ? Theme.positive : Theme.warning); Layout.preferredWidth: root.width < 1000 ? 125 : 150; Layout.minimumWidth: 115 }
        StatusChip { label: "録画"; value: appState.cameraStates && Object.keys(appState.cameraStates).length > 0 ? (appState.recording ? "録画中" : "待機") : "未接続"; indicatorColor: appState.recording ? Theme.red : Theme.muted; Layout.preferredWidth: root.width < 1000 ? 100 : 140; Layout.minimumWidth: 90 }
    }

    // 外部アプリ表示中は、クリック完了後にそのアプリを前面へ戻す。
    // onPressedで戻すと、その後のボタン解放時にUIが再び前面へ出ることがある。
    MouseArea {
        anchors.fill: parent
        onClicked: {
            if (appState.screen === "navigation" || appState.screen === "android_apps")
                screenController.restore_external_app_focus()
            else details.open()
        }
    }
    Popup {
        id: details; x: Math.max(0, root.width - width - 12); y: 62; width: Math.min(root.width - 24, 520); padding: 16
        background: Rectangle { color: Theme.panel; border.color: Theme.muted; radius: 4 }
        contentItem: ColumnLayout {
            Label { Layout.fillWidth: true; text: appState.serviceStatus; wrapMode: Text.Wrap; color: Theme.text }
            Label { Layout.fillWidth: true; text: "OBD2: " + appState.obdStatus; wrapMode: Text.Wrap; color: Theme.text }
            Label { Layout.fillWidth: true; text: "GPS: " + appState.gpsDetail; wrapMode: Text.Wrap; color: Theme.text }
            Label { Layout.fillWidth: true; text: appState.cameraStorage + " / " + appState.uploadStatus; wrapMode: Text.Wrap; color: Theme.text }
            Button { text: "警告履歴"; onClicked: { details.close(); screenController.request_screen("warnings") } }
        }
    }
    WarningOverlay { id: warningBand; anchors.fill: parent }
}
