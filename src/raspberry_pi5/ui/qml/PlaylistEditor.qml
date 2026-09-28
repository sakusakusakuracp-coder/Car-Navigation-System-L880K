import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

Dialog {
    id: root
    parent: Overlay.overlay
    width: Math.min(parent.width - 24, 1100)
    height: Math.min(parent.height - 24, 720)
    anchors.centerIn: parent
    title: "プレイリスト"
    modal: true
    standardButtons: Dialog.Close
    palette.window: Theme.background
    palette.windowText: Theme.text
    palette.base: Theme.panel
    palette.text: Theme.text
    palette.placeholderText: Theme.muted
    palette.button: Theme.panelRaised
    palette.buttonText: Theme.text
    palette.highlight: Theme.accent
    palette.highlightedText: Theme.background
    readonly property bool pending: uiFeatures.busy.some(function(name) { return name.indexOf("playlist_") === 0 })
    readonly property bool editable: uiFeatures.operationAllowed && !pending
    readonly property bool selected: !!uiFeatures.playlistDraft.uri
    contentItem: ColumnLayout {
        spacing: 4
        Label { Layout.fillWidth: true; text: uiFeatures.drivingRestriction || uiFeatures.notice; color: Theme.muted; elide: Text.ElideRight }
        RowLayout {
            Layout.fillWidth: true
            TextField { id: newName; Layout.fillWidth: true; placeholderText: "新規プレイリスト名"; maximumLength: 200; enabled: root.editable }
            Button { text: "作成"; enabled: root.editable && newName.text.trim().length > 0; onClicked: uiFeatures.create_playlist(newName.text) }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
            ListView {
                Layout.preferredWidth: Math.min(220, root.width * 0.3); Layout.fillHeight: true
                clip: true; model: uiFeatures.playlists; ScrollBar.vertical: ScrollBar {}
                delegate: ItemDelegate {
                    required property var modelData
                    width: ListView.view.width; height: 48; enabled: !root.pending
                    highlighted: modelData.uri === uiFeatures.playlistDraft.uri
                    background: Rectangle { color: parent.highlighted ? Theme.panelRaised : Theme.background }
                    contentItem: Label { text: modelData.name || "名前なし"; color: Theme.text; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter }
                    onClicked: uiFeatures.read_playlist(modelData.uri)
                }
                Label { anchors.centerIn: parent; visible: parent.count === 0; text: "保存済みリストなし"; color: Theme.muted }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true
                TextField { Layout.fillWidth: true; text: uiFeatures.playlistDraft.name || ""; placeholderText: "リストを選択"; maximumLength: 200; enabled: root.editable && root.selected; onTextEdited: uiFeatures.rename_playlist(text) }
                ListView {
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    model: uiFeatures.playlistDraft.tracks || []; ScrollBar.vertical: ScrollBar {}
                    delegate: RowLayout {
                        required property var modelData
                        required property int index
                        width: ListView.view.width; height: 44; spacing: 2
                        Label { Layout.fillWidth: true; text: (index + 1) + ". " + (modelData.name || modelData.uri); color: Theme.text; elide: Text.ElideRight }
                        Button { text: "↑"; Layout.preferredWidth: 40; enabled: root.editable && index > 0; ToolTip.visible: hovered; ToolTip.text: "前へ移動"; onClicked: uiFeatures.move_playlist_track(index, index - 1) }
                        Button { text: "↓"; Layout.preferredWidth: 40; enabled: root.editable && index < uiFeatures.playlistDraft.tracks.length - 1; ToolTip.visible: hovered; ToolTip.text: "後ろへ移動"; onClicked: uiFeatures.move_playlist_track(index, index + 1) }
                        Button { text: "×"; Layout.preferredWidth: 40; enabled: root.editable; ToolTip.visible: hovered; ToolTip.text: "リストから除外"; onClicked: uiFeatures.move_playlist_track(index, -1) }
                    }
                }
                Flow {
                    Layout.fillWidth: true; spacing: 4
                    Button { text: "キューを追加"; enabled: root.editable && root.selected; onClicked: uiFeatures.append_playlist_queue() }
                    Button { text: "キューへ"; enabled: !root.pending && root.selected && uiFeatures.busy.indexOf("audio_command") < 0; ToolTip.visible: hovered; ToolTip.text: "保存済みリストを再生キューへ追加"; onClicked: uiFeatures.music("add_playlist", uiFeatures.playlistDraft.uri) }
                    Button { text: "保存"; enabled: root.editable && root.selected; onClicked: uiFeatures.save_playlist() }
                    Button { text: "再取得"; enabled: !root.pending && root.selected; onClicked: uiFeatures.read_playlist(uiFeatures.playlistDraft.uri) }
                    Button { text: "削除"; enabled: root.editable && root.selected; onClicked: deleteDialog.open() }
                }
            }
        }
    }
    Dialog {
        id: deleteDialog; anchors.centerIn: parent; modal: true; title: "選択したプレイリストを削除しますか？"
        standardButtons: Dialog.Ok | Dialog.Cancel
        onAccepted: uiFeatures.delete_playlist()
    }
}
