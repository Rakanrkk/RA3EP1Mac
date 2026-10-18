import Foundation

@main
struct DisplayPlanCLI {
    static func main() {
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments == ["--help"] || arguments == ["-h"] {
            print("Usage: display-plan [--help]\nWith no arguments, print a read-only primary display plan as JSON.")
            return
        }
        guard arguments.isEmpty else {
            FileHandle.standardError.write(Data("display-plan: unexpected arguments\n".utf8))
            exit(2)
        }
        do {
            FileHandle.standardOutput.write(try primaryDisplayPlanData())
            FileHandle.standardOutput.write(Data("\n".utf8))
        } catch {
            FileHandle.standardError.write(Data("显示适配失败：\(error)\n".utf8))
            exit(1)
        }
    }
}
