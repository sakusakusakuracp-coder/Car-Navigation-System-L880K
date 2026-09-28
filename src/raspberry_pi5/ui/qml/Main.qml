import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "."

ApplicationWindow {
    visible: true
    // 外部Waylandウィンドウを重ねる試験時は、Swayが表示順を管理できる通常ウィンドウにする。
    visibility: embeddedWindow ? Window.Windowed : Window.FullScreen
    width: 1280
    height: 720
    title: "L880K Car Navigation"
    color: Theme.background
    palette.window: Theme.background
    palette.windowText: Theme.text
    palette.base: Theme.panel
    palette.alternateBase: Theme.panelRaised
    palette.text: Theme.text
    palette.placeholderText: Theme.muted
    palette.button: Theme.panelRaised
    palette.buttonText: Theme.text
    palette.highlight: Theme.accent
    palette.highlightedText: Theme.background
    readonly property bool featuresAvailable: typeof uiFeatures !== "undefined"
    Binding { target: Theme; property: "dark"; value: featuresAvailable ? uiFeatures.settings.night_mode : true }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        StatusBar { Layout.fillWidth: true }

        Loader {
            id: screenLoader
            Layout.fillWidth: true
            Layout.fillHeight: true
            opacity: 1
            scale: 1
            transformOrigin: Item.Center
            onSourceComponentChanged: {
                opacity = 0
                scale = 0.985
                pageEnter.restart()
            }
            onLoaded: {
                opacity = 0
                scale = 0.985
                pageEnter.restart()
            }
            SequentialAnimation {
                id: pageEnter
                ParallelAnimation {
                    NumberAnimation { target: screenLoader; property: "opacity"; from: 0; to: 1; duration: featuresAvailable && !uiFeatures.settings.animations ? 0 : 180; easing.type: Easing.OutCubic }
                    NumberAnimation { target: screenLoader; property: "scale"; from: 0.985; to: 1; duration: featuresAvailable && !uiFeatures.settings.animations ? 0 : 220; easing.type: Easing.OutCubic }
                }
            }
            sourceComponent: {
                switch (appState.screen) {
                case "navigation": return navigationView
                case "android_apps": return navigationView
                case "camera": return cameraView
                case "rear_camera": return cameraView
                case "audio": return audioView
                case "vehicle": return vehicleView
                case "settings": return settingsView
                case "recordings": return recordingsView
                case "warnings": return warningsView
                default: return homeView
                }
            }
        }

        NavigationBar { Layout.fillWidth: true }
    }

    Component {
        id: homeView
        HomeScreen { }
    }
    Component {
        id: navigationView
        NavigationScreen { }
    }
    Component {
        id: cameraView
        CameraScreen { }
    }
    Component {
        id: audioView
        MusicScreen { }
    }
    Component {
        id: vehicleView
        VehicleScreen { }
    }
    Component {
        id: settingsView
        SettingsScreen { }
    }
    Component { id: recordingsView; RecordingsScreen {} }
    Component { id: warningsView; WarningsScreen {} }

}
