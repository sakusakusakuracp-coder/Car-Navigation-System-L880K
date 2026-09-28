import QtQuick
import QtQuick.Controls
import ".."

Item {
    id: tile
    property string label: ""
    property string subtitle: ""
    property string iconSource: ""
    signal triggered()
    implicitWidth: 210
    implicitHeight: 132
    readonly property bool compact: height < 125
    readonly property string themedIcon: Theme.dark ? tile.iconSource : tile.iconSource.replace(".svg", "-light.svg")

    Rectangle {
        anchors.fill: parent
        radius: Theme.panelRadius
        color: mouse.containsMouse ? Theme.panelStrong : Theme.panel
        border.color: mouse.containsMouse ? "#4a565c" : "#293238"
        border.width: 1
        Behavior on color { ColorAnimation { duration: 140 } }
    }
    Image { anchors.left: parent.left; anchors.top: parent.top; anchors.margins: tile.compact ? 12 : 20; width: tile.compact ? 24 : 34; height: width; source: tile.themedIcon.indexOf("assets/") === 0 ? "../" + tile.themedIcon : tile.themedIcon; fillMode: Image.PreserveAspectFit }
    Column {
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.margins: tile.compact ? 12 : 20; anchors.leftMargin: tile.compact ? 48 : 20; spacing: 4
        Text { width: parent.width; text: tile.label; color: Theme.text; font.pixelSize: 18; font.bold: true; elide: Text.ElideRight; maximumLineCount: 1 }
        Text { width: parent.width; text: tile.subtitle; color: Theme.muted; font.pixelSize: 12; elide: Text.ElideRight }
    }
    MouseArea { id: mouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: tile.triggered() }
}
