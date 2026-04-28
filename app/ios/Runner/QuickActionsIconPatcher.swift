import UIKit

/// Patches UIApplication.shortcutItems after the quick_actions Flutter plugin sets them.
/// The plugin only supports templateImageName; this replaces icons with SF Symbols
/// via UIApplicationShortcutIcon(systemImageName:) which looks native and high-quality.
final class QuickActionsIconPatcher: NSObject {

    static let shared = QuickActionsIconPatcher()

    private var isObserving = false

    // Maps the shortcut type string (set from Dart) to an SF Symbol name.
    private let symbolMap: [String: String] = [
        "add_task":        "checkmark.circle.fill",
        "ask_omi":         "message.fill",
        "voice_mode":      "waveform",
        "mute":            "mic.slash.fill",
        "unmute":          "mic.fill",
        "connect_device":  "cable.connector.horizontal",
        "device_settings": "slider.horizontal.3",
    ]

    func startObserving() {
        guard !isObserving else { return }
        UIApplication.shared.addObserver(
            self,
            forKeyPath: #keyPath(UIApplication.shortcutItems),
            options: [.new],
            context: nil
        )
        isObserving = true
    }

    func stopObserving() {
        guard isObserving else { return }
        UIApplication.shared.removeObserver(self, forKeyPath: #keyPath(UIApplication.shortcutItems))
        isObserving = false
    }

    override func observeValue(
        forKeyPath keyPath: String?,
        of object: Any?,
        change: [NSKeyValueChangeKey: Any]?,
        context: UnsafeMutableRawPointer?
    ) {
        guard keyPath == #keyPath(UIApplication.shortcutItems) else { return }
        DispatchQueue.main.async { self.patchIcons() }
    }

    private func patchIcons() {
        guard let items = UIApplication.shared.shortcutItems, !items.isEmpty else { return }

        let patched = items.map { item -> UIApplicationShortcutItem in
            guard let symbol = symbolMap[item.type],
                  let icon = UIApplicationShortcutIcon(systemImageName: symbol)
            else { return item }

            return UIApplicationShortcutItem(
                type: item.type,
                localizedTitle: item.localizedTitle,
                localizedSubtitle: item.localizedSubtitle,
                icon: icon,
                userInfo: item.userInfo
            )
        }

        // Temporarily stop observing so setting shortcutItems doesn't recurse.
        stopObserving()
        UIApplication.shared.shortcutItems = patched
        startObserving()
    }

    deinit { stopObserving() }
}
