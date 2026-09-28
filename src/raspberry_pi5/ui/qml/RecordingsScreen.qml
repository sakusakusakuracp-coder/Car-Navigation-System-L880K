import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia
import "."

Item {
    id: root
    property var directions: ["", "front", "rear", "left", "right"]
    function refresh(page) { uiFeatures.list_recordings(directions[direction.currentIndex], date.text, page) }
    Component.onCompleted: refresh(0)
    Component.onDestruction: { player.stop(); uiFeatures.stop_playback() }
    Rectangle { anchors.fill: parent; color: Theme.background }
    MediaPlayer {
        id: player; source: uiFeatures.playbackSource; videoOutput: video
        onSourceChanged: { if (source.toString().length > 0 && uiFeatures.operationAllowed) play(); else stop() }
        onErrorOccurred: uiFeatures.playback_error(errorString)
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 12; spacing: 6
        RowLayout {
            Label { text: "録画一覧"; font.pixelSize: 24; font.bold: true; color: Theme.text }
            Item { Layout.fillWidth: true }
            ComboBox { id: direction; model: ["全方向", "前方", "後方", "左側", "右側"]; enabled: uiFeatures.busy.indexOf("recordings") < 0; onActivated: root.refresh(0) }
            TextField { id: date; placeholderText: "YYYY-MM-DD"; Layout.preferredWidth: 140; onAccepted: root.refresh(0) }
            Button { text: "検索"; enabled: uiFeatures.busy.indexOf("recordings") < 0; onClicked: root.refresh(0) }
        }
        Label { Layout.fillWidth: true; text: uiFeatures.notice; color: Theme.muted; elide: Text.ElideRight }
        Label { Layout.fillWidth: true; visible: !uiFeatures.parked; text: uiFeatures.drivingRestriction; color: Theme.warning; wrapMode: Text.Wrap }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 10
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 420
                Rectangle {
                    Layout.fillWidth: true; Layout.fillHeight: true; color: "#080a0b"
                    VideoOutput { id: video; anchors.fill: parent }
                    Label { anchors.centerIn: parent; visible: !uiFeatures.playbackSource; text: "動画を選択"; color: "#a6afb5" }
                }
                RowLayout {
                    Button { text: player.playbackState === MediaPlayer.PlayingState ? "一時停止" : "再生"; enabled: !!uiFeatures.playbackSource; onClicked: player.playbackState === MediaPlayer.PlayingState ? player.pause() : player.play() }
                    Slider { Layout.fillWidth: true; from: 0; to: Math.max(1, player.duration); value: player.position; enabled: player.seekable; onPressedChanged: if (!pressed && enabled) player.setPosition(value) }
                    Button { text: "停止"; onClicked: { player.stop(); uiFeatures.stop_playback() } }
                }
            }
            ListView {
                Layout.preferredWidth: root.width < 1000 ? 300 : 400; Layout.fillHeight: true; clip: true; spacing: 4
                model: uiFeatures.recordingsPage.items; ScrollBar.vertical: ScrollBar {}
                delegate: ItemDelegate {
                    required property var modelData
                    width: ListView.view.width; height: 62; enabled: uiFeatures.operationAllowed && modelData.playable && uiFeatures.busy.indexOf("playback") < 0
                    contentItem: Column {
                        Label { width: parent.width; text: modelData.date + " " + ({front: "前方", rear: "後方", left: "左側", right: "右側"})[modelData.camera_id]; color: Theme.text; elide: Text.ElideRight }
                        Label { width: parent.width; text: ({READY: "未送信", UPLOADING: "送信中", VERIFIED: "検証済み", DELETED: "ローカル削除済み", BLOCKED: "要確認"})[modelData.state] || modelData.state; color: Theme.muted; elide: Text.ElideRight }
                    }
                    onClicked: { player.stop(); uiFeatures.play_recording(modelData.file_id) }
                }
                Label { anchors.centerIn: parent; visible: parent.count === 0; text: "該当する録画はありません"; color: Theme.muted }
            }
        }
        RowLayout {
            Item { Layout.fillWidth: true }
            Button { text: "前のページ"; enabled: uiFeatures.recordingsPage.page > 0 && uiFeatures.busy.indexOf("recordings") < 0; onClicked: root.refresh(uiFeatures.recordingsPage.page - 1) }
            Label { text: (uiFeatures.recordingsPage.page + 1) + " ページ"; color: Theme.text }
            Button { text: "次のページ"; enabled: uiFeatures.recordingsPage.more && uiFeatures.busy.indexOf("recordings") < 0; onClicked: root.refresh(uiFeatures.recordingsPage.page + 1) }
        }
    }
}
