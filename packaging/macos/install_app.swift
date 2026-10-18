// Install a verified local replacement; retire the inspected Wineskin app via Trash.
import Foundation

struct InstallError: Error, CustomStringConvertible {
    let description: String
    init(_ text: String) { description = text }
}

func run(_ executable: String, _ arguments: [String]) throws -> String {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    let pipe = Pipe()
    process.standardOutput = pipe
    process.standardError = pipe
    try process.run()
    let output = pipe.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    let text = String(data: output, encoding: .utf8) ?? ""
    guard process.terminationStatus == 0 else { throw InstallError(text) }
    return text
}

func bundleID(_ app: URL) throws -> String {
    let data = try Data(contentsOf: app.appendingPathComponent("Contents/Info.plist"))
    guard let plist = try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
          let identifier = plist["CFBundleIdentifier"] as? String else {
        throw InstallError("Missing app identity: \(app.path)")
    }
    return identifier
}

do {
    let arguments = CommandLine.arguments
    guard arguments.count == 4 else { throw InstallError("Usage: install-app SOURCE.app TARGET.app REPORT.json") }
    let fm = FileManager.default
    let source = URL(fileURLWithPath: arguments[1]).standardizedFileURL
    let target = URL(fileURLWithPath: arguments[2]).standardizedFileURL
    let report = URL(fileURLWithPath: arguments[3]).standardizedFileURL
    guard source.pathExtension == "app", target.pathExtension == "app",
          target.deletingLastPathComponent().path == "/Applications",
          !fm.fileExists(atPath: report.path) else {
        throw InstallError("Expected an app target in /Applications and a new report path")
    }
    let sourceIdentity = try bundleID(source)
    let allowedIdentities = ["org.ra3mac.UprisingMetal", "org.ra3mac.UprisingMetal.Adaptive"]
    guard allowedIdentities.contains(sourceIdentity) else { throw InstallError("Unexpected source app") }
    let signature = try run("/usr/bin/codesign", ["--verify", "--deep", "--strict", source.path])
    let verified = try run(source.appendingPathComponent("Contents/MacOS/RA3Launcher").path, ["--verify-only"])
    let listing = try run("/bin/ps", ["-axww", "-o", "comm="])
    guard !listing.lowercased().contains("ra3ep1_1.0.game") else { throw InstallError("Game is running") }
    let incoming = target.deletingLastPathComponent().appendingPathComponent(".RA3Mac-install-\(UUID().uuidString).app")
    try fm.copyItem(at: source, to: incoming)
    var retired: NSURL?
    var retiredIdentity: String?
    var installedIncoming = false
    var postSignature = ""
    var postVerify = ""
    do {
        if fm.fileExists(atPath: target.path) {
            let identifier = try bundleID(target)
            guard identifier == "com.红警3起义时刻376265996.wineskin" || allowedIdentities.contains(identifier) else {
                throw InstallError("Refusing to replace an unrelated application")
            }
            retiredIdentity = identifier
            try fm.trashItem(at: target, resultingItemURL: &retired)
        }
        try fm.moveItem(at: incoming, to: target)
        installedIncoming = true
        // Installed post-verification belongs to the same rollback transaction: a
        // failed verification must not leave the new bundle in place.
        postSignature = try run("/usr/bin/codesign", ["--verify", "--deep", "--strict", target.path])
        postVerify = try run(target.appendingPathComponent("Contents/MacOS/RA3Launcher").path, ["--verify-only"])
    } catch {
        if installedIncoming, fm.fileExists(atPath: target.path),
           (try? bundleID(target)) == sourceIdentity {
            // The target now holds this run's own failed copy. Confirm its identity
            // before discarding so a user file can never be deleted, and park it in
            // the Trash as a recoverable failure artifact first.
            var discarded: NSURL?
            try? fm.trashItem(at: target, resultingItemURL: &discarded)
            if fm.fileExists(atPath: target.path) { try? fm.removeItem(at: target) }
        }
        if let retired, !fm.fileExists(atPath: target.path) {
            try? fm.moveItem(at: retired as URL, to: target)
        }
        if fm.fileExists(atPath: incoming.path) { try? fm.removeItem(at: incoming) }
        throw error
    }
    let result: [String: Any] = [
        "source": source.path, "installed": target.path,
        "identifier": try bundleID(target), "retired_identifier": retiredIdentity ?? "",
        "retired_to_trash": retired?.path ?? "",
        "pre_verify": verified, "post_verify": postVerify,
        "pre_signature": signature, "post_signature": postSignature,
        "local_only": true
    ]
    let data = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
    try data.write(to: report, options: .withoutOverwriting)
    print("Installed: \(target.path)")
    if let retired { print("Old app moved to Trash: \(retired.path ?? "")") }
} catch {
    FileHandle.standardError.write(Data("Install failed: \(error)\n".utf8))
    exit(1)
}
