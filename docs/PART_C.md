# C：多模型抓取选择与评估

C 在 A 提供的可行候选中选择抓取 ID，并让同一套物理控制器执行。现在包含 M3、
A1、一次输出修复、几何回退、原始回答缓存、批量运行、恢复和汇总。所有输出仍为
**pilot**：当前 A 为 `observable_primitives_v2`，使用共同固定目标与整个两秒保持区间逐步评分（`fixed_target_hold_v2`）；正式候选、评分及消融定义仍需全组冻结。旧 `bounded_ik_v1` 的目标/评分不同，旧验收数据保留在独立目录，不与本版混算。

## 1. 安装与快速验证

推荐 Python 3.11 或 3.12，在仓库根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-c.txt
python -m unittest discover -s tests -v
python -m grasp_c validate --config configs/c.mock.json
python -m grasp_c run --config configs/c.mock.json --out results/local/c_mock
```

`mock` 是确定性接口测试，物理执行仍由真实 PyBullet 完成。它不能用作 LLM 性能结果。
当前 macOS arm64 + 新版系统 SDK 编译 PyBullet 时，其自带旧版 zlib 可能在 `fdopen`
处报错。若发生该错误，使用：

```bash
CFLAGS='-include stdio.h' python -m pip install -r requirements-c.txt
```

Windows 可执行 `conda env update -f environment.yml`、`conda activate grasp-a`，
再运行上述 `python -m ...` 命令。`requirements-c.lock.txt` 是本次 macOS arm64、
Python 3.12 环境的完整版本快照；跨平台安装使用 `requirements-c.txt`，每次运行的
manifest 会记录实际环境。

## 2. 使用当前 ChatGPT 账号

已提供 `codex` 适配器，它通过官方 `codex exec` 使用本机已有的 ChatGPT CLI 登录。
不读取、复制或提交登录凭据。

```bash
codex login status
# 若尚未登录，执行 codex login 并在浏览器完成登录
python -m grasp_c validate --config configs/c.codex.json
python -m grasp_c run --config configs/c.codex.json --out results/local/c_codex
```

配置中的模型是本机实测可用的 `gpt-5.6-luna`。模型访问权限由账号和 CLI 决定；
本次 CLI 0.152.0 明确拒绝 `gpt-6.1-sol`，因此没有把它作为此配置的默认模型。
修改模型后使用新的输出目录。模型不可用、鉴权失败或参数配置错误会保存错误并停止，
不会自动换模型冒充该方法。

此适配器在空临时目录运行，关闭项目指令、命令、图片、搜索和多 agent 工具，
并检查 CLI 事件，发现工具调用就排除回答。它使用 Codex 自身的系统指令，
与直接 API 的输入环境不同，统计时用独立的方法 ID；记录中的模型 ID 是请求 ID，
不声称已核实底层模型快照。依赖支持 `--ignore-user-config`、`--ephemeral`、
`--output-schema` 和 `--json` 的 Codex CLI，本次验证版本为 0.152.0。

官方说明：[登录方式](https://learn.chatgpt.com/docs/auth)、
[非交互运行、结构化输出和已有登录复用](https://learn.chatgpt.com/docs/non-interactive-mode)。

## 3. 在代码中接入 GPT API

标准 OpenAI API 使用 OpenAI Platform 的 API key。可以用同一 OpenAI 账号管理，
但 API 的计费和用量独立于 ChatGPT 订阅；当前 ChatGPT 登录不能直接替代 SDK 的
`OPENAI_API_KEY`。项目不会从 Codex 登录文件提取 token。

在 [API key 管理页面](https://platform.openai.com/api-keys) 创建项目 key，
仅在本地终端设置：

```bash
export OPENAI_API_KEY='你的 API key'
python -m grasp_c run --config configs/c.openai.json --out results/local/c_openai
```

PowerShell 使用 `$env:OPENAI_API_KEY = '你的 API key'`。不要把真实 key 放进
配置、源码或提交；配置只允许指定 `api_key_env` 环境变量名。

实现使用官方 SDK 的 Responses API，并用 JSON Schema 约束输出：

```python
from grasp_a.contracts import load_candidates
from examples.evaluate_one_trial import evaluate_trial
from grasp_c.providers import build_provider
from grasp_c.selectors import LLMSelector

provider = build_provider({"type": "openai", "api_key_env": "OPENAI_API_KEY"})
provider.preflight()
selector = LLMSelector(
    provider, "gpt-6-luna", variant="M3",
    parameters={"reasoning_effort": "low", "max_output_tokens": 1024},
    cache_dir=".cache/grasp_c",
)
trial = evaluate_trial(
    "pour_prep", 0, "M3_GPT_LUNA",
    load_candidates("configs/candidates.c-pilot.json"), selector,
)
```

适配器内部对应 `OpenAI(...).responses.create(model=..., input=..., store=False,
text={"format": {"type": "json_schema", "strict": True, ...}})`。不会默认给所有
模型发送 temperature 或解码 seed；环境 seed 只固定仿真并进入日志/缓存。
不支持的模型参数会明确报错，用户需按模型能力配置。

官方参考：[Python SDK](https://developers.openai.com/api/reference/python)、
[结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)、
[API 认证](https://developers.openai.com/api/reference/overview)、
[订阅与 API 用量](https://learn.chatgpt.com/docs/pricing)。

## 4. 多模型与其他服务

一个 JSON 配置包含多个方法，每个方法独立指定 provider、model、parameters 和 variant。
`configs/c.openai.json` 已示范 GPT-6 Luna 与 GPT-6.1 Sol 两个模型，以及 Luna 的 A1。
如账号未开放其中一个模型，先移除该方法或改成实际可用的模型。

```json
{
  "id": "M3_MY_MODEL",
  "kind": "llm",
  "variant": "M3",
  "provider": {
    "type": "openai_compatible",
    "base_url": "http://localhost:8000/v1",
    "api_key_env": "LOCAL_LLM_API_KEY",
    "structured_outputs": true
  },
  "model": "your-served-model",
  "parameters": {"max_output_tokens": 1024}
}
```

`openai_compatible` 默认使用 Chat Completions；支持 Responses 的服务可加
`"api": "responses"`。服务不支持 JSON Schema 时可设置 `structured_outputs: false`，
仍会执行本地严格输出校验。兼容接口在输出字段、参数和 token 用量上可能有差异，
接入新服务时需先做该服务的真实冒烟测试。示例见 `configs/c.compatible.json`。

新服务若不兼容 OpenAI，增加实现 `LLMProvider` 的适配器：提供 `identity`、
`preflight()`、`generate(ModelRequest) -> ModelResponse`，再在 `build_provider()` 注册。
选择器、缓存和物理评估器不依赖具体 SDK。

## 5. 方法与失败策略

| 方法 | 当前定义 |
|---|---|
| M3 | LLM 根据任务意图和公开候选信息排序并选择 |
| A1 | 暂定消融：去掉 task_id 和任务描述，其余输入/输出协议相同 |
| GEOMETRY_REFERENCE | 按输入 geometry_score 降序，分数相同时按 ID 升序 |
| SMOKE / mock | 仅验证接口与流水线 |

原仓库没有规定 A1 的消融含义，本实现把 `remove_task_information` 明确写入 metadata。
若小组已有其他定义，需要修改 prompt、升级版本并使用新运行目录。
几何参考及回退使用的是现有 pilot 分数；B 仍负责冻结正式评分、M0/M1/M2 和独立可接受集合。

LLM 只收到 `id/region/approach/gripper_width/geometry_score`。位姿、筛选失败原因、
仿真轨迹、最终成功以及 oracle 标签均不传入模型。

输出必须严格为 `{"selected_id": "G02", "ranking": ["G02", "G03"]}`：ranking 是
当前所有可行 ID 的完整排列，首项等于 selected_id。非法 JSON、ID 或排名只修复一次；
仍无效则按几何参考规则回退。`fallback_to_geometry` 是当前主字段；`fallback_to_m1` 只保留为旧日志兼容别名，不能据此声称执行了 B 的正式 M1。超时、限流、拒绝或截断单独记原因并回退；鉴权、
模型访问、配置及缓存损坏不当成一次成功的 LLM 回答。SDK 的网络重试与这一次格式修复
分开，SDK 重试上限由 `provider.max_retries` 配置。

## 6. 缓存、重复与公平比较

缓存存放原始模型响应而非只存选中的 ID。键包括 provider 身份、模型、参数、
实际 prompt/schema、prompt_version、环境 seed、sample_id 和初始/修复阶段。
参数、候选公开信息、prompt 或模型改变会得到不同缓存。

- `cache_mode: read_write`：复用已有响应，缺失时调用并保存。
- `cache_mode: read_only`：只重放，缺失明确报错。
- `cache_mode: off`：不读写，用于新的独立模型采样。

设置 `repeats` 后，每个 repeat 都有独立 sample_id，避免把一个缓存回答当成多次
独立 LLM 调用。跨任务的 A1 prompt 相同，可能复用同一个响应；它代表任务无关的相同
选择策略，日志会显示缓存命中。若要跨任务也独立采样，请关闭缓存。
用相同 seed、候选、控制器和环境比较方法。seed 0–9 已参与控制器 pilot；正式
实验划分由小组冻结，配置 `seed_split.evaluation` 时会检查不与 `pilot_controller` 重叠。

## 7. 批量运行、恢复与输出

```bash
python -m grasp_c run --config configs/c.codex.json --out results/local/my_run --media
python -m grasp_c run --config configs/c.codex.json --out results/local/my_run --media --resume
python -m grasp_c summarize results/local/my_run
```

默认恢复会跳过所有已保存记录，包括错误，避免意外重复调用模型。明确要重试错误时加
`--resume --retry-errors`；旧记录会归档。配置、源码、资产、环境和 provider 身份变化时
拒绝恢复，应创建新目录。

| 输出 | 内容 |
|---|---|
| manifest.json | 配置、候选/源码/资产哈希、Git 状态、包版本、provider、评分版本、观测配置、固定策略模式、计划、seed 划分 |
| filters/seedN.json | 每个 seed 共享的完整筛选或筛选异常 |
| trials/METHOD/TASK/seedN_repeatR.json | 完整 filter、selection、episode、repeat、原始回答和异常 |
| summary.json | 各方法/任务统计、配对比较、分母与排除数 |
| media/ | 按 repeat、方法和任务区分的视频与图片 |

每个 seed 只筛选一次；每次物理 episode 都新建世界。结果逐条原子保存，运行目录有
进程锁。`validate` 离线检查配置，不调用模型或要求 API key；`summarize` 只读取已保存记录。
退出码 0 表示运行完整且没有运行/协议异常，物理失败需读取 `episode.success`。

## 8. 统计口径

summary 区分 completed、空候选、筛选异常、选择异常、非法选择、执行异常和未完成。
物理成功/碰撞/掉落标记的分母只用 completed；缺失位姿误差保持 null，均值不补零。
同时报告计划覆盖率、可行 seed 覆盖率、有效样本数、原始输出有效性、修复与回退率、
选择耗时、repeat 一致性、缓存以及 token 用量。缓存里的原始 token 用量与本次新调用
的 `live_token_usage` 分开，后者也不是账单金额估算。

配对结果仅使用共同完成、task/seed/repeat/候选哈希/控制器/manifest 相同的样本，
排除数单列。独立可接受集合由 B 提供，配置 `acceptable_sets` 路径后才能计算任务
可接受选择比例，否则该指标为 null。支持如下格式：

```json
{"pour_prep": ["G02"], "handover_prep": {"0": ["G03"], "default": ["G02", "G03"]}}
```

这是文件结构示意，不是任务真值。标签必须独立冻结；代码不会从物理成功反推标签，
也不会把标签加入 prompt。当前候选少且两个任务上都较容易成功，结果主要用于接口
验收；正式选择器收益仍需 B 补齐有任务区分度的候选并冻结评分与独立标签。A 的共同固定目标与完整保持判定已接入本版。


## 9. 与 A/B 主分支对齐

本次已同步 `main` 的 `5ae48d2`。仓库实际默认分支是 `main`，不存在 `master`。
C 的主实验只选择抓取 ID，所有方法共用 A 的 `policy_mode=fixed`；新增的逐步
primitive 控制属于另一条实验轨道，当前批量配置会拒绝混入 incremental 模式。

每个配置都显式记录 `observation_config`，同一份规范化配置用于筛选、任务执行
及 manifest。可测试观测估计误差或接入外部估计位姿，例如：

```json
{
  "policy_mode": "fixed",
  "observation_config": {
    "object_pose": {"position": [0.55, 0.0, 0.645], "quaternion": [0.0, 0.0, 0.0, 1.0]},
    "source": "calibrated_prior",
    "position_noise_m": 0.003,
    "yaw_noise_deg": 2.0,
    "noise_seed": 1000
  }
}
```

`object_pose` 是标定先验或外部估计，不从仿真真实杯子位姿取得；noise_seed 仅控制
估计扰动，与环境 seed、LLM 的 sample_id 分开。观测配置、评分版本或策略模式不同
时不能作配对比较；恢复运行也会拒绝配置变化或不匹配的筛选缓存。

```bash
python -m grasp_c run --config configs/c.mock.noisy.json --out results/local/c_mock_noisy
```

主分支目前仍只有 G01/G02/G03 pilot 候选，没有 B 的正式模块、M0/M1/M2 或独立
任务真值。当前五字段公开候选、选择器函数签名和 acceptable_sets 标签接口均与
A/B/C 共享协议一致；GEOMETRY_REFERENCE 及其回退仍明确使用 pilot 输入分数。
待 B 提交正式选择器后应直接使用其实现，不能把本地参考算法重命名成 B 的实验结果。
