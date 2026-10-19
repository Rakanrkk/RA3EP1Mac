# RA3EP1Mac 1.2.0

## 中文

RA3EP1Mac 1.2.0 在此前的自适应全屏实现上新增启动帧率选择。

### 新特性

- **启动帧率选择（60 / 120 FPS）**：启动窗口可选 60 或 120 FPS，默认 60。可勾选“记住选择并自动启动”，下次启动按记住的选择直接开始，自动开始前约 2 秒内可更改帧率或取消。也可用 `--fps 60` / `--fps 120` 直接启动。
- **游戏速度不变**：游戏逻辑保持原版 15 Hz；粒子与悬挂物理保持原始 30 Hz 节奏，画面按所选帧率插值。实际帧率取决于硬件、画质和场景。
- **存档与设置原样保留**：帧率选择不影响存档、设置和录像；用户数据目录与身份不变，升级后无需迁移。
- **载荷完整性**：每档帧率对应独立的时序载荷，随 App 内置并由清单绑定 SHA-256；每次启动使用当次会话副本并校验，不修改 App 内文件。

### 已知问题

天狗（喷气形态）在移动中降落时的变形衔接仍在调查中；原地降落不受影响。进展见独立项目 [RA3EP1 FPS Unlock](https://github.com/Rakanrkk/RA3EP1-FPS-Unlock)。

### 验证范围

实际游戏测试在一台 2× 刘海屏的 Apple Silicon Mac 上完成：60 FPS 为长期使用基准，120 FPS 已通过本机目视验收。其他机型、多显示器、运行中更改显示配置和完整战役／挑战未全部覆盖。仅支持横屏。

### 下载

按 GitHub 页面顺序：

- **运行环境（Runtime）**：`RA3EP1Mac-v1.2.0-macos-arm64-runtime.dmg`，含 Wine 运行时、prefix 模板、时序载荷和 App 构建 helper；不含游戏文件，游戏需自行导入，构建步骤见 [README](https://github.com/Rakanrkk/RA3EP1Mac/blob/v1.2.0/README.md)。
- **完整版（Standalone）**：`RA3EP1Mac-v1.2.0-macos-arm64-standalone.001.dmg` 及 `.002`–`.004.dmgpart` 分卷，含运行时与游戏的即装 App。四个分卷放在同一目录并保持原文件名，只打开 `.001.dmg`，macOS 自动读取其余分卷，无需拼接。
- **Wine 源码**：`RA3EP1Mac-v1.2.0-wine-source.tar.zst`，与 Runtime 版匹配、含 RA3Mac 补丁的 Wine 源码归档。
- `release-manifest.json` 与 `SHA256SUMS.txt`：发行文件清单与 SHA-256 校验值，打开镜像前请先核对。

### 组件与许可

Git 仓库不含游戏文件。第三方组件及许可见 [README 致谢](https://github.com/Rakanrkk/RA3EP1Mac/blob/v1.2.0/README.md#开源致谢)与 [THIRD-PARTY-NOTICES.md](https://github.com/Rakanrkk/RA3EP1Mac/blob/v1.2.0/THIRD-PARTY-NOTICES.md)。原创源码采用 [MIT](https://github.com/Rakanrkk/RA3EP1Mac/blob/v1.2.0/LICENSE) 许可；Wine 衍生补丁保留 LGPL 范围。发行包使用 ad-hoc 完整性签名，不含个人开发者证书，首次打开可在系统隐私与安全性中批准。

---

## English

RA3EP1Mac 1.2.0 adds a startup frame-rate choice on top of the adaptive fullscreen implementation.

### Highlights

- **Frame-rate choice at startup (60 / 120 FPS)**: the startup window offers 60 or 120 FPS, defaulting to 60. Enable "remember and launch automatically" and the next launch starts directly with the remembered choice, with a 2-second window to change the rate or cancel before the game starts. `--fps 60` / `--fps 120` also start directly.
- **Game speed unchanged**: game logic keeps the native 15 Hz; particle and suspension physics keep their authored 30 Hz cadence while the picture updates at the chosen rate. Actual frame rate depends on hardware, quality settings and the scene.
- **Saves and settings carry over**: the frame-rate choice does not affect saves, settings or replays; the user-data location and identity are unchanged and no migration is needed after upgrading.
- **Payload integrity**: each rate has its own timing payload, bundled with the app and pinned by SHA-256 in the manifest. Every launch stages and verifies a session-local copy; files inside the app are never modified.

### Known issue

The Jet Tengu's transformation when landing while still moving is under investigation; stationary landings are unaffected. Progress is tracked in the independent [RA3EP1 FPS Unlock](https://github.com/Rakanrkk/RA3EP1-FPS-Unlock) project.

### Validation scope

Real-game testing was done on an Apple Silicon Mac with a 2× notched display: 60 FPS is the long-standing baseline and 120 FPS passed local visual acceptance. Other Macs, multi-display setups, display changes during play and the full campaign/challenge modes are not fully covered. Landscape only.

### Downloads

In the order shown on GitHub:

- **Runtime**: `RA3EP1Mac-v1.2.0-macos-arm64-runtime.dmg` — Wine runtime, prefix template, timing payloads and app-building helpers; no game files. Import your own game copy; build steps are in the [README](https://github.com/Rakanrkk/RA3EP1Mac/blob/v1.2.0/README.en.md).
- **Standalone**: `RA3EP1Mac-v1.2.0-macos-arm64-standalone.001.dmg` with `.002`–`.004.dmgpart` volumes — a ready-to-install app with runtime and game. Keep all four parts in one directory with unchanged names and open only the `.001.dmg`; macOS reads the remaining parts automatically, no manual joining.
- **Wine source**: `RA3EP1Mac-v1.2.0-wine-source.tar.zst` — Wine source archive matching the Runtime edition, with RA3Mac patches.
- `release-manifest.json` and `SHA256SUMS.txt`: the release manifest and SHA-256 checksums. Verify before opening the images.

### Components and licensing

The Git repository contains no game files. Third-party components and licenses are listed in the [README acknowledgements](https://github.com/Rakanrkk/RA3EP1Mac/blob/v1.2.0/README.en.md#open-source-acknowledgements) and [THIRD-PARTY-NOTICES.md](https://github.com/Rakanrkk/RA3EP1Mac/blob/v1.2.0/THIRD-PARTY-NOTICES.md). Original source is [MIT](https://github.com/Rakanrkk/RA3EP1Mac/blob/v1.2.0/LICENSE)-licensed; Wine-derived patches remain under LGPL scope. Release packages carry an ad-hoc integrity signature with no personal developer certificate; approve the first open under Privacy & Security.
