pragma Singleton
import QtQuick

QtObject {
    property bool dark: true
    readonly property color background: dark ? "#0b0d0f" : "#f1f3f4"
    readonly property color panel: dark ? "#121619" : "#ffffff"
    readonly property color panelRaised: dark ? "#1c2226" : "#e2e6e8"
    readonly property color panelStrong: dark ? "#252d32" : "#d1dadd"
    readonly property color text: dark ? "#f4f6f7" : "#15191b"
    readonly property color muted: dark ? "#a6afb5" : "#505c62"
    readonly property color accent: dark ? "#e8eef1" : "#263b45"
    readonly property color red: "#e34242"
    readonly property color warning: "#f2b84b"
    readonly property color positive: "#68d391"
    readonly property int compactRadius: 6
    readonly property int panelRadius: 8
    readonly property int headingSize: 30
    readonly property int bodySize: 15
}
