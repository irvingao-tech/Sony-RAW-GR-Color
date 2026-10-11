# V0.3 色彩参考与改进记录

研究日期：2026-10-11。V2 为 S2G 自行编写、手工设定的实验参数，尚无同场景 Ricoh GR 对照数据，不能作为理光原厂算法或校准结果。

## 参考项目及取舍

| 项目 | 实际阅读与发现 | 采用方式 |
| --- | --- | --- |
| [Hncs](https://github.com/songjiun10-collab/Hncs) | `brands/ricoh_gr/look.py`、`core/engine.py`、`docs/brands.en.md`。GR 源码扩大到 GR/II/III/IIIx 样片群体，使用黑白位统计和 Lab L 曲线。它明确说明缺少 RAW 基线，肩部和 CLAHE 参数仍有未验证假设。旧方法文档仍提到较早的 34 张统计，不能混同当前源码。 | 借鉴亮度与色度分开处理、记录场景统计和证据范围。没有将不同场景总体统计强行当作 Sony→GR 的配对拟合目标，也未引入会随预览尺寸变化的 CLAHE。 |
| [Sony Mod](https://github.com/bonyback1/sony-pmca-ricoh-mod/blob/main/tools/generate_ricoh_hook.py) | 1024 点曲线、独立曝光偏移、定点 RGB 矩阵和硬件 WB 偏移。曲线优先寻找项目外的 `.cube` 文件，缺失时使用参数曲线；这里没有使用那些 LUT。矩阵前面的十进制注释与实际十六进制数组不一致，应以数组为准。硬件 ISP 所处颜色域不能直接等同 ACEScg。 | 借鉴分层结构。自行设定保守的线性 sRGB 风格矩阵，再变换到 AP1 并约束亮度与中性轴。曝光与曲线分开，避免重复补偿；不机械移植旧机型的硬件 WB 数值。 |
| [RawTherapee GR III DCP](https://github.com/RawTherapee/RawTherapee/blob/dev/rtdata/dcpprofiles/RICOH%20GR%20III.dcp) | 下载并检查 DCP 标签：相机型号 RICOH GR III，双光源标识 17/21，两个 ColorMatrix、90×30×1 HueSatMap、8192 点色调曲线。 | 作为相机基础显影结构参考。仅在 research 保存研究副本，没有加载进 Sony 渲染管线。它不是 Positive/Negative Film 的完整参数。 |
| [ricoh-gr3-android](https://github.com/Nielk74/ricoh-gr3-android/blob/main/ROADMAP.md) | 当前 roadmap 除相机协议和 effect 枚举，还已描述应用内显影、肤色保护与亮度中性分离色调，范围已超出用户提供的较早简介。 | 作为补充架构参考，没有移植其实现或把 effect 名称当作颜色算法。 |

Hncs 树版本：`5f1dd7e8f4fa64b16032842e4be50208c16a4140`；Ricoh 模块按该版本下载。Sony Mod 查询到的版本：`3dc77764efbbb272d0774b80b4dcacf4bd4f18bd`。研究快照的来源与文件 SHA-256 记录在 [source-provenance.json](source-provenance.json)。第三方下载副本保留在本机 `research`，不随本项目上传。main/dev 分支链接可能继续变化，以记录的快照哈希为本次研究依据。

## V2 处理顺序

原始相机 RGB → 原有 Sony 中性校正 / 白平衡 → AP1 场景线性 RGB → 独立曝光 → 保中性轴和亮度的矩阵 → log2 亮度的单调三次曲线 → 用户高光 / 阴影 → SDR 肩部 → 初次色域压缩 → Oklab 色度 / 色相分区与阴影高光分离 → 最终色域压缩 → sRGB 编码 → 编码空间的轻微黑位抬升 → 原有确定性颗粒。

- 曲线预计算为 1024 点浮点表；超出曲线端点采用单位斜率延伸，保留极端亮度连续性。此点数只是实现选择，不能证明 GR 精度。
- 风格矩阵参数明确标为线性 sRGB；转换到 AP1 后再投影，使灰阶保持中性、矩阵不额外改变亮度。
- 蓝、绿、红分区使用连续色相权重，减少硬阈值边界。Oklab L 保持不变，但最终色域压缩仍可能改变明度／色度。
- 肤色保护是宽暖色区的启发式权重，没有人脸识别。它减弱色相、色度和分离色调的调整，不能保证所有肤色、光源与曝光都准确。
- 近中性色与极暗／极亮区域减弱分离色调。负片黑位从 V1 的线性空间常量改为编码空间的小幅阴影修饰，避免全图蒙灰。
- 风格强度为 0 时直接使用原中性图，连同曝光、高光和阴影设置逐像素一致。

所有 V2 风格参数都在 `src/s2g/resources/looks_v2.json`，`calibrated=false`。Oklab 公式采用 [Björn Ottosson 的公开实现](https://bottosson.github.io/posts/oklab/)，作者明确提供 public-domain 使用许可。

## 兼容性与验证

V1 参数文件和渲染实现保持原样；旧 sidecar 的 0.2.0 渲染身份继续有效。V2 使用独立风格 ID、参数哈希和 0.3.0 渲染身份。应用版本与渲染版本分别记录。保存照片参数时只会保存用户实际选择的版本，不自动迁移旧风格；导出的 V2 文件名带 `-v2`，避免与旧版默认文件名冲突。

两张用户原片生成了中性、正片 V1/V2、负片 V1/V2，共 10 张 1600 像素 JPEG。它们使用相同拍摄白平衡、82% 风格强度、零曝光调整和零颗粒。`output/color-v03/report.json` 记录原片与输出哈希、版本和 Oklab 明度分位数／平均色度。统计不能用作 GR 相似度评分。

50 项自动测试通过（完整 49 项运行，加新增对比页 HTTP 测试所在的 6 项服务测试运行），含真实 DSC00944.ARW：V1/V2 缓存预览与同尺寸完整数据渲染一致；V2 16 位 TIFF 重复导出哈希一致；8/16 位差异在量化误差范围内。灰阶单调、矩阵保亮度、肤色保护权重、极端亮度和旧 sidecar 兼容性均有覆盖。此前生成的正片／负片 V1 JPEG 与本次重渲染文件 SHA-256 相同。浏览器检查发现对比页内联样式被既有 CSP 拦截，已改为本地独立 CSS/JS，并实地确认布局与负片 V2 TIFF 导出完成。

目前证据支持结构稳定性、旧版兼容性和负片黑位修正，不能证明 GR 还原准确。下一步校准需曝光和白平衡可控的 Sony/GR 同场景样片，分别标记 GR 型号、Positive/Negative 设置与光源；先做配准／色卡对应，再按训练、验证场景分开拟合。

全尺寸正片 V2 导出另行确认：`output/v03-validation/DSC00944-positive-v2-full.tif` 为 7028×4688、RGB uint16、带 sRGB ICC，输出 SHA-256 与清单一致。浏览器负片 V2 导出位于 `output/v03-ui-validation/DSC00944-negative-v2.tif`。两张 ARW 的哈希均与对比记录一致；DSC00944 原有正片 V1 sidecar 未修改。负片新旧页面截图保存在 `output/v03-negative-comparison.jpg`。
