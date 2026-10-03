# Qwen3.8 27B 本机评估（2026-09-24）

## 测试环境与来源

- Windows，RTX 4060 Ti 16GB，系统内存 31.6GB，Ryzen 5 7500F。
- [llama.cpp b10566](https://github.com/ggml-org/llama.cpp/releases/tag/b10566) 的 Windows CUDA 12.4 发行包，发行包 SHA256 已核对。
- [Unsloth Qwen3.8 27B UD-Q3_K_XL](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/blob/main/Qwen3.8-27B-UD-Q3_K_XL.gguf)：13,146,393,504 字节，SHA256 `8c2a45ff85e7674ca185ec8eb6cdeab0e617ed9d8018caed0b64380eb2a67a5e`。
- [mmproj-F16](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/blob/main/mmproj-F16.gguf)：927,607,488 字节，SHA256 `cbb841a9ee0636b2ec172f5bb8df2ea8dfeb01e90fe7c6126581d662a0b4e43e`。
- 主模型与视觉投影经 ModelScope 镜像下载，并与上述发布文件 SHA256 核对。

UP 主提供的参数已保存为 `D:\Models\Qwen3.8-27B\start-qwen38-video.ps1`。只有文件路径、监听地址（`127.0.0.1`）和端口（`18081`）按本机环境调整；其余 96K、Q4 KV、Flash Attention、MTP、视觉投影与采样设置保持原样。

## 实测结果

| 配置 | 显存占用 / 空余 | 提示处理 | 输出 | 任务结果 |
| --- | --- | --- | --- | --- |
| Qwen3.6 27B Q4_K_M，16K，部分 CPU/GPU（对照） | 约 12.9GB 占用 | 8,302 token：43.78 秒 | 写作 304 token：71.69 秒（约 4.2 token/秒，端到端） | 3/3 任务正确；原生工具类别正确 |
| Qwen3.8 27B Q3，UP 原样 96K | 15.98GB / 128MB | 前 2,064 token：64.53 秒；到 4,112 token 累计 242.71 秒 | 长提示因持续变慢中止 | 模型和视觉投影均能加载，但该上下文在此机上不实用 |
| Qwen3.8 27B Q3，32K，其余参数相同 | 14.62GB / 1.49GB | 8,302 token：13.31 秒；28,118 token：45.98 秒 | 写作 268 token：12.33 秒；服务端该次解码约 22.7 token/秒 | 3/3 任务正确；28K 输入找回正确记录 |
| Qwen3.8 27B Q3，64K，其余参数相同 | 15.46GB / 648MB | 8,302 token：13.28 秒；56,080 token：107.46 秒 | 写作 285 token：13.00 秒；56K 后解码约 24.5 token/秒 | 3/3 任务正确；56K 输入找回正确记录 |

32K 的 8K 提示处理服务端速度约 703 token/秒；64K 的 56K 提示处理约 530 token/秒。32K 下简短结构化回答的解码约 31–32 token/秒。上述本机结果没有达到视频标题所说的 90 token/秒，也没有证明 96K 真实输入可用。

原生 `set_tool_categories` 调用在非思考模式下格式正确，但同一题两次都只选了 `creation_data`，漏掉查询作品信息所需的 `project_files`。开启 Qwen3.8 的思考后，非流式和流式工具调用都包含 `project_files`；流式响应正确结束于 `tool_calls`。32K 下视觉识别正确读出司命工作台截图的“第23章 灯塔重燃”，但 CPU 视觉投影单次耗时 86.85 秒。

这些任务是司命关键契约的本机探针，不是全面的模型质量评测。写作样例可用，但两次都在“发现日期是明天”后追加了尾句，严格的结尾约束尚不稳定。

## 接入决定

在司命内置目录加入 Qwen3.8 27B Q3 的**文本**选项；16GB 显存且约 32GB 内存的设备初始推荐 32K，上下文可由用户明确调高。启动时沿用此次有效的 Q4 KV、Flash Attention、MTP 与 2GB RAM 缓存参数；该模型的工具回合启用思考，以保持工具类别选择正确。视觉投影不随目录项安装，视觉能力暂不列入该选项。96K 原样参数保留在本机脚本供复验，但不作为司命默认值。

集成后的司命启动命令已用同一 b10566 二进制、32K 上下文、临时 API 密钥实际加载并通过 `/v1/models` 检查；探针结束后进程退出，未留下 `llama-server.exe`。
