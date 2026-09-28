import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Rectangle {
    property string message: ""
    visible: message.length > 0
    implicitHeight: 42
    radius: Theme.compactRadius
    color: "#2d2415"
    border.color: "#755522"
    border.width: 1
    clip: true
    RowLayout {
        anchors.fill: parent; anchors.margins: 10; spacing: 8
        Image { Layout.preferredWidth: 20; Layout.preferredHeight: 20; source: "../assets/icons/warning.svg"; Layout.alignment: Qt.AlignVCenter }
        Text { Layout.fillWidth: true; Layout.minimumWidth: 0; text: message; color: Theme.warning; font.pixelSize: 13; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; maximumLineCount: 1 }
    }
}
