import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Item {
    id: root
    objectName: "musicScreen"
    readonly property var audio: uiFeatures.audio
    readonly property var official: uiFeatures.webMusic
    readonly property bool localMusic: official.source === "local"
    readonly property bool ready: audio.connected && !official.closing && uiFeatures.busy.indexOf("audio_command") < 0
    property bool libraryMode: false
    function timeText(ms) { var seconds = Math.floor((ms || 0) / 1000); return Math.floor(seconds / 60) + ":" + (seconds % 60).toString().padStart(2, "0") }
    Rectangle { anchors.fill: parent; color: Theme.background }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 12; spacing: 4
        RowLayout {
            TabBar {
                Layout.fillWidth: true; Layout.maximumWidth: 480
                currentIndex: root.localMusic ? 0 : root.official.source === "youtube_music" ? 1 : 2
                TabButton { text: "保存済み音楽"; onClicked: root.official.select("local") }
                TabButton { text: "YouTube Music"; onClicked: root.official.select("youtube_music") }
                TabButton { text: "Spotify"; onClicked: root.official.select("spotify") }
            }
            Item { Layout.fillWidth: true }
            Button { visible: root.localMusic; text: "プレイリスト"; enabled: audio.connected; onClicked: { uiFeatures.read_playlists(); playlistEditor.open() } }
            Button { visible: !root.localMusic; text: "終了"; enabled: root.official.running; onClicked: root.official.stop() }
        }
        Label { Layout.fillWidth: true; visible: text.length > 0; text: uiFeatures.notice; color: Theme.muted; elide: Text.ElideRight }
        Label { Layout.fillWidth: true; text: uiFeatures.audioOutput.label; font.pixelSize: 12; color: uiFeatures.audioOutput.status === "connected" ? Theme.muted : Theme.warning; elide: Text.ElideRight }
        RowLayout {
            visible: root.localMusic
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 16
            ColumnLayout {
                Layout.fillWidth: true; Layout.preferredWidth: 360; Layout.minimumWidth: 200; spacing: 2
                Label { Layout.fillWidth: true; text: audio.title || "未選曲"; font.pixelSize: root.height < 440 ? 20 : 24; font.bold: true; color: Theme.text; wrapMode: Text.Wrap; maximumLineCount: root.height < 440 ? 1 : 2; elide: Text.ElideRight }
                Label { Layout.fillWidth: true; text: audio.artist || ""; color: Theme.muted; elide: Text.ElideRight }
                Slider { Layout.fillWidth: true; Layout.preferredHeight: 28; from: 0; to: Math.max(1, audio.length || 0); value: audio.position || 0; enabled: root.ready && audio.length > 0; onPressedChanged: if (!pressed && enabled) uiFeatures.music("seek", value) }
                RowLayout {
                    Label { text: root.timeText(audio.position); color: Theme.muted }
                    Item { Layout.fillWidth: true }
                    Label { text: root.timeText(audio.length); color: Theme.muted }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Button { text: "|◀"; Layout.fillWidth: true; enabled: root.ready; ToolTip.visible: hovered; ToolTip.text: "前の曲"; onClicked: uiFeatures.music("previous", null) }
                    Button { text: audio.state === "playing" ? "Ⅱ" : "▶"; Layout.fillWidth: true; enabled: root.ready; ToolTip.visible: hovered; ToolTip.text: audio.state === "playing" ? "一時停止" : "再生"; onClicked: uiFeatures.music(audio.state === "playing" ? "pause" : "play", null) }
                    Button { text: "▶|"; Layout.fillWidth: true; enabled: root.ready; ToolTip.visible: hovered; ToolTip.text: "次の曲"; onClicked: uiFeatures.music("next", null) }
                }
                RowLayout {
                    Label { text: "音量"; color: Theme.text }
                    Slider { Layout.fillWidth: true; from: 0; to: 100; value: audio.volume || 0; enabled: root.ready && audio.volume !== null; onPressedChanged: if (!pressed && enabled) uiFeatures.music("volume", value) }
                    Label { text: audio.connected && audio.volume !== null ? audio.volume + "%" : "—"; color: Theme.text }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.preferredWidth: 350; Layout.fillHeight: true
                RowLayout {
                    Button { text: "再生キュー"; checked: !root.libraryMode; onClicked: root.libraryMode = false }
                    Button { text: "ライブラリ"; checked: root.libraryMode; enabled: audio.connected; onClicked: { root.libraryMode = true; uiFeatures.browse("") } }
                }
                RowLayout {
                    visible: root.libraryMode; Layout.fillWidth: true
                    TextField { id: search; Layout.fillWidth: true; Layout.minimumWidth: 60; placeholderText: "曲名・アーティスト"; maximumLength: 200; onAccepted: uiFeatures.search_music(text) }
                    Button { text: "検索"; enabled: audio.connected && search.text.trim().length > 0 && uiFeatures.busy.indexOf("browse") < 0; onClicked: uiFeatures.search_music(search.text) }
                    Button { text: "戻る"; enabled: uiFeatures.musicPage.back && uiFeatures.busy.indexOf("browse") < 0; onClicked: uiFeatures.music_back() }
                }
                ListView {
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 4
                    model: root.libraryMode ? uiFeatures.musicLibrary : (audio.tracks || [])
                    ScrollBar.vertical: ScrollBar {}
                    delegate: ItemDelegate {
                        required property var modelData
                        width: ListView.view.width; height: 52; enabled: root.ready && uiFeatures.busy.indexOf("browse") < 0
                        text: root.libraryMode ? modelData.name : (modelData.track.name || "曲名なし")
                        contentItem: Label { text: parent.text; color: Theme.text; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter }
                        onClicked: {
                            if (!root.libraryMode) uiFeatures.music("select", modelData.tlid)
                            else if (modelData.type === "directory") uiFeatures.browse(modelData.uri)
                            else uiFeatures.music("add", modelData.uri)
                        }
                    }
                    Label { anchors.centerIn: parent; visible: parent.count === 0; text: audio.connected ? "曲がありません" : "サービス未接続"; color: Theme.muted }
                }
                RowLayout {
                    visible: root.libraryMode; Layout.fillWidth: true
                    Button { text: "◀"; enabled: uiFeatures.musicPage.page > 0; ToolTip.visible: hovered; ToolTip.text: "前のページ"; onClicked: uiFeatures.music_page(uiFeatures.musicPage.page - 1) }
                    Label { Layout.fillWidth: true; text: (uiFeatures.musicPage.page + 1) + " / " + Math.max(1, Math.ceil(uiFeatures.musicPage.total / 50)) + "  (" + uiFeatures.musicPage.total + "曲・フォルダ)"; color: Theme.muted; elide: Text.ElideRight }
                    Button { text: "▶"; enabled: uiFeatures.musicPage.more; ToolTip.visible: hovered; ToolTip.text: "次のページ"; onClicked: uiFeatures.music_page(uiFeatures.musicPage.page + 1) }
                }
            }
        }
        Label {
            visible: !root.localMusic; Layout.fillWidth: true; elide: Text.ElideRight
            text: uiFeatures.drivingRestriction || root.official.status
            color: Theme.muted
        }
        Rectangle {
            id: webViewport
            objectName: "officialMusicViewport"
            visible: !root.localMusic
            Layout.fillWidth: true; Layout.fillHeight: true
            color: Theme.background
            function reportViewport() {
                var point = mapToItem(null, 0, 0)
                root.official.set_viewport(Math.round(point.x), Math.round(point.y), Math.round(width), Math.round(height))
            }
            Timer { interval: 500; running: webViewport.visible; repeat: true; onTriggered: webViewport.reportViewport() }
            Image { anchors.centerIn: parent; width: 64; height: 64; opacity: 0.3; source: Theme.dark ? "assets/icons/music.svg" : "assets/icons/music-light.svg" }
        }
    }
    PlaylistEditor { id: playlistEditor }
}
