import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Item {
    id: root
    property string titleText: "ホーム"
    property string detailText: appState.gpsStatus + " / " + appState.obdConnectionLabel

    Image { anchors.fill: parent; source: "assets/home_background.png"; fillMode: Image.PreserveAspectCrop; asynchronous: true; opacity: 0.42 }
    Rectangle { anchors.fill: parent; color: Theme.background; opacity: Theme.dark ? 0.48 : 0.9 }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 16; spacing: 10
        RowLayout {
            Layout.fillWidth: true
            Label { text: titleText; color: Theme.text; font.pixelSize: Theme.headingSize; font.bold: true }
            Item { Layout.fillWidth: true }
            StatusChip { label: "SYSTEM"; value: appState.serviceConnectionLabel; indicatorColor: appState.serviceConnected ? Theme.positive : Theme.warning }
            StatusChip { label: "SPEED"; value: appState.vehicleSpeed; indicatorColor: Theme.accent }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { Layout.fillWidth: true; text: detailText; color: Theme.muted; font.pixelSize: Theme.bodySize; elide: Text.ElideRight }
            Item { Layout.fillWidth: true }
        }
        GridLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; columns: width > 760 ? 4 : 2; columnSpacing: 12; rowSpacing: 12
            IconTile { objectName: "homeOsmandButton"; label: "ナビゲーション"; subtitle: "OsmAnd"; iconSource: "assets/icons/navigation.svg"; Layout.fillWidth: true; Layout.fillHeight: true; onTriggered: screenController.open_osmand() }
            IconTile { label: "カメラ"; subtitle: "周囲と録画"; iconSource: "assets/icons/camera.svg"; Layout.fillWidth: true; Layout.fillHeight: true; onTriggered: screenController.request_screen("camera") }
            IconTile { label: "音楽"; subtitle: "保存済み・公式サービス"; iconSource: "assets/icons/music.svg"; Layout.fillWidth: true; Layout.fillHeight: true; onTriggered: screenController.request_screen("audio") }
            IconTile { label: "車両情報"; subtitle: "K-Line / OBD2"; iconSource: "assets/icons/vehicle.svg"; Layout.fillWidth: true; Layout.fillHeight: true; onTriggered: screenController.request_screen("vehicle") }
        }
        RowLayout {
            Layout.fillWidth: true
            TeslaButton { objectName: "homeLiviButton"; text: "LIVI"; iconSource: "assets/icons/navigation.svg"; onClicked: screenController.open_livi() }
            TeslaButton { objectName: "homeWaydroidButton"; text: "Waydroid"; iconSource: "assets/icons/vehicle.svg"; onClicked: screenController.open_waydroid_home() }
            Item { Layout.fillWidth: true }
            RowLayout {
                spacing: 8
                Image { width: 20; height: 20; source: "assets/icons/recording.svg" }
                Label { text: Object.keys(appState.cameraStates).length ? (appState.recording ? "録画中" : "録画待機") : "録画未接続"; color: appState.recording ? Theme.red : Theme.muted; font.pixelSize: 14 }
            }
        }
    }
}
