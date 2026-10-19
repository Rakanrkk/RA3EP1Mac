"""Exercise real launcher controls and the spawn boundary using isolated substitutes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

SWIFT = r'''
import AppKit
import CryptoKit
import Foundation

func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        FileHandle.standardError.write(Data("FAIL: \(message)\n".utf8))
        exit(1)
    }
}
func pump() { RunLoop.main.run(until: Date().addingTimeInterval(0.03)) }
let config = FourRateConfig(pins: [:], profiles: [
    "60": FrameRateProfile(dll: "fps60.dll", log: timingLogName),
    "120": FrameRateProfile(dll: "fps120.dll", log: timingLogName)], defaultFrameRate: 60)

final class Fixture {
    let suite = "org.ra3mac.LauncherFixture." + UUID().uuidString
    let defaults: UserDefaults
    let delegate: AppDelegate
    var ticks: [() -> Void] = []
    var preparations: [(Error?) -> Void] = []
    var launched: [Int?] = []
    var terminations = 0
    var failures: [Error?] = []
    init(automatic: Bool = false, remembered: Int = 60, settings: Bool = false, legacy: Bool = false) {
        defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        defaults.set(remembered, forKey: frameRateDefaultsKey)
        defaults.set(automatic, forKey: frameRateAutostartDefaultsKey)
        delegate = AppDelegate(mode: settings ? .settingsOnly(config: config) : .normal, defaults: defaults)
        delegate.configurationHandler = { legacy ? nil : config }
        delegate.scheduleCountdownTick = { [weak self] in self?.ticks.append($0) }
        delegate.prepareHandler = { [weak self] in self?.preparations.append($0) }
        delegate.launchHandler = { [weak self] rate, completion in self?.launched.append(rate); completion(nil) }
        delegate.legacyLaunchHandler = { [weak self] completion in self?.launched.append(nil); completion(nil) }
        delegate.terminationHandler = { [weak self] in self?.terminations += 1 }
        delegate.finishHandler = { [weak self] in self?.failures.append($0) }
    }
    func open() { delegate.applicationDidFinishLaunching(Notification(name: NSApplication.didFinishLaunchingNotification)); pump() }
    func tick() { check(!ticks.isEmpty, "countdown callback exists"); ticks.removeFirst()(); pump() }
    func prepared(_ error: Error? = nil) { check(!preparations.isEmpty, "preparation callback exists"); preparations.removeFirst()(error); pump() }
    func close() { delegate.finishing = true; delegate.window?.close(); defaults.removePersistentDomain(forName: suite) }
    deinit { close() }
}

func render(_ view: NSView, _ output: URL) {
    view.layoutSubtreeIfNeeded()
    guard let image = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
    view.cacheDisplay(in: view.bounds, to: image)
    try! image.representation(using: .png, properties: [:])!.write(to: output)
}

func backend(_ base: URL, legacy: Bool = false) throws -> GameLauncher {
    let app = base.appendingPathComponent(UUID().uuidString + ".app")
    let resources = app.appendingPathComponent("Contents/Resources")
    let info: [String: Any] = ["CFBundleIdentifier": "org.ra3mac.BackendFixture." + UUID().uuidString,
                               "CFBundlePackageType": "APPL", "CFBundleExecutable": "RA3Launcher"]
    try fm.createDirectory(at: resources, withIntermediateDirectories: true)
    try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
        .write(to: app.appendingPathComponent("Contents/Info.plist"))
    let payloads = ["Uprising60.exe": Data("loader".utf8), "fps60.dll": Data("sixty".utf8), "fps120.dll": Data("one-twenty".utf8)]
    var pins: [String: String] = [:]
    for (name, bytes) in payloads {
        let file = resources.appendingPathComponent("timing/" + name)
        try fm.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try bytes.write(to: file)
        pins[name] = try digest(file)
    }
    var manifest: [String: Any] = ["schema": "ra3mac-app-v1", "version": "1.2.0", "data_version": "1.0",
        "runtime_pins": [:], "game_pins": [:], "payload_pins": [:], "presentation": "fixed"]
    if !legacy {
        manifest["timing_pins"] = pins
        manifest["frame_rate_profiles"] = ["60": ["dll": "fps60.dll", "log": timingLogName],
                                            "120": ["dll": "fps120.dll", "log": timingLogName]]
        manifest["default_frame_rate"] = 60
    }
    try writeJSON(manifest, to: resources.appendingPathComponent("manifest.json"))
    let launcher = try GameLauncher(bundle: Bundle(url: app)!, supportOverride: base.appendingPathComponent(UUID().uuidString))
    for relative in ["drive_c/ra3-mtld3d", "drive_c/u60-mtld3d"] {
        try fm.createDirectory(at: launcher.prefix.appendingPathComponent(relative), withIntermediateDirectories: true)
    }
    launcher.commandRunner = { _, _, _, _ in (0, "fixture") }
    launcher.gameRunningProbe = { false }
    // A real Wine process is impossible in this fixture: the only spawn boundary is replaced.
    launcher.gameProcessRunner = { _, _, _, _, _ in 0 }
    try launcher.acquireLock()
    return launcher
}

@main struct Driver {
    static func main() throws {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let output = URL(fileURLWithPath: CommandLine.arguments[1])
        var groups = 0
        do {
            let f = Fixture(remembered: 90); f.open()
            check(f.delegate.popup?.itemTitles == ["60 FPS", "120 FPS"], "only release rates")
            check(f.delegate.selectedFrameRate() == 60, "invalid saved value defaults to 60")
            f.delegate.autoStartCheckbox!.state = .on
            f.delegate.autoStartChanged(f.delegate.autoStartCheckbox!)
            check(f.defaults.bool(forKey: frameRateAutostartDefaultsKey), "checkbox persists")
            render(f.delegate.window!.contentView!, output.appendingPathComponent("chooser.png"))
            f.close(); groups += 1
        }
        do {
            let f = Fixture(automatic: true, remembered: 120); f.open()
            render(f.delegate.window!.contentView!, output.appendingPathComponent("autostart.png"))
            check(f.delegate.selectedFrameRate() == 120, "last choice selected")
            f.prepared(); check(f.launched.isEmpty, "preparation alone never launches")
            f.tick(); check(f.launched.isEmpty, "first second never launches")
            let stale = f.ticks.first!; f.tick()
            check(f.launched.count == 1 && f.launched[0] == 120, "two-second launch once")
            stale(); f.delegate.startClicked(f.delegate); pump()
            check(f.launched.count == 1, "duplicate timer/button does not launch twice")
            check(f.delegate.applicationShouldTerminate(app) == .terminateNow, "finished launcher can exit after game completion")
            f.close(); groups += 1
        }
        do {
            let f = Fixture(automatic: true); f.open(); f.tick(); f.tick()
            check(f.launched.isEmpty, "countdown alone never launches")
            f.delegate.popup!.selectItem(at: 1); f.delegate.selectionChanged(f.delegate.popup!)
            f.prepared(); check(f.launched.isEmpty, "choice change after countdown interrupts pending launch")
            f.delegate.startClicked(f.delegate); pump()
            check(f.launched.count == 1 && f.launched[0] == 120, "manual start uses changed choice")
            f.close(); groups += 1
        }
        do {
            let f = Fixture(automatic: true); f.open(); let stale = f.ticks.first!
            f.delegate.cancelClicked(f.delegate); check(f.terminations == 1, "cancel requests exit")
            f.prepared(); stale(); pump(); check(f.launched.isEmpty, "cancel ignores late preparation/timer")
            f.close(); groups += 1
        }
        do {
            let f = Fixture(automatic: true); f.open()
            f.prepared(LaunchError("fixture preparation failure")); f.tick(); f.tick()
            check(f.launched.isEmpty && f.failures.count == 1, "preparation error prevents launch")
            f.close(); groups += 1
        }
        do {
            let f = Fixture(automatic: true); f.open()
            f.delegate.window!.performClose(nil); pump()
            check(f.delegate.cancelling && f.terminations == 1, "real window close cancels and exits")
            f.prepared(); check(f.launched.isEmpty, "closed window cannot launch later")
            f.close(); groups += 1
        }
        do {
            let f = Fixture(remembered: 120, settings: true); f.open()
            check(f.preparations.isEmpty && f.launched.isEmpty, "settings-only never prepares")
            f.delegate.popup!.selectItem(at: 0); f.delegate.startClicked(f.delegate); pump()
            check(f.delegate.selectionResult == 60 && f.launched.isEmpty, "settings-only returns choice without launch")
            check(f.defaults.integer(forKey: frameRateDefaultsKey) == 120, "settings-only preserves saved preference")
            f.close(); groups += 1
        }
        do {
            let f = Fixture(automatic: true, remembered: 120, legacy: true); f.open()
            check(f.launched.count == 1 && f.launched[0] == nil, "legacy flow ignores remembered 120")
            check(f.preparations.isEmpty, "legacy substitute does not prepare real data")
            f.close(); groups += 1
        }
        do {
            let launcher = try backend(output)
            var spawns = 0
            launcher.gameProcessRunner = { _, args, _, _, _ in
                check(args.last?.hasSuffix("fps120.dll") == true, "backend selects correct DLL")
                spawns += 1; return 0
            }
            let token = LaunchAuthorization()
            try launcher.launchPrepared(frameRate: 120, authorization: token)
            check(spawns == 1 && token.isCommitted && launcher.lockFD == -1, "real backend permits one authorized substitute spawn")
            groups += 1
        }
        do {
            let launcher = try backend(output)
            let entered = DispatchSemaphore(value: 0), release = DispatchSemaphore(value: 0), done = DispatchSemaphore(value: 0)
            let f = Fixture(); f.delegate.launcher = launcher
            f.delegate.preparationComplete = true; f.delegate.launchInFlight = true
            let token = LaunchAuthorization(); f.delegate.launchAuthorization = token
            var spawns = 0
            launcher.gameProcessRunner = { _, _, _, _, _ in spawns += 1; return 0 }
            var firstProbe = true
            launcher.gameRunningProbe = {
                if firstProbe { firstProbe = false; entered.signal(); _ = release.wait(timeout: .now() + 5) }
                return false
            }
            DispatchQueue.global().async {
                do { try launcher.launchPrepared(frameRate: 60, authorization: token); check(false, "cancelled backend returned success") }
                catch { check(String(describing: error).contains("取消"), "backend cancellation surfaced") }
                done.signal()
            }
            check(entered.wait(timeout: .now() + 5) == .success, "final pre-spawn probe reached")
            f.delegate.cancelStartup(); release.signal()
            check(done.wait(timeout: .now() + 5) == .success, "cancelled backend unwound")
            check(spawns == 0 && !token.isCommitted && launcher.lockFD == -1, "cancel during final probe prevents spawn and releases lock")
            f.close(); groups += 1
        }
        print("PASS: \(groups) launcher UI/backend fixture groups; no Wine or game started")
        exit(0)
    }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("launcher", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    launcher = args.launcher.resolve()
    with tempfile.TemporaryDirectory(prefix="ra3-launcher-ui-") as temporary:
        work = Path(temporary)
        output = args.output.resolve() if args.output else work / "results"
        output.mkdir(parents=True, exist_ok=True)
        driver = work / "Driver.swift"
        fixture_source = work / "LauncherUnderTest.swift"
        binary = work / "Driver"
        source = launcher.read_text()
        entry = "#if !RA3_TEST_DRIVER\nrunLauncherCLI()\n#endif"
        if source.count(entry) != 1:
            raise RuntimeError("The production CLI entry must have one explicit fixture boundary")
        fixture_source.write_text(source.replace(entry, ""))
        driver.write_text(SWIFT)
        command = ["xcrun", "swiftc", "-O", "-warnings-as-errors", "-target", "arm64-apple-macos15.0",
                   "-swift-version", "5", "-D", "RA3_TEST_DRIVER", "-framework", "AppKit", "-framework", "CryptoKit",
                   str(fixture_source), str(driver), "-o", str(binary)]
        compiled = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if compiled.returncode:
            print(compiled.stdout + compiled.stderr)
            return 1
        ran = subprocess.run([str(binary), str(output)], capture_output=True, text=True, timeout=40)
        (output / "ui-fixture.log").write_text(ran.stdout + ran.stderr)
        (output / "ui-fixture.json").write_text(json.dumps({"pass": ran.returncode == 0,
            "launcher_source_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
            "exit_code": ran.returncode, "real_game_started": False,
            "compile_flags": command[2:-5]}, indent=2) + "\n")
        print(ran.stdout + ran.stderr)
        return ran.returncode


if __name__ == "__main__":
    raise SystemExit(main())
