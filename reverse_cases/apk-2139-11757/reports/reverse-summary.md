# APK 初始逆向报告：2139_11757.apk

## 当前阶段 / Current phase

离线静态分诊完成。尚未执行 APK，尚未连接外部服务，尚未做动态调试或 IL2CPP 还原。

## 样本信息 / Artifact

| 字段 | 值 |
|---|---|
| 原始路径 | `C:\Users\BSTECH05\Downloads\2139_11757.apk` |
| 大小 | 665,640,210 bytes |
| SHA-256 | `38546423140d318d62a890c2c4c0d194773866739390b7e89b1199e8de2b1945` |
| 类型 | Android APK / ZIP 容器 |
| 包名 | `com.cahx.gw1` |
| 应用名 | `长安幻想` |
| versionCode / versionName | `300060` / `3.0.6` |
| minSdk / targetSdk | `21` / `30` |
| 主入口 | `com.shiyuegame.cahx.GameMainActivity` |
| ABI | `arm64-v8a`, `armeabi-v7a` |

## 已验证事实 / Verified facts

1. APK 是 Unity IL2CPP 应用，而不是主要依赖普通 Java/Kotlin 业务逻辑。
   - 存在 `lib/arm64-v8a/libil2cpp.so`、`lib/armeabi-v7a/libil2cpp.so`。
   - 存在 `assets/bin/Data/Managed/Metadata/global-metadata.dat`。
   - `GameMainActivity` 带有 `unityplayer.UnityActivity` 元数据。

2. APK 包含 5 个 DEX、64 个 native `.so`。
   - 最大 native 库是 `libil2cpp.so` 和 `libunity.so`。
   - Java 层主要覆盖登录、支付、更新、渠道 SDK、TapTap、腾讯、支付宝、运营商认证、OAID 等。

3. Manifest 权限面较宽。
   - 包含网络、外部存储、悬浮窗、相机、录音、定位、安装包请求、读取日志、电话、设备标识相关权限。
   - 包含 `MANAGE_EXTERNAL_STORAGE`、`READ_LOGS`、`READ_PRIVILEGED_PHONE_STATE` 等高敏感或普通应用通常不可用/受限权限声明。

4. 网络安全配置允许明文流量。
   - `res/xml/network_config.xml` 中 `cleartextTrafficPermitted=true`。
   - 配置和字符串中同时存在 HTTPS 正式域名、HTTP 正式/测试域名、本地和内网地址。

5. 发现多个导出组件和深链入口。
   - 主入口 `com.shiyuegame.cahx.GameMainActivity` exported=true，支持 `com.cahx.gw1://shiyue/page...`。
   - `com.cahx.gw1.wxapi.WXEntryActivity` exported=true，scheme 为 `com.cahx.gw1`。
   - `com.tencent.tauth.AuthActivity` exported=true，scheme 为 `tencent0`。
   - 支付、TapTap、腾讯相关 Activity 也有 exported=true 组件。

6. 存在一个 exported=true 的 FileProvider。
   - `androidx.core.content.FileProvider`
   - authority: `com.cahx.gw1.sy.update.fileprovider`
   - `grantUriPermissions=true`
   - 该点需要进一步核验 provider path 配置和调用路径，单凭 manifest 不能断言可利用。

7. 配置文件中存在硬编码 SDK key/secret 和疑似私钥材料。
   - `assets/shiyue_channel_config.properties` 含 LeLan AppId、Secret、公钥等。
   - `assets/shiyue_game_config.properties` 含 SHIYUE_APPKEY 和长公钥/配置材料。
   - `assets/u8_developer_config.properties` 含 U8 AppKey、HTTP SDK 地址、内网地址，以及一个名为 `PAY_PUBLICKEY` 但内容形态更像 RSA 私钥结构的长 Base64 字段。
   - 本报告不复制完整密钥值。

8. 热更新/资源配置存在，但当前热更新标记为空。
   - `assets/hotfix_manifest.json`: `build_type=Release`, `has_hotfix=false`, `hotfix_version=""`。
   - 资源/CDN 配置指向 `cahx-cdn.shiyuegame.com` 等域名。

## 关键证据 / Key evidence

| 证据 | 位置 |
|---|---|
| 基础哈希、体积、APK 类型 | `reverse_cases/apk-2139-11757/triage/2139_11757.apk.triage.md` |
| ZIP/Dex/native/Unity 结构 | `reverse_cases/apk-2139-11757/reports/zip-inventory.json` |
| 包名、版本、入口、权限、ABI | `reverse_cases/apk-2139-11757/reports/aapt-badging.txt` |
| Manifest 组件、exported、深链 | `reverse_cases/apk-2139-11757/reports/manifest-xmltree.txt` |
| 明文网络配置 | `reverse_cases/apk-2139-11757/reports/network-config-xmltree.txt` |
| FileProvider 路径配置 | `reverse_cases/apk-2139-11757/reports/file-paths-xmltree.txt` |
| DEX 域名/URL/关键词指标 | `reverse_cases/apk-2139-11757/reports/high-signal-indicators.json` |
| DEX 包结构概览 | `reverse_cases/apk-2139-11757/reports/dex-class-packages.json` |
| 本机工具审计 | `reverse_cases/apk-2139-11757/tools/android-tool-audit.md` |

## 推断与置信度 / Inference and confidence

| 推断 | 置信度 | 依据 |
|---|---:|---|
| 游戏主体逻辑位于 IL2CPP native 层，需要 `libil2cpp.so + global-metadata.dat` 还原 | 高 | Unity IL2CPP 标准结构完整存在 |
| Java 层主要承担渠道、登录、支付、更新、设备标识和第三方 SDK 接入 | 高 | DEX 包分布、Manifest 组件、配置文件一致 |
| 明文 HTTP 可能扩大中间人攻击或降级风险 | 中 | Manifest 允许 cleartext，配置和字符串中出现 HTTP 地址；尚未动态确认运行时实际调用 |
| 硬编码密钥/私钥材料是高优先级泄露风险 | 高 | 多个明文配置文件直接包含 secret/key 字段；其中支付字段形态异常敏感 |
| exported FileProvider 是潜在高风险点 | 中 | provider exported=true，但需要进一步解析路径资源、调用授权逻辑和 Android 版本行为 |
| `192.168.*`、`127.0.0.1`、test 域名可能是测试配置残留 | 中 | DEX/配置中出现内网、本地和 test-developer 域名；需动态验证是否可达/是否被使用 |

## 风险/漏洞候选 / Risk or vulnerability candidates

1. **硬编码敏感配置**
   - 风险：SDK Secret、AppKey、疑似支付私钥材料可被离线提取。
   - 建议：轮换相关密钥；客户端只保留非敏感公钥/标识；支付签名和敏感鉴权应迁到服务端。

2. **允许明文 HTTP**
   - 风险：若实际请求走 HTTP，可能被篡改或监听。
   - 建议：禁用 cleartext；将 `http://sdk.shiyuegame.com/`、`http://192.168.2.88` 等配置从发布包移除或强制 HTTPS。

3. **导出深链入口**
   - 风险：外部 App 可构造 intent 打开页面或触发登录/支付/回调流程。
   - 建议：对所有 deep link 参数做来源、签名、状态机校验；重点审计 `GameMainActivity`、微信、腾讯、支付宝/TapTap 回调入口。

4. **exported FileProvider**
   - 风险：若路径过宽或 grant 逻辑错误，可能造成文件暴露/越权访问。
   - 建议：将 provider 设为非导出；缩小 path；确认 update/file sharing 只通过显式授权 URI。

5. **高敏感权限声明**
   - 风险：合规、商店审核和用户隐私风险；部分权限在普通应用上无效但仍暴露意图。
   - 建议：移除未实际使用的权限；延迟请求运行时权限；补齐隐私说明和最小化声明。

6. **测试/内网配置残留**
   - 风险：测试接口或内网地址泄露，可能导致错误环境调用或信息暴露。
   - 建议：发布构建管线中加入配置扫描，阻断 `test-`, `192.168`, `127.0.0.1`、私钥模式等残留。

## 工具与限制 / Tooling notes

- 本机未检测到 jadx、apktool、Frida。
- 已使用 Unity 自带 Android SDK 中的 `aapt`、`apkanalyzer`、`dexdump` 辅助分析。
- `dexdump` 直接处理 APK 时在 Windows mmap 上失败，但将 DEX 抽出后可继续做结构级分析。
- 未执行 APK，未做动态抓包，未做 IL2CPP 方法还原，因此不能确认每个 URL/权限/组件在运行时一定被触发。

## 建议下一步 / Suggested next steps

1. **IL2CPP 深度还原**：用 Il2CppDumper/Cpp2IL 对 `libil2cpp.so` 与 `global-metadata.dat` 还原类名、方法、字符串引用和业务入口。
2. **Java 层反编译**：安装/使用 jadx，重点看登录、支付、更新、FileProvider、deep link、WebView、签名算法和网络请求构造。
3. **动态验证**：在隔离模拟器/测试机运行，抓取启动、登录、更新、支付前置流程的网络请求，不接触真实账号/支付。
4. **漏洞专项审计**：围绕硬编码密钥、明文 HTTP、exported 组件、FileProvider 做可达性和影响面确认。
5. **输出安全整改清单**：将当前风险转成开发团队可执行的整改项和发布阻断规则。
