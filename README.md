# phigros_auto

面向 **Phigros（Android）** 的自动打歌工具。

> **当前状态：已在真机上跑通，`Glaciaxion [EZ]` 拿到全 Perfect**
> - 谱面：从自己的安装包里提取了 **686 份官方谱面**（217 首歌，43.5 万个音符），解析器已适配真实格式；
> - 注入：**免 root 的 scrcpy 通道**，真机 Xiaomi 24031PN0DC / Android 16 验证；
> - 校准：每首歌第一次用方向键微调，结果自动记进 `calibration.json`。
>

 
 ## 免责声明
+ 本项目属于个人兴趣项目，与厦门鸽游网络有限公司无关。
+ **本项目内不含任何版权素材，且本项目并非商业项目**。
+ 项目的服务端借用自[Genymobile/scrcpy](https://github.com/Genymobile/scrcpy)，在此感谢scrcpy的作者和维护者们。
+ 截止目前，项目作者从未在任何除GitHub以外的平台上以任何方式宣传过本项目。


0. 准备
1. **请安装Python 3.11**
2.  使用`pip install -r requirements.txt`安装依赖
3. 请安装`Android Debug Bridge`，**要求版本号至少为`1.0.41`**，并确保相应的环境变量已经配置好。

## 成果

| 日期 | 曲目 | 结果 | 校准 |
|---|---|---|---|
| 2026-10-27 | Glaciaxion [hd] | **全 Perfect**（66 notes，0 Good / 0 Bad / 0 Miss） | `-0.56s` |
| 2026-10-28 | Distorted Fate [IN] | **全 Perfect**（1449 触控，峰值 4 指） | `-2.56s` |

## 快速使用（`releases/`）

不想敲命令的话，`releases/` 里有两个入口：

| 文件 | 作用 |
|---|---|
| `1-导入谱面.bat` | 检查环境 → 连手机 → 拉取 APK → 解出 assets → 解码谱面到 `charts\` |
| `2-自动打歌.bat` | adb 截屏 → OCR 识别当前曲目和难度 → 启动自动打歌 |

两者都支持 `-DryRun`（只检查环境/只识别，不真正执行）：

```powershell
releases\1-导入谱面.bat -DryRun
releases\2-自动打歌.bat -DryRun
---

## 原理分析

```powershell
cd ".....\phigros auto"##文件目录

# ① 提谱面（从你自己解包出来的 assets 里读，一次就好）
python run.py tracks list    apk\assets\base\assets\aa
python run.py tracks extract apk\assets\base\assets\aa --out charts

# ② 看谱面、生成时间轴
python run.py info "charts\Stasis.Maozon.IN.json"
python run.py plan "charts\Stasis.Maozon.IN.json" --out out\plan.json --csv out\plan.csv

# ③ 真机试打：默认模式是「你点开始，音乐响起按回车」
python run.py play "charts\Stasis.Maozon.EZ.json" --backend scrcpy --countdown 5 --speed 0.5
```

`run.py` 在任何目录下都能用：

```powershell
python "D:\dsh\phigros auto\run.py" scrcpy-test
```

依赖：核心命令只用标准库；`preview` 需要 Pillow，`sync` 需要 numpy，`tracks` 需要 UnityPy
（`python -m pip install UnityPy`）。本机沙箱里 pip 装不了，我把 wheel 解压到了 `out/vendor/`，
代码会自动找到它。

## 已实现并验证

| 模块 | 文件 | 说明 |
|---|---|---|
| 谱面解析 | `phigros_auto/chart.py` | 官方格式（1/32 拍 + x∈±7）与 RPE 格式（拍 + x∈0~1）都支持，自动判断 |
| 判定线数学 | 同上 | 事件插值、`floorPosition` 反推、旋转后音符沿线偏移 |
| 触控时间轴 | `phigros_auto/schedule.py` | 音符 → 屏幕像素；Hold/Flick 按 1ms 采样（与 phisap 一致）；10 指复用 |
| **谱面提取** | `phigros_auto/tracks.py` | 解析 Addressables `catalog.json` 的二进制索引，把谱面从 bundle 里挖出来 |
| **scrcpy 注入** | `phigros_auto/scrcpy.py` | 免 root；push/forward/启服务端/握手/32 字节报文全流程 |
| 音频对齐 | `phigros_auto/sync.py` | 能量包络 + FFT 互相关 |
| 预览渲染 | `phigros_auto/render.py` | PNG 对比图 / GIF |
| 调度执行 | `phigros_auto/play.py` | 事件流派发 + 忙等收尾，支持播放中 ↑/↓ 实时微调 |
| CLI | `phigros_auto/cli.py` | `info` `plan` `preview` `play` `tracks` `sync` `scrcpy-test` |

```powershell
python -m unittest discover -s tests -v   # 42 个测试
```

## 谱面格式（这部分是踩坑换来的）

官方谱面和我最初按 RPE 编辑器格式写的解析器**不一样**，两个关键差异：

* **`time` 的单位是 1/32 拍**，不是拍。验证方法：`holdTime` 全是 8/16/32/64 的倍数
  （即 0.25/0.5/1/2 拍），首音符时刻的中位数正好 3.00s；按 1/32 拍换算，`Stasis` 时长 150.7s，
  和 2:30 的曲子吻合。
* **`positionX` 以 0 为中心，±7 到屏幕两侧**（不是 0~1、0.5 为中心）。验证方法：43 万个音符的
  |x| 分布在 7.0 处断崖（6.0 有 2 万，7.0 剩 1.3 千，7.5 只剩 53）。

解析器会各自自动判断，`--note-span` 可以在真机上微调（见下）。

## Random 模式（blockAreaList）

部分 AT 谱面（如 `Ametrine [AT]`）带 `blockAreaList`，游戏里开启 **Random** 时，
这些区域内的音符会被随机打乱位置。脚本无法预测打乱结果（种子在游戏内），所以：

* `chart.block_areas` 会解析出来，`play` 启动时打印警告；
* 不开 Random 时，区域对音符位置没有影响，可以正常打。

phisap 同样没有处理这个字段，属于游戏侧的随机机制，不是解析问题。

## 提取谱面是怎么做的

`apk/assets/base/assets/aa/catalog.json` 里三块 base64 二进制：

| 字段 | 结构 |
|---|---|
| `m_KeyDataString` | `int32 数量`，每个 key：`byte 类型(0=字符串) + int32 长度 + UTF-8` |
| `m_BucketDataString` | `int32 桶数`，每桶：`int32 key偏移, int32 n, int32[n] 条目下标` |
| `m_EntryDataString` | `int32 条目数`，每条 **28 字节**，第 3 个 int 是 internalId |

key 形如 `Assets/Tracks/<歌名>.<曲师>.0/Chart_IN.json`，internalId 指向 `m_InternalIds` 里的
`.../Android/<hash>.bundle`，谱面就是那个 bundle 里的 `Chart_IN` TextAsset（明文 JSON，没有加密）。

## 「开始」这一步

自动打歌的时间轴是相对于**歌曲第 0 秒**的，而游戏里点「开始」之后还有加载、音乐才响，
所以 `play` 必须知道 t=0 在哪。三种模式：

| 模式 | 用法 | 精度 |
|---|---|---|
| `enter`（默认） | 倒计时后，你在手机上点「开始」，**音乐响起那一刻按回车** | 人的反应 ~150ms |
| `tap` | 自己点「开始」按钮：`--start tap --start-tap 2050,950 --start-delay 1.5` | 加载时间是稳定的，校准后 ~30ms |
| `immediate` | 立刻开始，谱面已经在跑时用 | — |

「开始」按钮的坐标怎么找：先 `adb exec-out screencap -p > shot.png` 截个图，看图上按钮中心的像素位置。

**校准顺序**（从粗到细）：

1. `--speed 0.5` 跑，看落点偏内/偏外 → 调 `--note-span`；
2. 看成排的判定是「早」还是「晚」 → 调 `--start-delay`（tap 模式）或 `--offset`；
3. 每次只改一个量，改 0.02~0.05 秒一级。

## scrcpy 通道

按 scrcpy v5.0.1 源码实现（v2.4 与 5.0.1 协议一致，逐行对比过）：

* 服务端以 shell 身份跑 `app_process`，第一参数必须等于服务端版本号（自动识别）；
* 关掉视频/音频，只留一个 control socket，`adb forward tcp:27183 localabstract:scrcpy`；
* 连上先收 1 字节握手 —— **注意 `adb forward` 会先接受连接再转发**，服务端没起来时 recv 会立刻
  返回空，所以要一直重试到收到字节（这个坑已经修了）；
* 触控报文 32 字节大端：`type | action | i64 pointerId | x | y | w | h | pressure | actionButton | buttons`；
* `w/h` 必须和服务端认为的显示尺寸完全一致，否则事件被**静默丢弃**。横屏时是 `2400x1080`。

自检：

```powershell
python run.py scrcpy-test          # 倒计时 3 秒后点 5 个位置
```

> MIUI/HyperOS 上 `show_touches` 改完不会立刻画白点，所以「没看到圆点」不代表通道没通。
> 用 `adb shell dumpsys input` 看 `TouchStatesByDisplay` 里有没有 `touchingPointers=[Pointer(id=0, FINGER)]`
> 更可靠。

## 校准（每次开始按一下键）

触发方式：程序点「开始」后，**在第一个音符压到判定线的一瞬间按回车或空格**。

按键走全局检测，不依赖终端焦点，也不会和 adb 抢输入。偏了就用方向键现场调
（**任何模式都可用，默认开启**，`--no-nudge` 关闭）：

| 按键 | 步长 |
|---|---|
| PageUp / PageDown | 200ms |
| ↑ / ↓ | 20ms |
| q | 停止 |

调好的偏移会写进 `calibration.json`（键是谱面文件名），下次作为起点：

```json
{ "DistortedFate.Sakuzyo.IN": { "shift": -0.12 } }
```

* `--delay -0.3` 临时覆盖本次偏移
* `--no-save` 不写回文件
* 不给 `--start-tap` 时，程序不点开始，由你自己点

## 其他参数

| 参数 | 含义 | 说明 |
|---|---|---|
| `--screen` | 设备分辨率 | 默认自动；横屏是 `2400x1080` |
| `--note-unit` | 音符 x 换算 | 1 单位 = 多少像素（720 高基准，默认 72） |
| `--x-shift / --y-shift` | 整体平移 | 只在落点整体偏了才用 |
| `--start-tap` | 「开始」按钮坐标 | 2400x1080 横屏实测 `2280,940` |
| `--audio-sync` | 用 scrcpy 收音对齐 | 会让手机卡顿，不推荐 |


```

识别原理：

* **曲名** —— 用 Windows 自带的离线 OCR（`Windows.Media.Ocr`）读标题文字，
  再和 `charts/` 里的谱面名做模糊匹配（阈值 0.75）；
* **难度** —— 不看字母，看**难度标签的底色色相**：EZ 绿 / HD 蓝 / IN 红 / AT 紫。
  红底白字的 IN 经常 OCR 不出来，颜色反而稳定。

## 风险

* Phigros 是单机游戏但成绩会云同步，用脚本刷成绩属于作弊，建议只在离线/自娱场景使用；
* 免 root 注入走的是系统输入通道，理论上无法与真实手指区分，但仍请自行权衡；
* 本仓库不含游戏资源，`charts/`、`apk/` 都是你自己从安装包里提取的，请勿分发。

## 目录
phigros_auto/    核心包（chart / schedule / sync / render / play / scrcpy / tracks / cli）
tools/           演示谱面、调度抖动测试、直接解包（scan/pull/unpack）、依赖引导
tests/           38 个单元测试
charts/          从你自己安装包提取的 686 份谱面（+ manifest.json）
apk/             解包出来的 assets（约 2.6 GB，可以删）
out/             预览图、时间轴、vender 依赖
run.py / run.ps1 启动器（任意目录可用）
```
