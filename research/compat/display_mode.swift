// Save/restore the main desktop display mode around an explicitly launched game.
// The main display (CGMainDisplayID) may be built-in or external; restore only
// ever targets the exact saved display identity and never falls back to another.
import Foundation
import CoreGraphics

struct SavedMode: Codable {
    let displayID: UInt32
    let vendor: UInt32
    let model: UInt32
    let serial: UInt32
    let modeID: Int32
    let width: Int
    let height: Int
    let pixelWidth: Int
    let pixelHeight: Int
    let refreshRate: Double
    let isBuiltin: Bool?
}

enum Failure: Error { case message(String) }

let usage = "Usage: display-mode snapshot|restore FILE.json"

func displays() throws -> [CGDirectDisplayID] {
    var ids = [CGDirectDisplayID](repeating: 0, count: 16)
    var count: UInt32 = 0
    guard CGGetActiveDisplayList(16, &ids, &count) == .success else {
        throw Failure.message("Cannot enumerate active displays")
    }
    guard count > 0 else { throw Failure.message("No active display found") }
    return Array(ids.prefix(Int(count)))
}

func snapshot() throws -> SavedMode {
    let active = try displays()
    let main = CGMainDisplayID()
    guard main != kCGNullDirectDisplay, active.contains(main),
          let mode = CGDisplayCopyDisplayMode(main) else {
        throw Failure.message("No active main display available to snapshot")
    }
    return SavedMode(displayID: main, vendor: CGDisplayVendorNumber(main),
        model: CGDisplayModelNumber(main), serial: CGDisplaySerialNumber(main), modeID: mode.ioDisplayModeID,
        width: mode.width, height: mode.height, pixelWidth: mode.pixelWidth,
        pixelHeight: mode.pixelHeight, refreshRate: mode.refreshRate,
        isBuiltin: CGDisplayIsBuiltin(main) != 0)
}

func restore(_ saved: SavedMode) throws {
    let ids = try displays()
    guard let id = ids.first(where: { $0 == saved.displayID &&
              CGDisplayVendorNumber($0) == saved.vendor && CGDisplayModelNumber($0) == saved.model &&
              CGDisplaySerialNumber($0) == saved.serial }),
          let current = CGDisplayCopyDisplayMode(id) else {
        throw Failure.message("Saved display is no longer active")
    }
    if current.ioDisplayModeID == saved.modeID {
        print("Display mode unchanged; no switch needed")
        return
    }
    let options = [kCGDisplayShowDuplicateLowResolutionModes: true] as CFDictionary
    guard let modes = CGDisplayCopyAllDisplayModes(id, options) as? [CGDisplayMode],
          let target = modes.first(where: {
              $0.ioDisplayModeID == saved.modeID && $0.width == saved.width &&
              $0.height == saved.height && $0.pixelWidth == saved.pixelWidth &&
              $0.pixelHeight == saved.pixelHeight && abs($0.refreshRate - saved.refreshRate) < 0.1
          }) else { throw Failure.message("Saved mode no longer available; refusing to guess") }
    let result = CGDisplaySetDisplayMode(id, target, nil)
    guard result == .success, CGDisplayCopyDisplayMode(id)?.ioDisplayModeID == saved.modeID else {
        throw Failure.message("Display restoration failed: \(result.rawValue)")
    }
    print("Restored saved desktop mode")
}

do {
    let args = Array(CommandLine.arguments.dropFirst())
    if args == ["--help"] || args == ["-h"] {
        print(usage)
        exit(0)
    }
    guard args.count == 2 else { throw Failure.message(usage) }
    let url = URL(fileURLWithPath: args[1])
    switch args[0] {
    case "snapshot":
        let saved = try snapshot()
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(saved).write(to: url, options: .withoutOverwriting)
        let kind = (saved.isBuiltin ?? false) ? "built-in" : "display"
        print("Saved main \(kind) \(saved.width)x\(saved.height), pixels \(saved.pixelWidth)x\(saved.pixelHeight), \(saved.refreshRate)Hz")
    case "restore":
        let data = try Data(contentsOf: url)
        guard data.count <= 8192 else { throw Failure.message("Mode file exceeds size limit") }
        try restore(JSONDecoder().decode(SavedMode.self, from: data))
    default: throw Failure.message("Unknown operation: \(args[0])")
    }
} catch {
    FileHandle.standardError.write(Data("display-mode: \(error)\n".utf8))
    exit(1)
}
