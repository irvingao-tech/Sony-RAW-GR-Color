# M0 验证记录

验证日期：2026-10-11（Asia/Shanghai）。

M0 的验收条件是 A7C II ARW 能打开、元数据可读、可导出中性 16 位 TIFF。此阶段已用用户提供的真实原片通过验收。中性在这里表示未施加风格、自动提亮或输出对比曲线；尚未用色卡证明相机色彩准确性。

## 环境

- Windows、本地 Python 3.12.14。
- rawpy 0.27.1 / LibRaw 0.22.1。
- NumPy 2.5.4、tifffile 2026.9.20、Pillow 12.3.0。
- 运行依赖固定在 `requirements.txt`；程序通过 `tools/run.py` 启动。

## 真实原片

- 路径：`C:\S2G\samples\DSC00944.ARW`。
- 大小：73,478,144 字节。
- 相机：SONY ILCE-7CM2。
- ISO 400、快门约 1/1250 秒、f/7.1、26 mm。
- 镜头：Viltrox 26mm F2.8 FE。
- 可见区域：7028 × 4688；原始传感器区域：7040 × 4688。
- 黑电平：[512, 512, 512, 512]；LibRaw white_level：16383。
- 拍摄白平衡乘数：[2499, 1024, 1673, 1024]。
- 源文件 SHA-256：`961dd7a2f7cf4912389176c2cba1d36e942f8430b5c01e30741b7e8bf3d54371`。

## 产物

- TIFF：`C:\S2G\output\DSC00944-neutral.tif`。
- 导出清单：`C:\S2G\output\DSC00944-neutral.tif.json`。
- 人工检查预览：`C:\S2G\output\DSC00944-neutral-preview.jpg`。
- TIFF 大小：197,684,512 字节；形状：(4688, 7028, 3)；类型：uint16。
- TIFF 嵌入 ICC：S2G Linear sRGB v1；Orientation 为 1，像素已遵循原片方向。
- TIFF SHA-256：`7fd0896b458df8c22e477decf3673dcc5a3c1ddc002fbdbb0b9d5976f9a6e640`。
- 饱和值像素比例：R 约 0.0633%、G 约 0.0108%、B 约 0.0134%。这些是最终 TIFF 的通道饱和比例，不代表传感器过曝比例。

已查看 1600 × 1067 的 sRGB 检查预览：画面、方向和颜色正常，建筑、天空、阴影和路面细节可辨。此人工检查不能代替色卡或 GR 配对图像评估。

## 验证结果

17 项测试全部通过，其中 14 项自动化基础测试和 3 项真实 ARW 验收测试。

- 元数据 CLI 成功输出真实相机、曝光和镜头信息。
- 两次独立完整解码与 TIFF 导出得到相同文件 SHA-256，验证当前依赖版本和 CPU 处理路径下的确定性。
- 完整导出后的 TIFF 是三通道 16 位整数 RGB，内嵌 ICC 可被 LittleCMS 读取。
- linear-sRGB ICC 转换至普通 sRGB 时，测试色块得到正确的非线性编码。
- 半尺寸真实解码输出 3514 × 2344。
- 导出前后原片 SHA-256 相同。
- 已有 TIFF 或清单不会被覆盖。
- 发布清单失败时，本次新 TIFF 会回滚；源文件变化时取消发布。
- JPEG 输入、缺失文件、错误相机和损坏 ARW 返回可读错误。

复现命令：

```powershell
cd C:\S2G
$env:PYTHONPATH = "$PWD\src"
$env:S2G_TEST_ARW = "$PWD\samples\DSC00944.ARW"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 下一阶段条件

当前只有一张真实 A7C II 自然场景原片；未验证所有 ARW 压缩模式、竖向照片或多种光照。M1 仍需要合法的 ColorChecker / 灰卡数据、不同光源与独立留出集。

M1/M2 应建立浮点场景线性核心和版本化相机配置，明确 XYZ 与 ACEScg 的白点适应。M0 的整数 linear-sRGB 导出用于验证解码通路，会截断超范围颜色，不能替代保留广色域与高光的工作缓冲。

镜头校正、色卡误差报告、可编辑 sidecar、GR 风格拟合、正式 JPEG / Display P3 TIFF 导出和桌面 UI 尚未实现。
