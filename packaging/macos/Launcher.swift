import AppKit
import CryptoKit
import Darwin
import Foundation

struct LaunchError: Error, CustomStringConvertible {
    let description: String
    init(_ message: String) { description = message }
}

struct Manifest: Decodable {
    let schema: String
    let version: String
    let runtime_pins: [String: String]
    let game_pins: [String: String]
    let payload_pins: [String: String]
    let presentation: String?
    let display_plan: DisplayPlanComponent?
}

struct DisplayPlanComponent: Decodable {
    let sha256: String
}

struct AdaptiveDisplayPlan: Decodable, Equatable {
    let environment: [String: String]
    let screen_width: Int
    let screen_height: Int
    let left: Int
    let top: Int
    let right: Int
    let bottom: Int
    let viewport_width: Int
    let viewport_height: Int
    let drawable_width: Int
    let drawable_height: Int
    let display_id: UInt32

    func viewportEnvironment() throws -> [String: String] {
        guard screen_width > 0, screen_height > 0, screen_width <= 32768, screen_height <= 32768,
              left >= 0, top >= 0, right > left, bottom > top,
              right <= screen_width, bottom <= screen_height,
              right - left >= 320, bottom - top >= 200,
              viewport_width == right - left, viewport_height == bottom - top,
              drawable_width > 0, drawable_height > 0,
              drawable_width <= 32768, drawable_height <= 32768 else {
            throw LaunchError("显示器安全区域无效，请重新启动应用。")
        }
        let spec = "\(screen_width)x\(screen_height):\(left),\(top),\(right),\(bottom)"
        guard environment == ["RA3_SAFE_VIEWPORT": spec] else {
            throw LaunchError("显示适配参数不完整。")
        }
        return environment
    }
}

let fm = FileManager.default

func digest(_ file: URL) throws -> String {
    let stream = try FileHandle(forReadingFrom: file)
    defer { try? stream.close() }
    var hash = SHA256()
    while let bytes = try stream.read(upToCount: 1024 * 1024), !bytes.isEmpty { hash.update(data: bytes) }
    return hash.finalize().map { String(format: "%02x", $0) }.joined()
}

func verify(_ pins: [String: String], under root: URL) throws {
    for (relative, expected) in pins.sorted(by: { $0.key < $1.key }) {
        guard !relative.hasPrefix("/"), !relative.split(separator: "/").contains("..") else {
            throw LaunchError("组件路径无效：\(relative)")
        }
        let file = root.appendingPathComponent(relative)
        guard try digest(file) == expected else { throw LaunchError("组件校验失败：\(relative)") }
    }
}

func writeJSON(_ object: [String: Any], to file: URL) throws {
    let bytes = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
    try bytes.write(to: file, options: .withoutOverwriting)
}

func capture(_ executable: URL, _ arguments: [String], environment: [String: String]? = nil,
             directory: URL? = nil) throws -> (Int32, String) {
    let process = Process()
    process.executableURL = executable
    process.arguments = arguments
    process.environment = environment
    process.currentDirectoryURL = directory
    let pipe = Pipe()
    process.standardOutput = pipe
    process.standardError = pipe
    try process.run()
    let output = pipe.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    return (process.terminationStatus, String(data: output, encoding: .utf8) ?? "")
}

func gameRunning() throws -> Bool {
    let (status, output) = try capture(URL(fileURLWithPath: "/bin/ps"), ["-axww", "-o", "comm="])
    guard status == 0 else { throw LaunchError("无法确认游戏进程状态。") }
    return output.lowercased().contains("ra3ep1_1.0.game")
}

final class GameLauncher {
    let resources: URL
    let manifest: Manifest
    let support: URL
    let prefix: URL
    var lockFD: Int32 = -1
    var newlyPreparedPlan: AdaptiveDisplayPlan?

    init() throws {
        guard let resourceURL = Bundle.main.resourceURL else { throw LaunchError("应用资源目录缺失。") }
        resources = resourceURL
        let data = try Data(contentsOf: resourceURL.appendingPathComponent("manifest.json"))
        manifest = try JSONDecoder().decode(Manifest.self, from: data)
        guard manifest.schema == "ra3mac-app-v1" else { throw LaunchError("应用版本清单无效。") }
        support = try fm.url(for: .applicationSupportDirectory, in: .userDomainMask,
                             appropriateFor: nil, create: true)
            .appendingPathComponent(Bundle.main.bundleIdentifier ?? "org.ra3mac.UprisingMetal", isDirectory: true)
        prefix = support.appendingPathComponent("prefix", isDirectory: true)
    }

    deinit { if lockFD >= 0 { flock(lockFD, LOCK_UN); close(lockFD) } }

    func verifyRuntime() throws {
        try verify(manifest.runtime_pins, under: resources.appendingPathComponent("wine"))
        for relative in ["wine/bin/wine", "wine/bin/wineserver", "display-mode"] {
            guard fm.isExecutableFile(atPath: resources.appendingPathComponent(relative).path) else {
                throw LaunchError("可执行组件缺失：\(relative)")
            }
        }
        if manifest.presentation == "safe-area-v1" {
            guard fm.isExecutableFile(atPath: resources.appendingPathComponent("display-plan").path),
                  let component = manifest.display_plan else {
                throw LaunchError("自适应显示组件缺失。")
            }
            try verify(["display-plan": component.sha256], under: resources)
        } else if let presentation = manifest.presentation, presentation != "fixed" {
            throw LaunchError("不支持的显示适配版本。")
        }
    }

    func verifyBundle() throws {
        try verifyRuntime()
        try verify(manifest.game_pins, under: resources.appendingPathComponent("prefix-template/drive_c/ra3-mtld3d"))
        try verify(manifest.payload_pins, under: resources.appendingPathComponent("prefix-template/drive_c/u60-mtld3d"))
    }

    func acquireLock() throws {
        try fm.createDirectory(at: support, withIntermediateDirectories: true)
        // O_CLOEXEC keeps this lock out of every spawned child (wine, wineserver,
        // helpers): a launcher killed mid-game must not leave the lock held by its
        // inherited file descriptors after the process itself is gone.
        lockFD = open(support.appendingPathComponent("launcher.lock").path,
                      O_CREAT | O_RDWR | O_CLOEXEC, 0o600)
        guard lockFD >= 0, flock(lockFD, LOCK_EX | LOCK_NB) == 0 else {
            throw LaunchError("游戏启动器已在运行。")
        }
    }

    func prepareDrives(_ directory: URL) throws {
        let drive = directory.appendingPathComponent("dosdevices/z:")
        do {
            guard try fm.destinationOfSymbolicLink(atPath: drive.path) == "/" else {
                throw LaunchError("游戏Z盘映射异常，未覆盖现有文件。")
            }
        } catch let error as LaunchError { throw error }
        catch {
            if fm.fileExists(atPath: drive.path) { throw LaunchError("游戏Z盘映射不是符号链接。") }
            try fm.createSymbolicLink(atPath: drive.path, withDestinationPath: "/")
        }
    }

    func displayPlan() throws -> (AdaptiveDisplayPlan, Data) {
        let (status, output) = try capture(resources.appendingPathComponent("display-plan"), [])
        guard status == 0, let data = output.data(using: .utf8) else {
            throw LaunchError("无法读取显示器安全区域：\(output)")
        }
        let plan = try JSONDecoder().decode(AdaptiveDisplayPlan.self, from: data)
        _ = try plan.viewportEnvironment()
        return (plan, data)
    }

    func initializeAdaptiveResolution(in directory: URL) throws {
        guard manifest.presentation == "safe-area-v1" else { return }
        let (plan, _) = try displayPlan()
        newlyPreparedPlan = plan
        let users = directory.appendingPathComponent("drive_c/users", isDirectory: true)
        guard let enumerator = fm.enumerator(at: users, includingPropertiesForKeys: [.isSymbolicLinkKey],
                                             options: [.skipsHiddenFiles]) else {
            throw LaunchError("无法读取新游戏档案。")
        }
        var configured = 0
        while let file = enumerator.nextObject() as? URL {
            let metadata = try file.resourceValues(forKeys: [.isSymbolicLinkKey])
            if metadata.isSymbolicLink == true { enumerator.skipDescendants(); continue }
            guard file.lastPathComponent == "Options.ini", file.path.contains("/Profiles/") else { continue }
            let original = try String(contentsOf: file, encoding: .utf8)
            let expression = try NSRegularExpression(pattern: "(?m)^Resolution[ \\t]*=[^\\r\\n]*$")
            let range = NSRange(original.startIndex..<original.endIndex, in: original)
            guard expression.numberOfMatches(in: original, range: range) == 1 else {
                throw LaunchError("新游戏档案缺少唯一的分辨率配置。")
            }
            let updated = expression.stringByReplacingMatches(in: original, range: range,
                withTemplate: "Resolution = \(plan.drawable_width) \(plan.drawable_height)")
            try updated.write(to: file, atomically: true, encoding: .utf8)
            configured += 1
        }
        guard configured > 0 else { throw LaunchError("新游戏数据缺少可初始化的档案。") }
    }

    func preparePrefix() throws {
        for item in try fm.contentsOfDirectory(at: support, includingPropertiesForKeys: nil) {
            if item.lastPathComponent.hasPrefix("prefix-preparing-") && item.lastPathComponent.count == 53 {
                try fm.removeItem(at: item)
            }
        }
        if fm.fileExists(atPath: prefix.path) {
            let ready = prefix.appendingPathComponent(".ra3mac-ready.json")
            guard fm.fileExists(atPath: ready.path) else {
                throw LaunchError("发现未完成的游戏数据目录，未覆盖其中任何文件。请查看：\(prefix.path)")
            }
            let marker = try JSONSerialization.jsonObject(with: Data(contentsOf: ready)) as? [String: Any]
            guard marker?["version"] as? String == manifest.version else {
                throw LaunchError("游戏数据与应用版本不同，存档已保留。请先备份此目录，再联系维护者更新游戏数据：\(prefix.path)")
            }
            try prepareDrives(prefix)
            return
        }
        let source = resources.appendingPathComponent("prefix-template", isDirectory: true)
        let staging = support.appendingPathComponent("prefix-preparing-" + UUID().uuidString, isDirectory: true)
        let cloned = source.path.withCString { sourcePath in
            staging.path.withCString { destinationPath in clonefile(sourcePath, destinationPath, 0) }
        }
        do {
            if cloned != 0 { try fm.copyItem(at: source, to: staging) }
            try prepareDrives(staging)
            try verify(manifest.game_pins, under: staging.appendingPathComponent("drive_c/ra3-mtld3d"))
            try verify(manifest.payload_pins, under: staging.appendingPathComponent("drive_c/u60-mtld3d"))
            try initializeAdaptiveResolution(in: staging)
            try writeJSON(["version": manifest.version, "created": ISO8601DateFormatter().string(from: Date())],
                          to: staging.appendingPathComponent(".ra3mac-ready.json"))
            try fm.moveItem(at: staging, to: prefix)
        } catch {
            if fm.fileExists(atPath: staging.path) { try? fm.removeItem(at: staging) }
            throw error
        }
    }

    func prepareOnly() throws {
        try acquireLock()
        try verifyBundle()
        try preparePrefix()
        print("Prepared independent game data: \(prefix.path)")
    }

    func launch(progress: @escaping (String) -> Void = { _ in }) throws {
        progress("正在检查游戏运行环境……")
        try acquireLock()
        guard try !gameRunning() else { throw LaunchError("已有《起义时刻》正在运行，请先退出。") }
        let (rosettaStatus, _) = try capture(URL(fileURLWithPath: "/usr/bin/arch"), ["-x86_64", "/usr/bin/true"])
        guard rosettaStatus == 0 else { throw LaunchError("此应用需要 Rosetta 2，请先安装后重试。") }
        try verifyRuntime()
        progress("正在准备独立游戏数据……")
        try preparePrefix()
        progress("正在校验游戏文件……")
        let game = prefix.appendingPathComponent("drive_c/ra3-mtld3d", isDirectory: true)
        let sourcePayload = prefix.appendingPathComponent("drive_c/u60-mtld3d", isDirectory: true)
        try verify(manifest.game_pins, under: game)
        try verify(manifest.payload_pins, under: sourcePayload)
        let name = "live-" + UUID().uuidString
        let session = support.appendingPathComponent("Logs/" + name, isDirectory: true)
        try fm.createDirectory(at: session.appendingPathComponent("home"), withIntermediateDirectories: true)
        let payloadName = "u60-app-" + UUID().uuidString
        let payload = prefix.appendingPathComponent("drive_c/" + payloadName, isDirectory: true)
        try fm.createDirectory(at: payload, withIntermediateDirectories: false)
        for file in manifest.payload_pins.keys {
            try fm.copyItem(at: sourcePayload.appendingPathComponent(file), to: payload.appendingPathComponent(file))
        }
        try verify(manifest.payload_pins, under: payload)
        let runtime = resources.appendingPathComponent("wine", isDirectory: true)
        let wine = runtime.appendingPathComponent("bin/wine")
        let helper = resources.appendingPathComponent("display-mode")
        let snapshot = session.appendingPathComponent("desktop-before.json")
        var environment = ["HOME": session.appendingPathComponent("home").path,
                           "USER": NSUserName(), "LOGNAME": NSUserName(),
                           "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TMPDIR": session.path,
                           "WINEPREFIX": prefix.path, "WINEARCH": "win64", "WINEMSYNC": "1",
                           "WINEDLLOVERRIDES": "mscoree,mshtml=", "WINEDEBUG": "-all",
                           "RA3_FIXED_VIEWPORT": "3024x1900", "RA3_NO_AUDIO_CAPTURE": "1"]
        var viewport = "3024x1900"
        var adaptivePlan: AdaptiveDisplayPlan?
        if manifest.presentation == "safe-area-v1" {
            let (plan, planData) = try displayPlan()
            if let initial = newlyPreparedPlan, initial != plan {
                throw LaunchError("首次准备期间显示配置已变化，请重新打开应用。游戏数据已保留。")
            }
            adaptivePlan = plan
            environment.removeValue(forKey: "RA3_FIXED_VIEWPORT")
            environment.merge(try plan.viewportEnvironment()) { _, new in new }
            viewport = "\(plan.viewport_width)x\(plan.viewport_height)"
            try planData.write(to: session.appendingPathComponent("display-plan.json"), options: .withoutOverwriting)
        }
        let (snapshotStatus, snapshotOutput) = try capture(helper, ["snapshot", snapshot.path])
        guard snapshotStatus == 0 else { throw LaunchError("无法保存桌面显示模式：\(snapshotOutput)") }
        if let plan = adaptivePlan {
            let saved = try JSONSerialization.jsonObject(with: Data(contentsOf: snapshot)) as? [String: Any]
            guard (saved?["displayID"] as? NSNumber)?.uint32Value == plan.display_id,
                  (try displayPlan()).0 == plan else {
                throw LaunchError("保存显示模式时主显示配置发生变化，请重新打开应用。")
            }
        }
        defer {
            do {
                if try !gameRunning() {
                    let result = try capture(helper, ["restore", snapshot.path])
                    try writeJSON(["exit_code": result.0, "output": result.1],
                                  to: session.appendingPathComponent("desktop-restore.json"))
                }
            } catch { print("桌面恢复检查未完成：\(error)") }
        }
        let arguments = ["C:\\" + payloadName + "\\Uprising60.exe", "C:\\ra3-mtld3d",
                         "C:\\" + payloadName + "\\uprising60.dll"]
        try writeJSON(["version": manifest.version, "runtime": "Rosetta/WoW64 + Wine + mtld3d0.13.0",
                       "viewport": viewport, "presentation": manifest.presentation ?? "fixed",
                       "prefix": prefix.path, "runtime_bundle": runtime.path,
                       "arguments": arguments, "patch_log": payload.appendingPathComponent("uprising60.log").path,
                       "automatic_termination": false], to: session.appendingPathComponent("session.json"))
        let logURL = session.appendingPathComponent("wine.log")
        guard fm.createFile(atPath: logURL.path, contents: nil) else { throw LaunchError("无法创建游戏日志。") }
        let log = try FileHandle(forWritingTo: logURL)
        defer { try? log.close() }
        let process = Process()
        process.executableURL = wine
        process.arguments = arguments
        process.environment = environment
        process.currentDirectoryURL = game
        process.standardOutput = log
        process.standardError = log
        progress("正在启动游戏……")
        try process.run()
        DispatchQueue.main.async {
            (NSApp.delegate as? AppDelegate)?.window?.close()
            NSApp.hide(nil)
        }
        process.waitUntilExit()
        let wait = Process()
        wait.executableURL = runtime.appendingPathComponent("bin/wineserver")
        wait.arguments = ["-w"]
        wait.environment = environment
        wait.currentDirectoryURL = game
        wait.standardOutput = log
        wait.standardError = log
        try wait.run()
        wait.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw LaunchError("游戏启动失败（\(process.terminationStatus)）。日志：\(session.path)")
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var busy = true
    var window: NSWindow?
    var statusLabel: NSTextField?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 410, height: 120),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.title = "红警3：起义时刻"
        window.isReleasedWhenClosed = false
        window.center()
        let label = NSTextField(labelWithString: "正在准备游戏，请稍候……\n首次启动会创建独立游戏数据，保留存档。")
        label.frame = NSRect(x: 24, y: 38, width: 370, height: 50)
        window.contentView?.addSubview(label)
        window.makeKeyAndOrderFront(nil)
        self.window = window
        self.statusLabel = label
        DispatchQueue.global(qos: .userInitiated).async {
            var failure: Error?
            do {
                try GameLauncher().launch { message in
                    DispatchQueue.main.async { self.statusLabel?.stringValue = message }
                }
            } catch { failure = error }
            DispatchQueue.main.async {
                self.busy = false
                self.window?.close()
                if let failure {
                    // After a successful spawn the app hides itself; activate() does not
                    // unhide a hidden app, so unhide first or the alert is invisible.
                    NSApp.unhide(nil)
                    NSApp.activate(ignoringOtherApps: true)
                    let alert = NSAlert()
                    alert.messageText = "游戏未能正常启动"
                    alert.informativeText = String(describing: failure)
                    alert.runModal()
                }
                NSApp.terminate(nil)
            }
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if busy {
            NSApp.unhide(nil)
            NSApp.activate(ignoringOtherApps: true)
            window?.makeKeyAndOrderFront(nil)
            window?.title = "请先从游戏内退出；启动器会自动完成恢复"
            return .terminateCancel
        }
        return .terminateNow
    }
}

let launcherArguments = Array(CommandLine.arguments.dropFirst())
let launcherUsage = """
Usage: RA3Launcher [--help | --verify-only | --prepare-only]
  --help          Show this help without opening a window or loading game data.
  --verify-only   Verify the app resources without starting Wine or the game.
  --prepare-only  Prepare the independent user prefix without starting the game.
Open 红警3起义时刻.app from Applications to play.
"""

if launcherArguments == ["--help"] || launcherArguments == ["-h"] {
    print(launcherUsage)
    exit(0)
} else if !launcherArguments.isEmpty && launcherArguments != ["--verify-only"] && launcherArguments != ["--prepare-only"] {
    FileHandle.standardError.write(Data("启动参数无效。\n\(launcherUsage)\n".utf8))
    exit(2)
} else if launcherArguments == ["--verify-only"] || launcherArguments == ["--prepare-only"] {
    do {
        let launcher = try GameLauncher()
        if launcherArguments == ["--prepare-only"] { try launcher.prepareOnly() }
        else { try launcher.verifyBundle(); print("PASS: standalone app resources and component pins verified") }
    } catch {
        FileHandle.standardError.write(Data("验证失败：\(error)\n".utf8))
        exit(1)
    }
} else {
    guard Bundle.main.bundleURL.pathExtension == "app",
          ["org.ra3mac.UprisingMetal", "org.ra3mac.UprisingMetal.Adaptive"].contains(Bundle.main.bundleIdentifier ?? "") else {
        FileHandle.standardError.write(Data("请从应用程序打开红警3起义时刻.app；独立启动器文件不能直接运行。\n".utf8))
        exit(2)
    }
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.regular)
    app.run()
}
