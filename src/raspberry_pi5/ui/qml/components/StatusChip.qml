import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Rectangle {
    id: chip
    property string label: ""
    property string value: ""
    property color indicatorColor: Theme.positive
    implicitWidth: 160
    implicitHeight: 42
    radius: Theme.compactRadius
    color: Theme.panelRaised
    border.color: "#2c363b"
    border.width: 1
    clip: true
    RowLayout {
        anchors.fill: parent; anchors.margins: 10; spacing: 8
        Rectangle {
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: 7
            Layout.preferredHeight: 7
            radius: 4
            color: chip.indicatorColor
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: 1
            Text {
                Layout.fillWidth: true
                text: chip.label
                color: Theme.muted
                font.pixelSize: 10
                elide: Text.ElideRight
            }
            Text {
                Layout.fillWidth: true
                text: chip.value
                color: Theme.text
                font.pixelSize: 13
                font.bold: true
                elide: Text.ElideRight
                maximumLineCount: 1
            }
        }
    }
}
