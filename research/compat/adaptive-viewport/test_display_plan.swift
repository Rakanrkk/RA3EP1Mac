import Foundation

@main
struct DisplayPlanTests {
    static func main() throws {
        var checks = 0
        func check(_ condition: @autoclosure () -> Bool, _ name: String) {
            checks += 1
            guard condition() else { fatalError("FAIL: \(name)") }
        }
        func geometry(_ width: Double, _ height: Double, _ scale: Double,
                      _ left: Double = 0, _ top: Double = 0,
                      _ right: Double = 0, _ bottom: Double = 0) -> DisplayGeometry {
            DisplayGeometry(width: width, height: height, left: left, top: top,
                            right: right, bottom: bottom, backingScale: scale)
        }
        let cases: [(String, DisplayGeometry, String, Int, Int)] = [
            ("accepted notch", geometry(1512, 982, 2, 0, 32), "3024x1964:0,64,3024,1964", 3024, 1900),
            ("no notch", geometry(1280, 800, 2), "2560x1600:0,0,2560,1600", 2560, 1600),
            ("large notch synthetic", geometry(1728, 1117, 2, 0, 37), "3456x2234:0,74,3456,2234", 3456, 2160),
            ("external 1x", geometry(1920, 1080, 1), "3840x2160:0,0,3840,2160", 1920, 1080),
            ("scaled framebuffer", geometry(1920, 1200, 2), "3840x2400:0,0,3840,2400", 3840, 2400),
            ("5K framebuffer", geometry(2560, 1440, 2), "5120x2880:0,0,5120,2880", 5120, 2880),
            ("odd dimensions", geometry(1511.5, 981.5, 2, 0, 32), "3023x1963:0,64,3023,1963", 3023, 1899),
            ("fractional inset", geometry(1000, 800, 2, 0.2, 0.25, 0.3, 0.4), "2000x1600:1,1,1999,1599", 1998, 1598),
            ("synthetic1.5backing", geometry(2560, 1440, 1.5), "5120x2880:0,0,5120,2880", 3840, 2160),
            ("side bottom insets", geometry(1920, 1080, 1, 8, 0, 12, 20), "3840x2160:16,0,3816,2120", 1900, 1060),
            ("portrait external", geometry(1080, 1920, 1), "2160x3840:0,0,2160,3840", 1080, 1920),
            ("minimum raw", geometry(160, 100, 2), "320x200:0,0,320,200", 320, 200),
            ("maximum raw", geometry(16384, 16384, 1), "32768x32768:0,0,32768,32768", 16384, 16384)
        ]
        for (name, input, spec, width, height) in cases {
            let plan = try makeSafeDisplayPlan(input)
            check(plan.specification == spec, name + " raw specification")
            check(plan.drawableWidth == width && plan.drawableHeight == height, name + " drawable")
            check(plan.right <= plan.screenWidth && plan.bottom <= plan.screenHeight, name + " containment")
            check(plan.width == plan.right - plan.left && plan.height == plan.bottom - plan.top, name + " extent")
        }
        let invalid = [geometry(.nan, 800, 2), geometry(1000, .infinity, 2),
                       geometry(1000, 800, 0), geometry(1000, 800, 9),
                       geometry(1000, 800, 2, -1), geometry(1000, 800, 2, 500, 0, 500),
                       geometry(1000, 800, 2, 0, 400, 0, 400), geometry(159, 100, 2),
                       geometry(160, 99, 2), geometry(16385, 800, 2),
                       geometry(1512.2, 982, 2), geometry(1000, 800, 2, 0, .nan),
                       geometry(16384, 16384, 8)]
        for (index, input) in invalid.enumerated() {
            do {
                _ = try makeSafeDisplayPlan(input)
                check(false, "invalid geometry \(index) accepted")
            } catch { check(true, "invalid geometry \(index) rejected") }
        }
        print("PASS: \(checks) display-plan checks (pure geometry; no UI or display changes)")
    }
}
