import QtQuick
import "."

Item {
    id: root
    objectName: "navigationViewport"
    property rect fixedViewport: Qt.rect(0, 0, width, height)

    // OsmAndはSway上の外部ウィンドウとしてこの領域へ配置される。
    // QML側には外部画面と重なる操作部品を置かない。
    Rectangle {
        anchors.fill: parent
        color: "#0b0f11"
    }

    Text {
        anchors.centerIn: parent
        visible: !appState.navigationWindowVisible
        text: appState.screen === "android_apps"
              ? "Androidアプリ画面を起動しています..."
              : (appState.navigationMode === "livi" ? "LIVIを起動しています..." : "OsmAndを起動しています...")
        color: Theme.muted
        font.pixelSize: 18
    }
}
