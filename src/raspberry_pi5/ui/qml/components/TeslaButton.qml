import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Button {
    id: control
    property string iconSource: ""
    property bool active: false
    property color accentColor: Theme.accent
    implicitHeight: 48
    implicitWidth: 150
    leftPadding: 16
    rightPadding: 16
    icon.source: iconSource ? Qt.resolvedUrl(iconSource.indexOf("assets/") === 0 ? "../" + iconSource : iconSource) : ""
    icon.width: 22
    icon.height: 22
    icon.color: active ? Theme.background : Theme.text
    palette.buttonText: active ? Theme.background : Theme.text
    font.pixelSize: Theme.bodySize
    display: AbstractButton.TextBesideIcon
    background: Rectangle {
        radius: Theme.compactRadius
        color: control.active ? control.accentColor : (control.pressed ? Theme.panelStrong : Theme.panel)
        border.color: control.active ? control.accentColor : "#313b40"
        border.width: 1
        opacity: control.enabled ? 1 : .45
    }
    ToolTip.visible: control.hovered && control.text.length > 0
    ToolTip.text: control.text
    ToolTip.delay: 600
}
