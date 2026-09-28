import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

RowLayout {
    property string title: ""
    property string subtitle: ""
    property string stateLabel: ""
    property color stateColor: Theme.positive
    Layout.fillWidth: true
    spacing: 12
    Label { text: parent.title; color: Theme.text; font.pixelSize: Theme.headingSize; font.bold: true }
    Label { text: parent.subtitle; color: Theme.muted; font.pixelSize: Theme.bodySize; Layout.leftMargin: 4 }
    Item { Layout.fillWidth: true }
    Rectangle {
        visible: parent.stateLabel.length > 0
        implicitWidth: stateText.implicitWidth + 7 + 7 + 24; height: 30
        radius: Theme.compactRadius; color: "#151b1f"; border.color: "#2c363b"; border.width: 1
        Row { anchors.centerIn: parent; spacing: 7
            Rectangle { width: 7; height: 7; radius: 4; color: parent.parent.parent.stateColor; anchors.verticalCenter: parent.verticalCenter }
            Text { id: stateText; text: parent.parent.parent.stateLabel; color: parent.parent.parent.stateColor; font.pixelSize: 11 }
        }
    }
}
