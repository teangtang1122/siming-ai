# Siming 本地 AI 与 LoRA 训练

## 数据与目录

- 推理模型：`%LOCALAPPDATA%\Siming\models`
- llama.cpp：`%LOCALAPPDATA%\Siming\runtimes\llama_cpp`
- 训练环境：`%LOCALAPPDATA%\Siming\runtimes\trainer`
- 训练集、检查点与适配器：`%LOCALAPPDATA%\Siming\training`

这些目录可以与小说数据目录分开。模型目录可在桌面控制面板的“本地 AI”页面修改。

## 硬件分档

| 档位 | 默认模型 | 默认上下文 | 适用设备 |
| --- | --- | --- | --- |
| 轻量 | Qwen3 4B Q4_K_M | 8K | CPU、低显存设备 |
| 标准 | Qwen3 8B Q4_K_M | 16K | 8-12GB 显存或较大内存 |
| 高质量 | Qwen3 14B Q4_K_M | 32K | 16GB 及以上显存 |

启动模型时先尝试完整 GPU 卸载。失败后会减少 GPU 层和上下文，最后尝试 CPU。

## 任务路由

模型中心可分别为以下任务指定本地模型：

- 项目助手
- 作品建档
- 新书与大纲规划
- 章节写作
- 质量评估

用户明确指定的模型优先于任务路由。未指定时使用任务设置，再回退到全局默认模型。API 回退默认关闭。

## 结构化输出

本地模型通过 llama.cpp 的 OpenAI 兼容接口接入 `LLMGateway`。工具型任务继续使用 Siming 的工具注册表；要求 JSON 的任务会启用 JSON 模式，支持调用方传入 JSON Schema。

## LoRA 训练 Beta

首版训练仅支持 NVIDIA CUDA，建议：

- 4B QLoRA：8GB 及以上显存
- 8B QLoRA：12-16GB 及以上显存
- 14B QLoRA：24GB 及以上显存

训练流程：

1. 选择作品并生成训练集。
2. 检查样本数量、长度和训练/验证划分。
3. 确认拥有文本训练权利。
4. 选择基座、LoRA Rank、样本长度与训练轮数。
5. 训练任务支持暂停、继续、取消和检查点恢复。
6. PEFT 适配器转换为 llama.cpp GGUF LoRA 后登记到模型中心。
7. 新适配器默认停用，用户确认后再启用或设为写作默认。

## 远程清单

程序始终内置可离线使用的基础模型清单。发布方可以通过：

- `MOSHU_MODEL_MANIFEST_URL`
- `MOSHU_MODEL_MANIFEST_PUBLIC_KEY`

提供 Ed25519 签名的远程模型清单。签名验证失败时不会使用远程内容，并自动回退到上次验证成功的缓存或内置清单。
