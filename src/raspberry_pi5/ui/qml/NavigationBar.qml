import QtQuick
import QtQuick.Layouts
import "."
import "components"

Rectangle {
    height: 78; color: Theme.panel; border.color: "#222b30"; border.width: 1
    RowLayout { anchors.fill: parent; anchors.leftMargin: 24; anchors.rightMargin: 24; spacing: 8
        Repeater {
            model: [ {label: "ホーム", id: "home", icon: "assets/icons/vehicle.svg"}, {label: "ナビ", id: "navigation", icon: "assets/icons/navigation.svg"}, {label: "カメラ", id: "camera", icon: "assets/icons/camera.svg"}, {label: "音楽", id: "audio", icon: "assets/icons/music.svg"}, {label: "車両", id: "vehicle", icon: "assets/icons/vehicle.svg"}, {label: "設定", id: "settings", icon: "assets/icons/vehicle.svg"} ]
            delegate: TeslaButton { text: modelData.label; iconSource: modelData.icon; active: appState.screen === modelData.id; Layout.fillWidth: true; onClicked: screenController.request_screen(modelData.id) }
        }
    }
}
