import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Item {
    id: warningOverlay
    property var banner: typeof uiFeatures !== "undefined" ? uiFeatures.banner : ({})
    readonly property string message: banner.message || ""
    readonly property int lineBreak: message.indexOf("\n")
    visible: !!banner.message && appState.screen !== "warnings"
    Rectangle {
        anchors.fill: parent; anchors.margins: 4
        color: Theme.panel; border.color: banner.severity === "critical" ? Theme.red : Theme.warning; radius: 4
        RowLayout {
            anchors.fill: parent; anchors.margins: 6
            ColumnLayout {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                spacing: 2
                Label { objectName: "warningTitle"; Layout.fillWidth: true; text: warningOverlay.lineBreak < 0 ? warningOverlay.message : warningOverlay.message.substring(0, warningOverlay.lineBreak); textFormat: Text.PlainText; color: Theme.text; font.bold: true; font.pixelSize: 14; elide: Text.ElideRight }
                Label { objectName: "warningReason"; Layout.fillWidth: true; visible: warningOverlay.lineBreak >= 0; text: warningOverlay.message.substring(warningOverlay.lineBreak + 1).replace(/\n/g, " "); textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 12; elide: Text.ElideRight }
            }
            Button { text: "確認"; onClicked: uiFeatures.acknowledge(banner.id) }
            Button { text: "詳細"; onClicked: screenController.request_screen("warnings") }
        }
    }
}
