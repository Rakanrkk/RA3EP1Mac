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
    // v1.2 optional additions. Absent fields mean the legacy 60 FPS auto flow.
    let data_version: String?
    let timing_pins: [String: String]?
    let frame_rate_profiles: [String: FrameRateProfile]?
    let default_frame_rate: Int?
    let resource_updates: [String: ResourceUpdate]?

    // The user prefix ready marker records this value, so a new manifest can keep
    // shipping while the on-disk game data (and its marker) stays untouched.
    var dataVersion: String { data_version ?? version }
}

struct DisplayPlanComponent: Decodable {
    let sha256: String
}

struct FrameRateProfile: Decodable {
    let dll: String
    let log: String
}

struct ResourceUpdate: Decodable {
    let previous_sha256: [String]
    let sha256: String
}

struct FourRateConfig {
    let pins: [String: String]
    let profiles: [String: FrameRateProfile]
    let defaultFrameRate: Int
}

let supportedFrameRates: [Int] = [60, 120]
let timingLoaderName = "Uprising60.exe"
let timingLogName = "uprising120.log"
let resourceUpdateKey = "Data/StaticStream.big"
let frameRateDefaultsKey = "RA3PreferredFrameRate"
let frameRateAutostartDefaultsKey = "RA3FrameRateAutostartEnabled"
let defaultFrameRate = 60

extension Manifest {
    var hasFrameRateFields: Bool {
        timing_pins != nil || frame_rate_profiles != nil || default_frame_rate != nil
    }

    // All-or-nothing: when any v1.2 frame-rate field is present, all of them must
    // be present and strictly valid, so the runner never invents paths or rates.
    func frameRateConfig() throws -> FourRateConfig? {
        guard hasFrameRateFields else { return nil }
        guard let pins = timing_pins, let profiles = frame_rate_profiles else {
            throw LaunchError("应用清单帧率配置不完整，请重新安装应用。")
        }
        let expectedPins: Set<String> = [timingLoaderName, "fps60.dll", "fps120.dll"]
        guard Set(pins.keys) == expectedPins, pins.values.allSatisfy(isSha256) else {
            throw LaunchError("应用清单帧率组件清单无效。")
        }
        guard Set(profiles.keys) == Set(supportedFrameRates.map(String.init)) else {
            throw LaunchError("应用清单帧率档位无效。")
        }
        for (rate, profile) in profiles {
            guard profile.dll == "fps\(rate).dll", profile.log == timingLogName else {
                throw LaunchError("应用清单帧率档位无效：\(rate)")
            }
        }
        guard let fallback = default_frame_rate, fallback == defaultFrameRate else {
            throw LaunchError("应用清单缺少或包含无效的默认帧率配置。")
        }
        return FourRateConfig(pins: pins, profiles: profiles, defaultFrameRate: fallback)
    }

    // Only the exact agreed key is accepted; anything else is a manifest error.
    func resourceUpdates() throws -> [String: ResourceUpdate] {
        guard let updates = resource_updates, !updates.isEmpty else { return [:] }
        for (relative, update) in updates {
            guard relative == resourceUpdateKey else {
                throw LaunchError("不支持的游戏资源更新项：\(relative)")
            }
            guard !update.previous_sha256.isEmpty else {
                throw LaunchError("游戏资源更新缺少原版本校验：\(relative)")
            }
            guard isSha256(update.sha256), update.previous_sha256.allSatisfy(isSha256) else {
                throw LaunchError("游戏资源更新校验值无效：\(relative)")
            }
            guard !update.previous_sha256.contains(update.sha256) else {
                throw LaunchError("游戏资源更新校验值配置重复：\(relative)")
            }
        }
        return updates
    }
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

func isSha256(_ value: String) -> Bool {
    guard value.count == 64 else { return false }
    return value.allSatisfy { character in
        (character >= "0" && character <= "9") || (character >= "a" && character <= "f")
    }
}

func isSymbolicLink(_ url: URL) -> Bool {
    (try? url.resourceValues(forKeys: [.isSymbolicLinkKey]))?.isSymbolicLink == true
}

// No component between base and the file may be a symlink, so an upgraded file
// can only land at a canonical location inside the expected tree.
func assertRegularTree(_ base: URL, _ relative: String, label: String) throws {
    guard !isSymbolicLink(base) else {
        throw LaunchError("游戏资源路径包含符号链接，已保留现有数据：\(label)")
    }
    var cursor = base
    for component in relative.split(separator: "/").map(String.init) {
        cursor = cursor.appendingPathComponent(component)
        guard !isSymbolicLink(cursor) else {
            throw LaunchError("游戏资源路径包含符号链接，已保留现有数据：\(label)")
        }
    }
}

// APFS clone first; a plain copy keeps the same "source is never mutated" promise
// on filesystems without clonefile.
func cloneFile(from source: URL, to destination: URL) throws {
    let status = source.path.withCString { origin in
        destination.path.withCString { target in clonefile(origin, target, 0) }
    }
    guard status != 0 else { return }
    try fm.copyItem(at: source, to: destination)
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

final class LaunchAuthorization {
    private enum State { case armed, cancelled, committed }
    private let lock = NSLock()
    private var state = State.armed

    // Marks the launch as interruptible during the final pre-spawn probe.
    // commit() fails only if a cancel landed while armed, so an external
    // cancelStartup during the probe deterministically aborts the spawn.
    func cancel() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard state == .armed else { return false }
        state = .cancelled
        return true
    }

    func commit() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard state == .armed else { return false }
        state = .committed
        return true
    }

    var isCommitted: Bool {
        lock.lock()
        defer { lock.unlock() }
        return state == .committed
    }
}

final class GameLauncher {
    let resources: URL
    let manifest: Manifest
    let support: URL
    let prefix: URL
    var lockFD: Int32 = -1
    var newlyPreparedPlan: AdaptiveDisplayPlan?
    var preparedRateKey: String?
    // Injectable for fixture tests; production always probes the process table.
    var gameRunningProbe: () throws -> Bool = gameRunning
    var commandRunner: (URL, [String], [String: String]?, URL?) throws -> (Int32, String) = { executable, arguments, environment, directory in
        try capture(executable, arguments, environment: environment, directory: directory)
    }
    var gameProcessRunner: ((URL, [String], [String: String], URL, FileHandle) throws -> Int32)?

    // `bundle`/`supportOverride` let fixture tests exercise the real preparation
    // and upgrade transactions without touching the installed app or its data.
    init(bundle: Bundle = .main, supportOverride: URL? = nil) throws {
        guard let resourceURL = bundle.resourceURL else { throw LaunchError("应用资源目录缺失。") }
        resources = resourceURL
        let data = try Data(contentsOf: resourceURL.appendingPathComponent("manifest.json"))
        manifest = try JSONDecoder().decode(Manifest.self, from: data)
        guard manifest.schema == "ra3mac-app-v1" else { throw LaunchError("应用版本清单无效。") }
        support = try supportOverride ?? fm.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                appropriateFor: nil, create: true)
            .appendingPathComponent(bundle.bundleIdentifier ?? "org.ra3mac.UprisingMetal", isDirectory: true)
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
        if let config = try manifest.frameRateConfig() {
            try verify(config.pins, under: resources.appendingPathComponent("timing", isDirectory: true))
        }
    }

    func verifyBundle() throws {
        try verifyRuntime()
        try verify(manifest.game_pins, under: resources.appendingPathComponent("prefix-template/drive_c/ra3-mtld3d"))
        try verify(manifest.payload_pins, under: resources.appendingPathComponent("prefix-template/drive_c/u60-mtld3d"))
        _ = try manifest.resourceUpdates()
    }

    func acquireLock() throws {
        guard lockFD < 0 else { throw LaunchError("游戏启动器已在运行。") }
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

    func releaseLock() {
        guard lockFD >= 0 else { return }
        flock(lockFD, LOCK_UN)
        close(lockFD)
        lockFD = -1
    }

    func cancelPreparedLaunch() { releaseLock() }

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
            guard marker?["version"] as? String == manifest.dataVersion else {
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
            try writeJSON(["version": manifest.dataVersion, "created": ISO8601DateFormatter().string(from: Date())],
                          to: staging.appendingPathComponent(".ra3mac-ready.json"))
            try fm.moveItem(at: staging, to: prefix)
        } catch {
            if fm.fileExists(atPath: staging.path) { try? fm.removeItem(at: staging) }
            throw error
        }
    }

    // Resource upgrade contract (v1.2): only runs on explicit prepare/launch,
    // only while the launch lock is held and the game is not running. A known
    // previous hash is backed up via APFS clone before an atomic rename; an
    // unknown hash fails the launch and preserves the file untouched.
    func applyResourceUpgrades(_ updates: [String: ResourceUpdate]) throws {
        guard !updates.isEmpty else { return }
        guard lockFD >= 0 else { throw LaunchError("内部状态异常：资源更新前未持有启动锁。") }
        guard try !gameRunningProbe() else { throw LaunchError("已有《起义时刻》正在运行，无法更新游戏资源。") }
        for (relative, update) in updates.sorted(by: { $0.key < $1.key }) {
            let targetRelative = "drive_c/ra3-mtld3d/" + relative
            let target = prefix.appendingPathComponent(targetRelative)
            let template = resources.appendingPathComponent("prefix-template", isDirectory: true)
                .appendingPathComponent(targetRelative)
            guard fm.fileExists(atPath: target.path) else {
                throw LaunchError("待更新的游戏资源缺失，已保留现有数据：\(relative)")
            }
            try assertRegularTree(prefix, targetRelative, label: relative)
            let existing = try digest(target)
            guard existing != update.sha256 else { continue }
            guard update.previous_sha256.contains(existing) else {
                throw LaunchError("游戏资源版本未知，已保留原文件不做更新，请手动核对：\(relative)")
            }
            guard fm.fileExists(atPath: template.path),
                  try digest(template) == update.sha256 else {
                throw LaunchError("应用内资源模板校验失败，已保留原文件不做更新：\(relative)")
            }
            try assertRegularTree(resources.appendingPathComponent("prefix-template", isDirectory: true),
                                  targetRelative, label: relative)
            let backupRoot = support.appendingPathComponent(
                "Backups/resources-v\(manifest.version)-\(UUID().uuidString)", isDirectory: true)
            do {
                try fm.createDirectory(at: backupRoot.appendingPathComponent(relative).deletingLastPathComponent(),
                                       withIntermediateDirectories: true)
                let backup = backupRoot.appendingPathComponent(relative)
                try cloneFile(from: target, to: backup)
                // The backup is the only copy of the old file once we rename;
                // its integrity is verified before the upgrade may proceed.
                guard try digest(backup) == existing else {
                    throw LaunchError("资源备份完整性校验失败：\(relative)")
                }
                try writeJSON(["relative": relative, "previous_sha256": existing, "sha256": update.sha256,
                               "manifest_version": manifest.version, "data_version": manifest.dataVersion,
                               "created": ISO8601DateFormatter().string(from: Date())],
                              to: backupRoot.appendingPathComponent("metadata.json"))
            } catch {
                throw LaunchError("游戏资源备份失败，已保留原文件不做更新：\(relative)")
            }
            let staging = target.deletingLastPathComponent()
                .appendingPathComponent(".ra3-resource-\(UUID().uuidString)")
            do {
                try cloneFile(from: template, to: staging)
                guard try digest(staging) == update.sha256 else {
                    throw LaunchError("资源更新暂存校验失败：\(relative)")
                }
                guard try digest(target) == existing else {
                    throw LaunchError("资源更新提交前文件已变化，已保留原文件：\(relative)")
                }
                // POSIX rename over the verified old file is atomic; readers see
                // either the whole old file or the whole new file, never a mix.
                let replaced = staging.path.withCString { source in
                    target.path.withCString { destination in rename(source, destination) }
                }
                guard replaced == 0 else {
                    throw LaunchError("游戏资源更新失败：\(relative)（\(String(cString: strerror(errno)))）")
                }
            } catch {
                if fm.fileExists(atPath: staging.path) { try? fm.removeItem(at: staging) }
                throw error
            }
        }
    }

    func prepareOnly() throws {
        try acquireLock()
        try verifyBundle()
        let updates = try manifest.resourceUpdates()
        if !updates.isEmpty {
            // --prepare-only historically ran without a game-process check; the
            // check is mandatory whenever resource updates would be applied.
            guard try !gameRunningProbe() else {
                throw LaunchError("已有《起义时刻》正在运行，无法准备或更新游戏数据。")
            }
        }
        try preparePrefix()
        try applyResourceUpgrades(updates)
        print("Prepared independent game data: \(prefix.path)")
    }

    func prepareForLaunch(progress: @escaping (String) -> Void = { _ in }) throws {
        try acquireLock()
        do {
            progress("正在检查游戏运行环境……")
            guard try !gameRunningProbe() else {
                throw LaunchError("已有《起义时刻》正在运行，请先退出。")
            }
            let (rosettaStatus, _) = try commandRunner(URL(fileURLWithPath: "/usr/bin/arch"), ["-x86_64", "/usr/bin/true"], nil, nil)
            guard rosettaStatus == 0 else { throw LaunchError("此应用需要 Rosetta 2，请先安装后重试。") }
            try verifyRuntime()
            progress("正在准备游戏……")
            try preparePrefix()
            try applyResourceUpgrades(try manifest.resourceUpdates())
            progress("正在校验游戏文件……")
            let game = prefix.appendingPathComponent("drive_c/ra3-mtld3d", isDirectory: true)
            try verify(manifest.game_pins, under: game)
        } catch {
            releaseLock()
            throw error
        }
    }

    func launch(frameRate: Int? = nil, progress: @escaping (String) -> Void = { _ in }) throws {
        try prepareForLaunch(progress: progress)
        try launchPrepared(frameRate: frameRate, progress: progress)
    }

    func launchPrepared(frameRate: Int? = nil, authorization: LaunchAuthorization? = nil,
                        progress: @escaping (String) -> Void = { _ in }) throws {
        defer { releaseLock() }
        let config = try manifest.frameRateConfig()
        var selectedProfile: FrameRateProfile?
        var selectedRate: Int?
        if let config {
            guard let frameRate, let profile = config.profiles[String(frameRate)] else {
                throw LaunchError("所选帧率不受支持，请重新打开应用选择。")
            }
            selectedProfile = profile
            selectedRate = frameRate
        } else if let frameRate, frameRate != 60 {
            throw LaunchError("此应用版本仅支持 60 FPS 启动。")
        }
        let game = prefix.appendingPathComponent("drive_c/ra3-mtld3d", isDirectory: true)
        let name = "live-" + UUID().uuidString
        let session = support.appendingPathComponent("Logs/" + name, isDirectory: true)
        try fm.createDirectory(at: session.appendingPathComponent("home"), withIntermediateDirectories: true)
        let payloadName = "u60-app-" + UUID().uuidString
        let payload = prefix.appendingPathComponent("drive_c/" + payloadName, isDirectory: true)
        try fm.createDirectory(at: payload, withIntermediateDirectories: false)
        var launchDLLName: String
        var patchLogName: String
        if let config, let profile = selectedProfile {
            // New profiles come from the read-only app bundle Resources/timing;
            // only the loader and the selected DLL are copied into this session.
            let timing = resources.appendingPathComponent("timing", isDirectory: true)
            guard let loaderPin = config.pins[timingLoaderName], let dllPin = config.pins[profile.dll] else {
                throw LaunchError("帧率组件校验清单不完整。")
            }
            for file in [timingLoaderName, profile.dll] {
                try fm.copyItem(at: timing.appendingPathComponent(file), to: payload.appendingPathComponent(file))
            }
            try verify([timingLoaderName: loaderPin, profile.dll: dllPin], under: payload)
            launchDLLName = profile.dll
            patchLogName = profile.log
        } else {
            let sourcePayload = prefix.appendingPathComponent("drive_c/u60-mtld3d", isDirectory: true)
            try verify(manifest.payload_pins, under: sourcePayload)
            for file in manifest.payload_pins.keys {
                try fm.copyItem(at: sourcePayload.appendingPathComponent(file), to: payload.appendingPathComponent(file))
            }
            try verify(manifest.payload_pins, under: payload)
            launchDLLName = "uprising60.dll"
            patchLogName = "uprising60.log"
        }
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
        let (snapshotStatus, snapshotOutput) = try commandRunner(helper, ["snapshot", snapshot.path], nil, nil)
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
                if try !gameRunningProbe() {
                    let result = try commandRunner(helper, ["restore", snapshot.path], nil, nil)
                    try writeJSON(["exit_code": result.0, "output": result.1],
                                  to: session.appendingPathComponent("desktop-restore.json"))
                }
            } catch { print("桌面恢复检查未完成：\(error)") }
        }
        let arguments = ["C:\\" + payloadName + "\\Uprising60.exe", "C:\\ra3-mtld3d",
                         "C:\\" + payloadName + "\\" + launchDLLName]
        var sessionInfo: [String: Any] = ["version": manifest.version,
                       "runtime": "Rosetta/WoW64 + Wine + mtld3d0.13.0",
                       "viewport": viewport, "presentation": manifest.presentation ?? "fixed",
                       "prefix": prefix.path, "runtime_bundle": runtime.path,
                       "arguments": arguments, "patch_log": payload.appendingPathComponent(patchLogName).path,
                       "automatic_termination": false]
        if let config, let profile = selectedProfile, let rate = selectedRate {
            sessionInfo["frame_rate"] = rate
            sessionInfo["frame_rate_profile"] = ["dll": profile.dll, "log": profile.log]
            sessionInfo["timing_pins"] = [timingLoaderName: config.pins[timingLoaderName] ?? "",
                                          profile.dll: config.pins[profile.dll] ?? ""]
        }
        try writeJSON(sessionInfo, to: session.appendingPathComponent("session.json"))
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
        guard lockFD >= 0 else { throw LaunchError("启动状态已取消，请重新打开应用。") }
        guard try !gameRunningProbe() else { throw LaunchError("已有《起义时刻》正在运行，请先退出。") }
        if let authorization {
            guard authorization.commit() else { throw LaunchError("启动已取消。") }
        }
        guard authorization?.isCommitted ?? true else {
            throw LaunchError("启动已取消。")
        }
        if let gameProcessRunner {
            let status = try gameProcessRunner(wine, arguments, environment, game, log)
            guard status == 0 else { throw LaunchError("游戏启动失败（\(status)）。") }
            return
        }
        try process.run()
        DispatchQueue.main.async {
            if let delegate = NSApp.delegate as? AppDelegate {
                delegate.window?.close()
                NSApp.hide(nil)
            }
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

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    enum Mode {
        case normal
        case settingsOnly(config: FourRateConfig)
    }

    let mode: Mode
    let defaults: UserDefaults
    var busy = false
    var launchInFlight = false
    var finishing = false
    var preparing = false
    var preparationComplete = false
    var launchRequested = false
    var cancelling = false
    var window: NSWindow?
    var statusLabel: NSTextField?
    var countdownLabel: NSTextField?
    var popup: NSPopUpButton?
    var autoStartCheckbox: NSButton?
    var launcher: GameLauncher?
    var selectionResult: Int?
    var selectedRate: Int?
    var launchAuthorization: LaunchAuthorization?
    var autoCountdownActive = false
    var autoLaunchAwaitingPreparation = false
    var countdownRemaining = 0
    var countdownGeneration = 0
    var prepareHandler: ((@escaping (Error?) -> Void) -> Void)?
    var launchHandler: ((Int?, @escaping (Error?) -> Void) -> Void)?
    var legacyLaunchHandler: ((@escaping (Error?) -> Void) -> Void)?
    var configurationHandler: (() throws -> FourRateConfig?)?
    var terminationHandler: (() -> Void)?
    var finishHandler: ((Error?) -> Void)?
    var scheduleCountdownTick: (@escaping () -> Void) -> Void = { callback in
        DispatchQueue.main.asyncAfter(deadline: .now() + 1, execute: callback)
    }

    init(mode: Mode, defaults: UserDefaults = .standard) {
        self.mode = mode
        self.defaults = defaults
    }

    var settingsOnly: Bool {
        if case .settingsOnly = mode { return true }
        return false
    }

    var started: Bool { launchAuthorization?.isCommitted == true }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if settingsOnly {
            showChooser(automaticCountdown: false)
            return
        }
        do {
            let config: FourRateConfig?
            if let configurationHandler {
                config = try configurationHandler()
            } else {
                let launcher = try GameLauncher()
                self.launcher = launcher
                config = try launcher.manifest.frameRateConfig()
            }
            if config != nil {
                if defaults.bool(forKey: frameRateAutostartDefaultsKey) {
                    beginAutomaticLaunch()
                } else {
                    showChooser(automaticCountdown: false)
                }
            } else {
                beginLegacyLaunch()
            }
        } catch { finish(with: error) }
    }

    func rememberedFrameRate() -> Int {
        let stored = defaults.object(forKey: frameRateDefaultsKey) as? Int ?? defaultFrameRate
        return supportedFrameRates.contains(stored) ? stored : defaultFrameRate
    }

    func selectedFrameRate() -> Int {
        guard let popup else { return selectedRate ?? rememberedFrameRate() }
        let index = min(max(popup.indexOfSelectedItem, 0), supportedFrameRates.count - 1)
        return supportedFrameRates[index]
    }

    func ensureWindow(height: CGFloat) -> NSWindow {
        if let window { return window }
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 410, height: height),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "红警3：起义时刻"
        window.isReleasedWhenClosed = false
        window.center()
        window.delegate = self
        self.window = window
        return window
    }

    func showChooser(automaticCountdown: Bool) {
        let window = ensureWindow(height: 232)
        window.title = "红警3：起义时刻"
        window.setContentSize(NSSize(width: 410, height: 232))
        window.contentView?.subviews.forEach { $0.removeFromSuperview() }

        let prompt = NSTextField(labelWithString: "选择帧率：")
        prompt.frame = NSRect(x: 24, y: 190, width: 362, height: 22)
        let popup = NSPopUpButton(frame: NSRect(x: 24, y: 153, width: 362, height: 28))
        for rate in supportedFrameRates { popup.addItem(withTitle: "\(rate) FPS") }
        popup.selectItem(at: supportedFrameRates.firstIndex(of: selectedRate ?? rememberedFrameRate()) ?? 0)
        popup.target = self
        popup.action = #selector(selectionChanged(_:))
        popup.setAccessibilityLabel("帧率")

        let checkbox = NSButton(checkboxWithTitle: "记住选择并自动启动", target: self,
                                action: #selector(autoStartChanged(_:)))
        checkbox.frame = NSRect(x: 24, y: 111, width: 362, height: 24)
        checkbox.state = defaults.bool(forKey: frameRateAutostartDefaultsKey) ? .on : .off
        if settingsOnly { checkbox.isHidden = true }
        checkbox.setAccessibilityLabel("记住选择并自动启动")

        let countdown = NSTextField(labelWithString: "")
        countdown.frame = NSRect(x: 24, y: 76, width: 362, height: 22)
        countdown.isHidden = !automaticCountdown
        if automaticCountdown {
            countdown.stringValue = "正在准备游戏；2 秒后开始，可取消或更改帧率。"
        }

        let start = NSButton(title: "开始游戏", target: self, action: #selector(startClicked(_:)))
        start.bezelStyle = .rounded
        start.frame = NSRect(x: 246, y: 22, width: 140, height: 32)
        let cancel = NSButton(title: "取消", target: self, action: #selector(cancelClicked(_:)))
        cancel.bezelStyle = .rounded
        cancel.keyEquivalent = "\u{1b}"
        cancel.frame = NSRect(x: 24, y: 22, width: 140, height: 32)
        start.setAccessibilityLabel("开始游戏")
        cancel.setAccessibilityLabel("取消")

        for view in [prompt, popup, checkbox, countdown, start, cancel] { window.contentView?.addSubview(view) }
        self.popup = popup
        autoStartCheckbox = checkbox
        countdownLabel = countdown
        statusLabel = nil
        window.makeKeyAndOrderFront(nil)
        NSApp.activate()
    }

    func showProgressInterface() {
        let window = ensureWindow(height: 120)
        window.setContentSize(NSSize(width: 410, height: 120))
        window.contentView?.subviews.forEach { $0.removeFromSuperview() }
        let label = NSTextField(labelWithString: "正在准备游戏，请稍候……")
        label.frame = NSRect(x: 24, y: 55, width: 362, height: 28)
        let cancel = NSButton(title: "取消", target: self, action: #selector(cancelClicked(_:)))
        cancel.bezelStyle = .rounded
        cancel.keyEquivalent = "\u{1b}"
        cancel.frame = NSRect(x: 135, y: 14, width: 140, height: 30)
        window.contentView?.addSubview(label)
        window.contentView?.addSubview(cancel)
        statusLabel = label
        countdownLabel = nil
        popup = nil
        autoStartCheckbox = nil
        window.makeKeyAndOrderFront(nil)
    }

    func beginAutomaticLaunch() {
        guard !preparing, !launchInFlight, !cancelling else { return }
        selectedRate = rememberedFrameRate()
        autoCountdownActive = true
        countdownRemaining = 2
        busy = true
        showChooser(automaticCountdown: true)
        startPreparation()
        scheduleNextCountdownTick()
    }

    func scheduleNextCountdownTick() {
        guard autoCountdownActive, !cancelling, !launchRequested else { return }
        countdownGeneration += 1
        let generation = countdownGeneration
        scheduleCountdownTick { [weak self] in
            guard let self, self.autoCountdownActive, !self.cancelling,
                  self.countdownGeneration == generation else { return }
            self.countdownRemaining -= 1
            if self.countdownRemaining <= 0 {
                self.countdownLabel?.stringValue = "正在启动游戏……"
                self.autoCountdownActive = false
                self.autoLaunchAwaitingPreparation = true
                if self.preparationComplete {
                    self.launchRequested = true
                    self.attemptLaunch()
                }
            } else {
                self.countdownLabel?.stringValue = "正在准备游戏；1 秒后开始，可取消或更改帧率。"
                self.scheduleNextCountdownTick()
            }
        }
    }

    func cancelCountdown() {
        countdownGeneration += 1
        autoCountdownActive = false
    }

    func startPreparation() {
        guard !preparing, !preparationComplete, !cancelling else { return }
        preparing = true
        if let prepareHandler {
            prepareHandler { [weak self] error in
                DispatchQueue.main.async { self?.preparationDidFinish(error) }
            }
            return
        }
        guard let launcher else {
            preparationDidFinish(LaunchError("启动器尚未就绪。"))
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            var failure: Error?
            do {
                try launcher.prepareForLaunch { message in
                    DispatchQueue.main.async { self.statusLabel?.stringValue = message }
                }
            } catch { failure = error }
            DispatchQueue.main.async { self.preparationDidFinish(failure) }
        }
    }

    func preparationDidFinish(_ failure: Error?) {
        preparing = false
        if cancelling {
            if failure == nil { launcher?.cancelPreparedLaunch() }
            return
        }
        if let failure { finish(with: failure); return }
        preparationComplete = true
        if autoLaunchAwaitingPreparation {
            autoLaunchAwaitingPreparation = false
            launchRequested = true
        }
        attemptLaunch()
    }

    func beginLegacyLaunch() {
        guard !launchRequested, !launchInFlight, !cancelling else { return }
        launchRequested = true
        busy = true
        cancelCountdown()
        if legacyLaunchHandler != nil {
            attemptLegacyLaunch()
            return
        }
        showProgressInterface()
        startPreparation()
        attemptLaunch()
    }

    func attemptLegacyLaunch() {
        guard !launchInFlight, !cancelling else { return }
        launchInFlight = true
        let completion: (Error?) -> Void = { [weak self] failure in
            DispatchQueue.main.async { self?.finish(with: failure) }
        }
        legacyLaunchHandler?(completion)
    }

    func attemptLaunch() {
        guard preparationComplete, launchRequested, !launchInFlight, !cancelling else { return }
        launchInFlight = true
        cancelCountdown()
        // nil means the legacy 60-only flow: it must stay nil so launchPrepared
        // keeps using uprising60.dll instead of resolving a remembered profile.
        let rate = selectedRate
        if let rate { defaults.set(rate, forKey: frameRateDefaultsKey) }
        showProgressInterface()
        let authorization = LaunchAuthorization()
        launchAuthorization = authorization
        let completion: (Error?) -> Void = { [weak self] failure in
            DispatchQueue.main.async { self?.finish(with: failure) }
        }
        if let launchHandler {
            guard authorization.commit() else { return }
            launchHandler(rate, completion)
            return
        }
        guard let launcher else {
            completion(LaunchError("启动器尚未就绪。"))
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            var failure: Error?
            do { try launcher.launchPrepared(frameRate: rate, authorization: authorization) }
            catch { failure = error }
            completion(failure)
        }
    }

    func finish(with failure: Error?) {
        guard !finishing, !cancelling else { return }
        finishing = true
        busy = false
        window?.close()
        if let finishHandler {
            finishHandler(failure)
            return
        }
        if let failure {
            NSApp.unhide(nil)
            NSApp.activate()
            let alert = NSAlert()
            alert.messageText = "无法启动游戏"
            alert.informativeText = String(describing: failure)
            alert.runModal()
        }
        NSApp.terminate(nil)
    }

    @objc func selectionChanged(_ sender: Any) {
        cancelCountdown()
        autoLaunchAwaitingPreparation = false
        countdownLabel?.isHidden = true
        selectedRate = selectedFrameRate()
    }

    @objc func autoStartChanged(_ sender: Any) {
        if settingsOnly { return }
        guard let checkbox = sender as? NSButton else { return }
        defaults.set(checkbox.state == .on, forKey: frameRateAutostartDefaultsKey)
        if checkbox.state == .off {
            cancelCountdown()
            autoLaunchAwaitingPreparation = false
            countdownLabel?.isHidden = true
        }
    }

    @objc func startClicked(_ sender: Any) {
        guard !launchRequested, !launchInFlight, !cancelling else { return }
        let rate = selectedFrameRate()
        if settingsOnly {
            selectionResult = rate
            finishSettingsOnly()
            return
        }
        defaults.set(rate, forKey: frameRateDefaultsKey)
        selectedRate = rate
        launchRequested = true
        cancelCountdown()
        busy = true
        if !preparing {
            showProgressInterface()
            startPreparation()
        } else {
            countdownLabel?.stringValue = "正在准备游戏……"
        }
        attemptLaunch()
    }

    @objc func cancelClicked(_ sender: Any) {
        if settingsOnly {
            finishSettingsOnly()
            return
        }
        if started {
            bringToFront(title: "请先退出游戏，再关闭启动器。")
            return
        }
        cancelStartup()
        requestTermination()
    }

    func cancelStartup() {
        guard !started else { return }
        cancelling = true
        launchRequested = false
        autoLaunchAwaitingPreparation = false
        cancelCountdown()
        // Cancel the authorization unconditionally first: a launch already
        // waiting in its final game-process probe must observe this and abort
        // before any spawn. Releasing the lock afterwards lets that cancelled
        // path unwind without ever reaching Process.run.
        _ = launchAuthorization?.cancel()
        if preparationComplete && !launchInFlight { launcher?.cancelPreparedLaunch() }
    }

    func finishSettingsOnly() {
        guard !cancelling else { return }
        cancelling = true
        window?.close()
        DispatchQueue.main.async {
            let app = NSApplication.shared
            app.stop(nil)
            let event = NSEvent.otherEvent(with: .applicationDefined, location: .zero, modifierFlags: [],
                                           timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: 0,
                                           context: nil, subtype: 0, data1: 0, data2: 0)
            if let event { app.postEvent(event, atStart: true) }
        }
    }

    func bringToFront(title: String) {
        NSApp.unhide(nil)
        NSApp.activate()
        window?.makeKeyAndOrderFront(nil)
        window?.title = title
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if started {
            bringToFront(title: "请先退出游戏，再关闭启动器。")
            return false
        }
        if busy { cancelStartup() }
        return true
    }

    // Requests process termination. The fixture replaces this so tests never
    // call the real NSApp.terminate; production uses NSApp.terminate(nil).
    func requestTermination() {
        if let terminationHandler { terminationHandler(); return }
        NSApp.terminate(nil)
    }

    func windowWillClose(_ notification: Notification) {
        guard !finishing else { return }
        if settingsOnly {
            finishSettingsOnly()
            return
        }
        if busy && !started {
            // windowShouldClose already cancelled; this second close event must
            // still terminate the app, otherwise the launcher lingers hidden.
            cancelStartup()
            requestTermination()
            return
        }
        if !busy {
            cancelling = true
            requestTermination()
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if finishing { return .terminateNow }
        if started {
            bringToFront(title: "请先退出游戏，再关闭启动器。")
            return .terminateCancel
        }
        cancelStartup()
        return .terminateNow
    }
}
let launcherArguments = Array(CommandLine.arguments.dropFirst())
let launcherUsage = """
Usage: RA3Launcher [--help | --verify-only | --prepare-only | --fps <rate> | --settings-only]
  --help           Show this help without opening a window or loading game data.
  --verify-only    Verify the app resources (including frame-rate components) without starting Wine or the game.
  --prepare-only   Prepare the independent user prefix and apply pending resource updates, without starting the game.
  --fps <rate>     Start the game directly at 60 or 120 FPS without the chooser window (app bundle only).
  --settings-only  Show the frame-rate chooser, print the selection and exit without touching game data (app bundle only).
Open 红警3起义时刻.app from Applications to play.
"""

func failUsage() -> Never {
    FileHandle.standardError.write(Data("启动参数无效。\n\(launcherUsage)\n".utf8))
    exit(2)
}

func requireAppBundle() {
    guard Bundle.main.bundleURL.pathExtension == "app",
          ["org.ra3mac.UprisingMetal", "org.ra3mac.UprisingMetal.Adaptive"].contains(Bundle.main.bundleIdentifier ?? "") else {
        FileHandle.standardError.write(Data("请从应用程序打开红警3起义时刻.app；独立启动器文件不能直接运行。\n".utf8))
        exit(2)
    }
}

func runLauncherCLI() {
    if launcherArguments == ["--help"] || launcherArguments == ["-h"] {
        print(launcherUsage)
        exit(0)
    } else if launcherArguments == ["--verify-only"] || launcherArguments == ["--prepare-only"] {
        do {
            let launcher = try GameLauncher()
            if launcherArguments == ["--prepare-only"] { try launcher.prepareOnly() }
            else { try launcher.verifyBundle(); print("PASS: standalone app resources and component pins verified") }
        } catch {
            FileHandle.standardError.write(Data("验证失败：\(error)\n".utf8))
            exit(1)
        }
    } else if launcherArguments.count == 2, launcherArguments[0] == "--fps" {
        let value = launcherArguments[1]
        guard let fps = Int(value), supportedFrameRates.contains(fps), String(fps) == value else { failUsage() }
        requireAppBundle()
        do {
            let launcher = try GameLauncher()
            let config = try launcher.manifest.frameRateConfig()
            if config == nil, fps != 60 {
                FileHandle.standardError.write(Data("此应用版本仅支持 60 FPS 启动，无法以 \(fps) FPS 启动。\n".utf8))
                exit(1)
            }
            try launcher.launch(frameRate: config == nil ? nil : fps, progress: { print($0) })
        } catch {
            FileHandle.standardError.write(Data("启动失败：\(error)\n".utf8))
            exit(1)
        }
    } else if launcherArguments == ["--settings-only"] {
        requireAppBundle()
        do {
            let launcher = try GameLauncher()
            guard let config = try launcher.manifest.frameRateConfig() else {
                FileHandle.standardError.write(Data("此应用版本不支持帧率选择。\n".utf8))
                exit(1)
            }
            let app = NSApplication.shared
            let delegate = AppDelegate(mode: .settingsOnly(config: config))
            delegate.launcher = launcher
            app.delegate = delegate
            app.setActivationPolicy(.regular)
            app.run()
            print(delegate.selectionResult.map { "Selected frame rate: \($0) FPS" } ?? "Frame rate selection cancelled.")
            exit(0)
        } catch {
            FileHandle.standardError.write(Data("验证失败：\(error)\n".utf8))
            exit(1)
        }
    } else if launcherArguments.isEmpty {
        requireAppBundle()
        let app = NSApplication.shared
        let delegate = AppDelegate(mode: .normal)
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.run()
    } else {
        failUsage()
    }
}

#if !RA3_TEST_DRIVER
runLauncherCLI()
#endif
