# S2G Sony RAW to GR

本地 Sony A7C II RAW 编辑器，当前版本 0.4.0。已支持照片库、实时预览、中性 / 正片 / 负片风格、曝光与白平衡、高光与阴影、饱和度与自然饱和度、独立冷暖分离、颗粒、裁切旋转、JSON 编辑参数、预设和可取消的批量 JPEG / 16 位 TIFF 导出。

正片、负片是独立的实验参数，尚未经过理光同场景参考数据拟合。相机配置也尚未完成双光源色卡校准。

0.4 新增“色彩与分离色调”：饱和度整体增减颜色，-100 得到黑白；自然饱和度优先调整较淡的颜色并减弱暖色肤色区变化；冷暖分离 100% 保持风格原设定、0% 关闭、200% 加强。新色彩调整使用 Oklab，并通过减少色度使超出 sRGB 色域的颜色保持色相与感知明度。三项均可保存、应用预设和同步批量导出，“重置色彩”只恢复这三项。新控制处于默认值时，旧版参数格式与渲染身份保持不变。详见 [0.4 验证记录](docs/v04-validation.md)。

0.3 新增正片 / 负片 V2：1024 点平滑单调曲线、保亮度与中性轴的颜色矩阵、Oklab 色相分区、肤色 / 中性色保护和冷暖分离。负片改用小幅编码空间抬黑，减少蒙灰。旧版风格仍在菜单内，已保存的旧参数保持原渲染结果。研究来源、具体取舍和验证边界见 [色彩研究记录](docs/color-research-v03.md)。

本机服务运行时可打开 [两张样片的 V1 / V2 对比](http://127.0.0.1:8876/comparison)。新照片对比可在空输出目录下运行：

```powershell
& .venv/Scripts/python.exe tools/compare_looks.py samples/DSC00944.ARW samples/DSC00914.ARW
```

默认写入 `output/color-v03`；已有结果时拒绝覆盖。自定义 `--output` 可保存另一次研究记录，本机对比页目前固定展示默认目录。

## 打开编辑器

双击项目目录里的 `Start-S2G.cmd`，会启动本机服务并打开浏览器。当前已运行时也可直接访问 `http://127.0.0.1:8876`。终端窗口需保持开启；关闭窗口或按 Ctrl+C 会停止服务。

1. 在照片库中选照片；也可通过“导入照片”填写文件 / 文件夹完整路径。当前原型仅访问项目目录 `C:\S2G` 内的文件，将原片放到 `samples` 最方便。
2. 选择中性、正片或负片，调整滑块。按住“查看原图”比较，点击旋转或选择居中裁切比例。
   颜色偏淡时可先尝试自然饱和度 +20～+35，再按需要调整饱和度；冷暖分离可以单独调节。中性风格或风格强度为 0 时，冷暖分离滑块禁用。
3. 点击“保存参数”，编辑会写入原片旁的 `*.ARW.s2g.json`，原始 ARW 不变。切换照片时会读取已保存参数。
4. 保存命名预设，或者勾选多张照片后同步调整。应用预设时默认保留当前曝光与白平衡，可取消锁定。
5. 选择 JPEG / 16 位 TIFF、尺寸和导出目录。勾选照片时批量导出；否则导出当前照片。文件重名会报错并继续下一张，选择新的目录即可重新导出。

默认导出目录：`C:\S2G\output\editor`。每个导出文件附带同名 JSON 清单，包含源文件哈希、编辑参数、相机与风格参数哈希、处理版本和输出哈希。照片库 / 预设 / 任务记录存放在 `.s2g/catalog.sqlite3`；可移植的照片编辑参数保留在独立 sidecar 中。

界面是完全本地的浏览器原型，不需要联网、不使用外部字体或 CDN。它和 CLI 通过同一 Python 渲染核心处理像素；规范推荐的 Tauri / React / Rust 外壳和 Windows 安装包尚未实现。

## 0.2 色彩流程

LibRaw 解码与黑电平处理 → 未缩放相机 RGB → 浮点拍摄白平衡 / 色温调整 → 版本化矩阵 → XYZ D65 → Bradford 白点适应 → ACEScg AP1 → 曝光与风格 → 自定义 SDR 高光肩部与色域压缩 → sRGB 编码。

场景线性阶段保留负值与大于 1 的值，不先裁成整数 sRGB。高光滑块调节保留下来的层次，不能恢复传感器已经剪切的颜色。色温使用 Planckian 近似，尚无相机实测 Kelvin 校准。输出是标准非线性 sRGB、带 ICC 的 JPEG 或 16 位 TIFF；目前不支持 Display P3。

预览使用完整 RAW 解码后的 1400 像素缓存，再调用同一处理图。相同几何与分辨率下，真实样本的缓存预览和完整数据渲染逐像素一致。首次读取较慢，后续滑块无需重复解码。全尺寸与缩小结果的细节 / 颗粒会随分辨率变化。

配置 / 风格 JSON 在 `src/s2g/resources` 下。sidecar 会校验原片、渲染版本和参数文件哈希；V1 与 V2 分开保存身份，旧版编辑无需迁移。其他未知版本或哈希仍明确要求迁移，避免静默改变旧编辑。

## 首次安装与命令行

首次安装需要网络。使用 Python 3.12 或更高版本，在 PowerShell 中运行：

```powershell
git clone https://github.com/irvingao-tech/Sony-RAW-GR-Color.git C:\S2G
cd C:\S2G
.\setup.ps1
New-Item -ItemType Directory -Force samples
```

已有项目目录时跳过 `git clone`。也可克隆到其他目录；服务以启动时的项目目录为文件访问范围。仓库包含源码、开发规范、研究与验证记录，不包含 RAW 样片、导出图片、照片库或下载的第三方研究副本。将自己的 Sony A7C II ARW 放入 `samples` 后打开编辑器。

已安装后，处理完全离线。无需激活虚拟环境：

```powershell
.\.venv\Scripts\python.exe tools\run.py doctor
.\.venv\Scripts\python.exe tools\run.py inspect samples\DSC00944.ARW
.\.venv\Scripts\python.exe tools\run.py render samples\DSC00944.ARW --output output\DSC00944-neutral.tif
.\.venv\Scripts\python.exe tools\run.py serve --open
.\.venv\Scripts\python.exe tools\run.py develop samples\DSC00944.ARW --look gr_positive_experimental_v1 --output output\positive.jpg --max-size 1600
.\.venv\Scripts\python.exe tools\run.py develop samples\DSC00944.ARW --look gr_negative_experimental_v1 --output output\negative.tif
```

旧 `render` 命令的 `--half-size` 输出宽高减半的 M0 解码；编辑器预览不使用这个模式。完整导出可能需要数百 MB 内存。输出重名时会报错，请使用新文件名。

旧 `render` 的线性 TIFF 与其 `.tif.json` 清单仍可用于 M0 排查。正式编辑通过 `develop` 或界面导出；可编辑 sidecar 与导出清单是不同文件。原片保持只读，导出时在解码后和发布前检查源文件哈希。

## M0 色彩行为

本节仅说明保留的旧 `render` 命令；0.2 编辑器的浮点流程与导出编码见上文。

- 只接受扩展名为 ARW、相机元数据为 SONY ILCE-7CM2 的文件。
- 使用 LibRaw AHD 解马赛克、拍摄白平衡、黑白电平归一化和内置相机矩阵。
- 关闭自动白平衡、自动提亮和基于画面内容的白电平调整；不读取内嵌 JPEG 来代替 RAW 显影。
- 输出 sRGB 原色、线性传递函数的 16 位 TIFF，嵌入专用 linear-sRGB ICC。它不同于普通 sRGB JPEG 的非线性编码。
- 应在支持 ICC 色彩管理的应用中查看。忽略 ICC 的应用可能把线性数据显示得过暗。
- M0 的整数 linear-sRGB 会截断超出范围的颜色和高光；不能作为后续保留高光与广色域的场景线性核心。M1/M2 需要独立的浮点相机 RGB → XYZ → ACEScg 处理，明确白点适应，并用色卡校准。
- 不宣称已完成中性色卡精度验证、镜头校正或理光色彩匹配。

TIFF 和清单分别用同卷临时文件与硬链接发布，已有文件不会被覆盖。第二个文件发布失败时回滚本次 TIFF。两文件并非跨文件事务；断电可能留下只含 TIFF 的不完整导出。目的盘须支持硬链接（Windows 本地 NTFS 支持）；不支持时返回错误。

## 测试

```powershell
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

自动化测试覆盖矩阵与白点、浮点高光范围、单调曲线、曝光、种子颗粒、裁切旋转、sidecar 校验、JPEG / TIFF ICC、输出确定性、原片保护、取消、批量错误恢复和目录持久化。M0 记录在 `docs/m0-validation.md`；当前记录在 `docs/v02-validation.md`。

加上真实文件验收（文件不提交到代码库）：

```powershell
$env:S2G_TEST_ARW = "$PWD\samples\DSC00944.ARW"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

生成用于人工检查的 sRGB 预览（此工具暂不属于正式 JPEG 导出功能）：

```powershell
.\.venv\Scripts\python.exe tools\preview_tiff.py output\DSC00944-neutral.tif output\DSC00944-neutral-preview.jpg
```

## 下一步

继续完善编辑器与 Windows 打包。M1 的色卡误差报告需要多光源 ColorChecker / 灰卡原片；M3/M4 的正片与负片拟合需要合法的索尼 / 理光配对参考。当前浮点核心、sidecar、导出与本地界面已实现原型，但尚不满足 M6 的校准、稳定性和安装包验收。

参考 API：

- [rawpy 参数](https://letmaik.github.io/rawpy/api/rawpy.Params.html)
- [rawpy 元数据接口](https://letmaik.github.io/rawpy/api/rawpy.RawPy.html)
- [LibRaw 支持的相机](https://www.libraw.org/supported-cameras)
- [TIFF 读写库](https://github.com/cgohlke/tifffile)
- [ICC 配置规范](https://www.color.org/icc_specs2/)
