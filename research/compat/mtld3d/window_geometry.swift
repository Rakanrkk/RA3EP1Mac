// Read-only screen and Wine window bounds; no screenshots or window changes.
import AppKit
import CoreGraphics
import Foundation

let screens: [[String: Any]] = NSScreen.screens.map { screen in
    ["frame": ["x": screen.frame.origin.x, "y": screen.frame.origin.y,
               "width": screen.frame.width, "height": screen.frame.height],
     "visibleFrame": ["x": screen.visibleFrame.origin.x, "y": screen.visibleFrame.origin.y,
                      "width": screen.visibleFrame.width, "height": screen.visibleFrame.height],
     "safeAreaInsets": ["top": screen.safeAreaInsets.top,
                        "left": screen.safeAreaInsets.left,
                        "bottom": screen.safeAreaInsets.bottom,
                        "right": screen.safeAreaInsets.right],
     "notchSafeFrame": ["x": screen.frame.minX + screen.safeAreaInsets.left,
                        "y": screen.frame.minY + screen.safeAreaInsets.bottom,
                        "width": screen.frame.width - screen.safeAreaInsets.left - screen.safeAreaInsets.right,
                        "height": screen.frame.height - screen.safeAreaInsets.top - screen.safeAreaInsets.bottom],
     "backingScale": screen.backingScaleFactor]
}
let rows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements],
                                    kCGNullWindowID) as? [[String: Any]] ?? []
let windows = rows.filter { row in
    let owner = (row[kCGWindowOwnerName as String] as? String ?? "").lowercased()
    return owner.contains("wine") || owner.contains("ra3") || owner.contains("uprising")
}.map { row -> [String: Any] in
    var result: [String: Any] = [:]
    for key in [kCGWindowOwnerName, kCGWindowOwnerPID, kCGWindowNumber,
                kCGWindowBounds, kCGWindowLayer, kCGWindowIsOnscreen] {
        if let value = row[key as String] { result[key as String] = value }
    }
    return result
}
let pointer = NSEvent.mouseLocation
let result: [String: Any] = ["timestamp": ISO8601DateFormatter().string(from: Date()),
                            "screens": screens, "wineWindows": windows,
                            "pointerAppKitPoints": ["x": pointer.x, "y": pointer.y],
                            "readOnly": true]
let data = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data("\n".utf8))
