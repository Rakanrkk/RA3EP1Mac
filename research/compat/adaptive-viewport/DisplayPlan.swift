// Wine's opt-in Retina coordinate unit is two raw units per AppKit point.
// Metal drawable pixels use the screen backing scale independently.
import AppKit
import CoreGraphics
import Foundation

struct DisplayPlanFailure: Error, CustomStringConvertible {
    let description: String
    init(_ message: String) { description = message }
}

struct DisplayGeometry {
    let width: Double
    let height: Double
    let left: Double
    let top: Double
    let right: Double
    let bottom: Double
    let backingScale: Double
}

struct SafeDisplayPlan: Equatable {
    let screenWidth: Int
    let screenHeight: Int
    let left: Int
    let top: Int
    let right: Int
    let bottom: Int
    let drawableWidth: Int
    let drawableHeight: Int
    var width: Int { right - left }
    var height: Int { bottom - top }
    var specification: String {
        "\(screenWidth)x\(screenHeight):\(left),\(top),\(right),\(bottom)"
    }
}

func makeSafeDisplayPlan(_ screen: DisplayGeometry) throws -> SafeDisplayPlan {
    let values = [screen.width, screen.height, screen.left, screen.top,
                  screen.right, screen.bottom, screen.backingScale]
    guard values.allSatisfy({ $0.isFinite }), screen.width > 0, screen.height > 0,
          screen.width <= 16384, screen.height <= 16384,
          screen.backingScale > 0, screen.backingScale <= 8,
          [screen.left, screen.top, screen.right, screen.bottom].allSatisfy({ $0 >= 0 }),
          screen.left + screen.right < screen.width,
          screen.top + screen.bottom < screen.height else {
        throw DisplayPlanFailure("显示器尺寸、缩放或安全边缘无效。")
    }
    let rawScale = 2.0
    let fullWidth = Int((screen.width * rawScale).rounded())
    let fullHeight = Int((screen.height * rawScale).rounded())
    guard abs(Double(fullWidth) - screen.width * rawScale) <= 0.01,
          abs(Double(fullHeight) - screen.height * rawScale) <= 0.01 else {
        throw DisplayPlanFailure("显示器尺寸无法表示为Wine整数坐标。")
    }
    let left = Int(ceil(screen.left * rawScale))
    let top = Int(ceil(screen.top * rawScale))
    let right = fullWidth - Int(ceil(screen.right * rawScale))
    let bottom = fullHeight - Int(ceil(screen.bottom * rawScale))
    let width = right - left
    let height = bottom - top
    guard width >= 320, height >= 200 else {
        throw DisplayPlanFailure("显示器安全区域过小。")
    }
    let drawableWidth = Int((Double(width) / rawScale * screen.backingScale).rounded())
    let drawableHeight = Int((Double(height) / rawScale * screen.backingScale).rounded())
    guard drawableWidth >= 1, drawableHeight >= 1,
          drawableWidth <= 32768, drawableHeight <= 32768 else {
        throw DisplayPlanFailure("Metal呈现尺寸超出候选范围。")
    }
    return SafeDisplayPlan(screenWidth: fullWidth, screenHeight: fullHeight,
                           left: left, top: top, right: right, bottom: bottom,
                           drawableWidth: drawableWidth, drawableHeight: drawableHeight)
}

// This snapshot is read-only; primary follows the menu-bar display, not focus.
func primaryDisplayPlanData() throws -> Data {
    let identifier = CGMainDisplayID()
    guard let screen = NSScreen.screens.first(where: {
        ($0.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value == identifier
    }), let mode = CGDisplayCopyDisplayMode(identifier) else {
        throw DisplayPlanFailure("无法读取主显示器。")
    }
    let frame = screen.frame
    let inset = screen.safeAreaInsets
    let rotation = ((Int(CGDisplayRotation(identifier).rounded()) % 360) + 360) % 360
    let modeWidth = (rotation == 90 || rotation == 270) ? mode.height : mode.width
    let modeHeight = (rotation == 90 || rotation == 270) ? mode.width : mode.height
    guard abs(Double(modeWidth) - frame.width) <= 1,
          abs(Double(modeHeight) - frame.height) <= 1 else {
        throw DisplayPlanFailure("主显示模式和屏幕坐标不一致，请在显示配置稳定后重试。")
    }
    let plan = try makeSafeDisplayPlan(DisplayGeometry(width: frame.width, height: frame.height,
        left: inset.left, top: inset.top, right: inset.right, bottom: inset.bottom,
        backingScale: screen.backingScaleFactor))
    let record: [String: Any] = [
        "schema": "ra3-safe-viewport-plan-v1", "read_only": true,
        "timestamp": ISO8601DateFormatter().string(from: Date()),
        "display_id": identifier, "display_vendor": CGDisplayVendorNumber(identifier),
        "display_model": CGDisplayModelNumber(identifier), "display_serial": CGDisplaySerialNumber(identifier),
        "built_in": CGDisplayIsBuiltin(identifier) != 0,
        "screen_width": plan.screenWidth, "screen_height": plan.screenHeight,
        "left": plan.left, "top": plan.top, "right": plan.right, "bottom": plan.bottom,
        "viewport_width": plan.width, "viewport_height": plan.height,
        "drawable_width": plan.drawableWidth, "drawable_height": plan.drawableHeight,
        "raw_scale": 2, "backing_scale": screen.backingScaleFactor,
        "environment": ["RA3_SAFE_VIEWPORT": plan.specification],
        "frame_points": ["x": frame.minX, "y": frame.minY, "width": frame.width, "height": frame.height],
        "safe_area_points": ["left": inset.left, "top": inset.top, "right": inset.right, "bottom": inset.bottom],
        "mode": ["id": mode.ioDisplayModeID, "width": mode.width, "height": mode.height,
                 "pixel_width": mode.pixelWidth, "pixel_height": mode.pixelHeight, "rotation": rotation],
        "limitations": ["Wine raw units use opt-in Retina2; Metal backing pixels are separate.",
                        "Recompute at each launch. Changing display configuration during a game requires restart.",
                        "Recorded CGDisplayMode pixels do not establish physical panel resolution."]
    ]
    return try JSONSerialization.data(withJSONObject: record, options: [.prettyPrinted, .sortedKeys])
}
