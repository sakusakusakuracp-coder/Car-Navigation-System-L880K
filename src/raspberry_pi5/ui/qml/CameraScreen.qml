import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Item {
    id: root
    property string selectedCamera: "front"
    readonly property var labels: ({front: "前方", rear: "後方", left: "左側", right: "右側"})
    readonly property var states: appState.cameraStates
    readonly property var selectedState: states[selectedCamera] || ({})
    readonly property bool rearOnly: appState.screen === "rear_camera"

    function updateSelection() {
        if (rearOnly) selectedCamera = "rear"
        cameraPresenter.select_camera(visible ? selectedCamera : "")
    }
    function inputLabel(state) {
        return ({STREAMING: "受信中", STARTING: "準備中", STALE: "映像途絶", FAULT: "取得異常", DISCONNECTED: "未接続"})[state] || "未接続"
    }
    onSelectedCameraChanged: updateSelection()
    onVisibleChanged: updateSelection()
    onRearOnlyChanged: updateSelection()
    Component.onCompleted: updateSelection()
    Component.onDestruction: cameraPresenter.select_camera("")

    Rectangle { anchors.fill: parent; color: Theme.background }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Label { text: "カメラ"; color: Theme.text; font.pixelSize: Theme.headingSize; font.bold: true }
            Item { Layout.fillWidth: true }
            Label { Layout.maximumWidth: 240; text: appState.cameraStorage; color: Theme.muted; elide: Text.ElideRight }
            Button { text: "録画一覧"; visible: !root.rearOnly; onClicked: screenController.request_screen("recordings") }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: root.labels[root.selectedCamera] + "カメラ"; color: Theme.text; font.pixelSize: 18 }
                    Item { Layout.fillWidth: true }
                    Label {
                        text: root.selectedState.record_state === "RECORDING" ? "録画中" : (root.selectedState.record_state === "FAULT" ? "録画異常" : "録画待機")
                        color: root.selectedState.record_state === "RECORDING" ? Theme.red : Theme.muted
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumWidth: 160
                    color: "#080a0b"
                    clip: true
                    Image {
                        anchors.fill: parent
                        source: cameraPresenter.source
                        cache: false
                        fillMode: Image.PreserveAspectFit
                    }
                    Label {
                        anchors.centerIn: parent
                        width: parent.width - 24
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        visible: cameraPresenter.source === ""
                        text: root.inputLabel(root.selectedState.input_state)
                        color: Theme.muted
                        font.pixelSize: 20
                    }
                }
                Label {
                    Layout.fillWidth: true
                    text: root.selectedState.reason || ""
                    color: Theme.muted
                    elide: Text.ElideRight
                }
            }
            ColumnLayout {
                Layout.preferredWidth: 150
                Layout.maximumWidth: 150
                Layout.fillHeight: true
                spacing: 8
                Repeater {
                    model: ["front", "rear", "left", "right"]
                    delegate: Button {
                        required property string modelData
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        padding: 4
                        enabled: !root.rearOnly || modelData === "rear"
                        onClicked: root.selectedCamera = modelData
                        background: Rectangle {
                            radius: 4
                            color: root.selectedCamera === modelData ? Theme.panelStrong : Theme.panel
                            border.color: root.selectedCamera === modelData ? Theme.accent : "#303a3f"
                        }
                        contentItem: ColumnLayout {
                            spacing: 0
                            Label { text: root.labels[modelData]; color: Theme.text; font.bold: true; font.pixelSize: 14 }
                            Label {
                                Layout.fillWidth: true
                                text: root.inputLabel((root.states[modelData] || ({})).input_state)
                                color: Theme.muted
                                font.pixelSize: 11
                                elide: Text.ElideRight
                            }
                        }
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { text: appState.recording ? "ドライブレコーダー録画中" : "ドライブレコーダー待機"; color: Theme.text; font.pixelSize: 12 }
            Item { Layout.fillWidth: true }
            Label { Layout.fillWidth: true; horizontalAlignment: Text.AlignRight; text: appState.uploadStatus; color: Theme.muted; font.pixelSize: 12; elide: Text.ElideRight }
        }
    }
}
