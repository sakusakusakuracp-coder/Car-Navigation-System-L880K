import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Item {
    Rectangle { anchors.fill: parent; color: Theme.background }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 16
        Label { text: "警告履歴"; color: Theme.text; font.pixelSize: 26; font.bold: true }
        ListView {
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 8; model: uiFeatures.warnings
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                required property var modelData
                width: ListView.view.width; height: Math.max(88, warningRow.implicitHeight + 20); color: Theme.panel
                RowLayout {
                    id: warningRow
                    anchors.fill: parent; anchors.margins: 10
                    ColumnLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: modelData.message; textFormat: Text.PlainText; color: modelData.active ? Theme.warning : Theme.muted; wrapMode: Text.Wrap }
                        Label { Layout.fillWidth: true; text: modelData.time + "  " + (modelData.active ? "発生中" : modelData.previous_run ? "前回の警告・現在は未確認" : "回復 " + modelData.recovered_at) + (modelData.acknowledged ? " / 確認済み" : ""); color: Theme.muted; font.pixelSize: 12; elide: Text.ElideRight }
                    }
                    Button { text: "確認"; enabled: !modelData.acknowledged; onClicked: uiFeatures.acknowledge(modelData.id) }
                }
            }
            Label { anchors.centerIn: parent; visible: parent.count === 0; text: "警告履歴はありません"; color: Theme.muted }
        }
    }
}
