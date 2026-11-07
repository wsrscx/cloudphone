# 云手机远控

这是一个只控制本机 ADB 所连接 Android 设备的云手机项目。项目包含 Python 服务端和本地 Web 管理页、Android 客户端、Remote IME 输入助手，以及上游 scrcpy-server v4.1 采集端。视频和控制分别使用独立的 TLS WebSocket，视频直接传输 H.264 Annex-B，不经过 WebRTC、RTP 或设备常驻代理。

服务端 API、视频和控制共用配置的 HTTPS 端口；管理页只监听 `127.0.0.1:8080`。

## 目录结构

| 路径 | 内容 |
| --- | --- |
| `server/` | Python 服务端、管理页静态资源和单元测试 |
| `android/client/` | Android 控制端 |
| `android/remote-ime/` | 部署到被控设备的 Remote IME |
| `android/protocol/` | 控制协议模块 |
| `protocol/` | Protobuf 定义 |
| `third_party/scrcpy/` | 上游 scrcpy v4.1 固定源码归档、构建补丁和许可证 |
| `scripts/` | 环境准备、配置、构建和启动脚本 |
| `docs/` | 使用说明 |

## 环境要求

- Windows 10/11
- Python 3.11 或更高版本
- JDK 17–21（需要 `jlink` 和 `keytool`）
- Android SDK 34（需要 `platforms/android-34` 和 `platform-tools`）

`bootstrap.ps1` 会按需查找本机环境，也可以显式传入路径：

```powershell
Set-Location '<项目目录>'
.\scripts\bootstrap.ps1 -PythonExe '<python.exe>' -JavaHomePath '<JDK目录>' -AndroidSdkPath '<Android SDK目录>'
```

脚本不会安装或修改系统 Python、PATH 或代理，只在项目内创建 `.venv` 和 `android/local.properties`。

## 快速开始

```powershell
Set-Location '<项目目录>'

# 1. 生成本地证书、客户端 PSK 和管理密码
.\scripts\configure.ps1 -PublicIp '<公网IP或127.0.0.1>' -PublicPort 8443

# 2. 构建服务端依赖和两个 APK
.\scripts\build.ps1

# 3. 启动服务端
.\scripts\run-server.ps1
```

也可以双击根目录的 `启动云手机服务.bat` 启动。管理页地址为 `http://127.0.0.1:8080`，使用配置时设置的管理密码登录。

构建完成后，`dist/` 中会生成：

- `cloudphone-client.apk`：安装到 Android 控制端，Android 8.0 或更高。
- `cloudphone-remote-ime.apk`：服务端自动部署到被控手机，不需要手工安装。
- `scrcpy-server-direct-v4.1`：服务端通过 ADB 自动推送到被控手机。
- `scrcpy-Apache-2.0-LICENSE.txt`：scrcpy Apache 2.0 许可证。

只需手工安装 `cloudphone-client.apk`。使用 ADB 安装时请明确指定控制端序列号：

```powershell
adb -s '<控制端序列号>' install -r '.\dist\cloudphone-client.apk'
```

公网部署、端口转发、输入法行为和限制见 [docs/使用说明.md](docs/使用说明.md)。

## 安全说明

- `data/` 保存证书私钥、客户端 PSK、管理密码哈希和数据库；`android/signing.properties` 与 `android/client-secrets.properties` 保存签名口令和客户端连接信息。这些文件均已被 `.gitignore` 排除，请勿提交或分享。
- 只向公网开放 HTTPS/WSS TCP 端口（默认 `8443`）。不要开放管理页 `8080`、ADB `5037/5555` 或其他本地调试端口。
- 如果凭据可能泄露，立即运行 `.\scripts\configure.ps1 -Force` 轮换证书、PSK 和管理密码，然后重新构建并安装客户端。

## 验证

- Python 服务端：单元测试覆盖鉴权、防重放、设备授权、IME 恢复、可解码 GOP 队列、scrcpy 帧解析和 Protobuf 边界。
- Android 客户端：JVM 单元测试覆盖双 WebSocket 会话、解码队列、重连退避和坐标映射。
- Remote IME：3 个 JVM 单元测试。
- 上游 scrcpy-server：Java 测试、Release 构建和 lint 通过。
- 两个 APK：Release 签名验证通过，使用 APK Signature Scheme v2。

## 许可证

本项目使用 [MIT License](LICENSE)。`third_party/scrcpy` 为上游 [Genymobile/scrcpy](https://github.com/Genymobile/scrcpy) v4.1 的固定源码和构建补丁，使用 Apache-2.0 许可证，详见 `third_party/scrcpy/LICENSE` 与 `server/THIRD_PARTY_NOTICES.md`。
