# 声场 · TTS Lab

在 Apple Silicon Mac 上研究 **IndexTTS 2、IndexTTS 2.5 和 BreezeTTS2** 的本地配音与声音克隆。中文网页包含三个模型的独立入口、同条件对照、多语气对照、音色库、多角色剧本与外部音频导入。

## 开始使用

双击 `启动语音实验室.command`，打开 <http://127.0.0.1:8765>。

| 页面 | 地址 |
| --- | --- |
| IndexTTS 2 | <http://127.0.0.1:8765/studio/index2> |
| IndexTTS 2.5 | <http://127.0.0.1:8765/studio/index25> |
| BreezeTTS2 | <http://127.0.0.1:8765/studio/breeze2> |
| 对比试听 | <http://127.0.0.1:8765/compare> |
| API 文档 | <http://127.0.0.1:8765/api/docs> |

1. 选择演示音色，或上传 2–30 秒人声，建议 5–15 秒清晰、单人、少背景声的录音。克隆真人时使用本人或已获授权的录音。
2. 输入文本，选择模型及情绪。Breeze 无参考时可用文字设计音色；有参考时可用导演指令调整演绎。Breeze 克隆需要准确逐字稿。
3. 勾选「同时提交三个模型」，得到同文案、同参考、同种子的对照组。点击「对比多种语气」会生成自然、开心、难过、生气、平静五种版本。
4. 在「对比试听」中切换原始/统一响度、隐藏模型、随机顺序、连续播放，按自然度、清晰度、音色相似度、情绪符合度评分。
5. 下载 WAV、MP3 或整组 ZIP；剧本另有整段 WAV 与按片段时长对齐的 SRT。

默认只监听本机，不使用云 GPU 或收费语音 API。模型下载需要联网；生成进程设置离线模式。音频、参考录音、运行日志和权重保存在本机，并被 Git 忽略。

## 环境与恢复

本次目标机器为 M3 Max / 64 GB。工作台使用 Python 3.11，Index MLX 与 Breeze 使用独立环境；Breeze 使用 Python 3.12。上游代码与依赖锁见 `config/upstreams.json` 和 `config/locks/`；权重仓库与不可变 revision 见 `config/engines.json`。

```bash
brew install uv git ffmpeg sox
uv run --frozen python scripts/setup.py --download
uv run --frozen python scripts/doctor.py --hash
uv run --frozen pytest -q
uv run --frozen python scripts/launch.py --no-open
```

弱网时重新运行相应下载器，会继续已有 `.partial` 与分块文件，完成后校验大小和 SHA-256。请勿同时运行两个相同模型的下载器。

```bash
uv run --frozen python scripts/download_models.py index2
uv run --frozen python scripts/download_models.py index25
uv run --frozen python scripts/download_models.py breeze2
uv run --frozen python scripts/download_auxiliary.py
```

Breeze 大型依赖包反复中断时，可在已创建的 Breeze 环境上运行 `uv run --frozen python scripts/repair_breeze_install.py`。它按锁文件续传 wheel，校验哈希后安装，不改第三方依赖声明。

停止：双击 `停止语音实验室.command`。未完成任务在重启后标记中断，已经完成的音频仍可试听和导出。

## 研究方法与范围

- 同一对照组固定文本、参考文件 SHA-256、语言、随机种子、后期语速；参数、适配代码 commit、权重 revision 与日志随结果保存。不同模型的相同种子不会产生相同随机采样过程。
- 统一响度采用 FFmpeg 两遍 loudnorm，目标 -16 LUFS / -1.5 dBTP；原始文件同时保留。主观比较用同一耳机和音量。
- RTF 为模型进程总耗时除以音频时长，含启动、导入、模型加载与参考编码，排队和 FFmpeg 后处理不计入；这是冷启动工作流数据。变速时以变速后时长为分母，性能对比应使用 1.0×。不能与厂商的首包延迟或常驻服务吞吐直接比较。
- 只记录真人实际评分。演示音频出声、无静音和可解码不等于发音完全正确；不能据此宣布模型质量排名。多语种、情绪、长文应按实际使用文本继续听测。
- 这里运行社区 MLX 移植与量化权重；没有把其音质与官方 CUDA 实现的等价性视为已验证。

完整操作与实验协议见 [使用指南](docs/使用指南.md) 和 [研究方法](docs/研究方法.md)。整合包候选、兼容性与来源见网页「模型与整合包」及 [整合包调研](docs/整合包调研.md)。

本机样本、耗时、自动转写差异及异常保留见 [本机验证记录](docs/本机验证记录.md)。其中包括 15 条三模型多语气对照、英日语、剧本与长文复测。

本机处理过 Index MLX 0.31.1 的崩溃并锁定可运行的 0.32.4，见 [运行时修复记录](docs/运行时修复记录.md)。

## 来源和许可

[IndexTTS 官方](https://github.com/index-tts/index-tts) · [Index MLX 适配](https://github.com/groxaxo/mlx-indextts2-inference) · [BreezeTTS2 官方](https://github.com/breezeblue-ai/breeze-tts) · [Breeze MLX 适配](https://github.com/rishikksh20/breeze-tts-mlx)

Breeze 的代码许可与模型许可不同，**模型权重、衍生模型及自托管输出限研究和非商业使用**。Index 的模型按其 bilibili 模型协议执行。参考录音及其他整合包遵循各自许可。
