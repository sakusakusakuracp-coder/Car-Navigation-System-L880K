# QML・Python・Qtの役割を説明する挿絵

- 対象画像: [qml_python_qt_roles.png](qml_python_qt_roles.png)
- 作成方法: 内蔵画像生成ツール
- 用途: カーナビUI詳細設計書のQML注釈。役割を説明するイメージであり、実装済み画面や確定した画面配置ではない。

## 生成時の指示

```text
Use case: scientific-educational.
Create one polished Japanese educational illustration for a Markdown car-navigation software design document. A pictorial explainer, not merely boxes with text. Landscape white background, crisp large Japanese sans serif type, generous whitespace. Audience: Japanese beginner who asks whether QML builds the screen layout. Medium: clean technical editorial bitmap illustration with tangible tablet screens and a simple paper layout drawing. No people, mascots, logos, decorative blobs, watermark, or code snippets. Restrained teal, blue, green and charcoal accents.
Title exact: 「カーナビ画面ができるまで」
Top two-thirds: two inputs on the left converge on a central Qt engine then one clearly recognizable car dashboard touchscreen on the right. Upper-left show a drawn wireframe screen as a design sheet, labeled 「QML」 and 「画面の配置を決める」 with three tiny clearly legible region labels 「時計」「表示領域」「操作ボタン」. Lower-left depict a small decision path showing reverse signal, labeled 「Python」 and 「表示する内容を決める」 and short text 「リバースON → 後方カメラ」. Both input arrows feed the center labeled 「Qt」 and 「実際の画面を表示する」. Do NOT show QML -> Python as a serial conversion process: these are two contributions to Qt. Beside the Python-to-Qt arrow use the small label 「PySide6で連携」. The right touchscreen should display a clean rear camera view of an empty parking lot with pavement and boundary lines, no artificial distance guidelines, a status bar clock 12:30, a modest home icon and volume icon on bottom. Label above 「ディスプレイ」 and below 「後方映像を表示」. Avoid tiny text or clutter. Do not imply QML creates or captures the footage; camera footage is already provided by a camera service.
Bottom separated with a thin rule: compact three-step example with arrows and small visual symbols: 「① リバースON」 then 「② Pythonが切替を判断」 then 「③ QMLで決めた配置にQtが表示」.
Small final footer exact: 「役割を説明するイメージです。実際の画面デザインではありません。」
Technical meaning: QML defines layout/appearance; Python selects content/screens in this project's design; Qt renders/handles the UI; PySide6 connects Python with Qt. Make every Japanese label accurate and readable.
```
