# C：多模型抓取选择与评估

C 在 A 提供的可行候选中选择抓取 ID，并让同一套物理控制器执行。包含 M3、
A1、一次输出修复、几何回退、原始回答缓存、批量运行、恢复和汇总。GPT 默认使用
官方 `openai` SDK 和 `OPENAI_API_KEY`；Gemini 使用官方 `google-genai` SDK 和
`GEMINI_API_KEY`。组员配置各自的 key 即可使用同一份代码，无须登录 Codex。

当前仍为 **pilot**：A 使用 `observable_primitives_v2` 控制器及
`fixed_target_hold_v2` 评分；正式候选、评分及消融定义仍需全组冻结。
C 给 A 的即时输出为 `selected_id/ranking` 和审计 metadata，物理成功由 A 判定。
最终交付是第 7–8 节的 manifest、逐条 trial 和多方法统计 summary。

## 1. 安装与离线验证

推荐 Python 3.11 或 3.12，在仓库根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-c.txt
python -m unittest discover -s tests -v
python -m grasp_c validate --config configs/c.api.json
```

Windows 可执行 `conda env update -f environment.yml`、`conda activate grasp-a`，
再运行上述 `python -m ...` 命令。已有虚拟环境需重新安装 `requirements-c.txt`，
其中包含 `openai`、`google-genai` 和 `python-dotenv`。跨平台安装使用该文件；
`requirements-c.lock.txt` 是 macOS arm64/Python 3.12 的版本快照。
每次运行的 manifest 记录实际环境。

无凭据也可以验证完整流水线：

```bash
python -m grasp_c run --config configs/c.mock.json --out results/local/c_mock
```

`mock` 是确定性接口测试，物理执行仍由真实 PyBullet 完成，不能用作 LLM 性能结果。
macOS arm64 编译 PyBullet 若在自带旧版 zlib 的 `fdopen` 处报错，可使用：

```bash
CFLAGS='-include stdio.h' python -m pip install -r requirements-c.txt
```

## 2. 为小组成员配置 API key

在项目根目录复制模板，再用本地编辑器填写自己的 key：

```bash
cp .env.example .env
```

Windows PowerShell 使用 `Copy-Item .env.example .env`。`.env` 不进入 Git；
不要把真实 key 放入 JSON、源码或聊天。只用一个服务时，另一个 key 可以保持空白。
项目只加载仓库根目录的 `.env`，不会搜索父目录；已导出的环境变量优先，
并且不会展开 key 中的变量表达式。

也可以在运行命令的同一终端导出环境变量：

```bash
export OPENAI_API_KEY='你的 OpenAI API key'
export GEMINI_API_KEY='你的 Gemini API key'
```

```powershell
$env:OPENAI_API_KEY = '你的 OpenAI API key'
$env:GEMINI_API_KEY = '你的 Gemini API key'
```

OpenAI key 在 [OpenAI Platform](https://platform.openai.com/api-keys) 创建；
Gemini key 在 [Google AI Studio](https://aistudio.google.com/apikey) 创建。
OpenAI API key 与 ChatGPT 登录分别配置，API 用量按 API 账户结算。
若终端更新了变量而 IDE/Codex 仍未读取，请在该进程的运行配置中更新变量，
或重启进程使其继承新环境；也可把 key 写入仓库根目录 `.env`。
缺少所选服务的 key 会在预检查时明确停止，不会自动换成 Codex 登录。

选择与凭据对应的配置：

| 配置 | 所需环境变量 | 方法 |
|---|---|---|
| `configs/c.openai.json` | `OPENAI_API_KEY` | GPT 的 M3、A1，以及几何参考 |
| `configs/c.gemini.json` | `GEMINI_API_KEY` | Gemini 的 M3、A1，以及几何参考 |
| `configs/c.api.json` | 两个 key | 同条件比较 GPT、Gemini 的 M3/A1 和几何参考 |

先用关闭缓存的单次样例验证鉴权、模型权限、输出和物理执行：

```bash
python -m examples.llm_smoke --out results/local/openai_smoke.json
python -m examples.llm_smoke --provider gemini --out results/local/gemini_smoke.json
```

默认 GPT 模型沿用 `gpt-6-luna`，Gemini 使用 `gemini-3.8-flash`；
模型访问权限取决于各自账户，可用 `--model` 指定可访问的准确 ID。
Gemini 3.8 Flash 默认设置 `thinking_level=low`；更换模型时需同步调整批量配置的
模型参数。样例为仍有旧模型访问权限的账户保留 2.5 系列的 `thinking_budget=0` 分支。
本次真实调用发现：即使 `models.list()` 列出了 `gemini-2.5-flash`，新用户调用仍可能
收到 404，提示该模型不再向新用户提供，并建议 `gemini-3.8-flash`。模型出现在列表
中不保证该账户能够生成内容，应以关闭缓存的真实调用结果判断。

样例代码见 [`examples/llm_smoke.py`](../examples/llm_smoke.py)。它打印
`raw_initial_response`、`selected_id`、`ranking`、`live_requests`、`cache_hit`、
修复/回退标记及 `episode.success`，并保存完整 trial。只有新调用、未回退的
模型回答与物理成功同时成立时才退出 0；物理失败本身不意味着 API 不通。
输出路径若已存在会被拒绝，重跑请指定新路径。

2026-10-16 本次接入验证：已读取 `GEMINI_API_KEY`，官方 SDK 的模型列表请求成功。
按账户提示改用 `gemini-3.8-flash` 后，两次关闭缓存的完整样例及一次最小生成请求
均返回 HTTP 503 / UNAVAILABLE，服务端说明模型需求过高、建议稍后重试。
完整样例保存了错误与 `fallback_to_geometry=true`，其物理执行成功来自几何回退，
**不能算 Gemini 生成或 LLM 评估成功**。当前进程仍未读取到 `OPENAI_API_KEY`，
因此本次也尚未完成 OpenAI API key 的真实验证。此前 Codex 登录方式的成功记录
不能替代这两个 API 的验证；服务恢复、凭据可见后，应重新运行上述关闭缓存样例。

通过单次测试后运行批量比较：

```bash
python -m grasp_c run --config configs/c.api.json --out results/local/c_api
python -m grasp_c summarize results/local/c_api
```

需要代理时在本机设置 `HTTP_PROXY`/`HTTPS_PROXY`；SDK 会继承进程环境，
无需把代理凭据写进实验配置。官方接入参考：[OpenAI quickstart](https://developers.openai.com/api/docs/quickstart)、
[Gemini quickstart](https://ai.google.dev/gemini-api/docs/quickstart)。

## 3. 在代码中调用 GPT 或 Gemini

两个 SDK 适配器共用 `LLMProvider` 边界；选择器、修复、缓存和评估器无需修改。
下面示例加载本地 `.env` 后调用预检查（由适配器处理），并让 A 执行所选抓法：

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

换成 Gemini 时只替换 provider、model 和参数：

```python
provider = build_provider({"type": "gemini", "api_key_env": "GEMINI_API_KEY"})
provider.preflight()
selector = LLMSelector(
    provider, "gemini-3.8-flash", variant="M3",
    parameters={"thinking_level": "low", "max_output_tokens": 1024},
    cache_dir=".cache/grasp_c",
)
```

GPT 适配器内部使用 `OpenAI(...).responses.create(..., store=False)`，
并以 JSON Schema 约束输出。Gemini 适配器使用
`genai.Client(...).models.generate_content(...)`，以 `system_instruction` 和
对话内容传入相同的 prompt，用 JSON MIME 类型和 JSON Schema 约束输出。
两者均记录模型实际返回文本、token 用量和请求状态，随后执行相同本地输出校验。

不会默认给所有模型发送 temperature 或解码 seed。环境 seed 只固定仿真并进入
日志/缓存；服务端模型参数取决于具体模型能力，不支持的参数明确报错。
Gemini 3 的 `thinking_level` 和 Gemini 2.5 的 `thinking_budget` 不能混用，也不能直接复制到 GPT。
官方参考：[OpenAI Python SDK](https://developers.openai.com/api/reference/python)、
[OpenAI 结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)、
[Gemini 结构化输出](https://ai.google.dev/gemini-api/docs/structured-output)。

## 4. 多模型与其他服务

一个配置可包含多个方法，每个方法独立指定 provider、model、parameters 和 variant。
`configs/c.api.json` 同时包含 GPT 与 Gemini，所有方法共享同一份 seed、候选、
观测配置和固定执行器。为新的模型增加独立方法 ID，并使用新的运行目录。
配置中的模型 ID 可以改为各自账户可访问的准确 ID；没有任何静默换模型行为。

Qwen、DeepSeek 或本地服务可使用 OpenAI 兼容接口：

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
原生 Gemini 使用 `type: gemini`，无须填写 OpenAI 兼容的 base_url。

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
python -m grasp_c run --config configs/c.api.json --out results/local/my_run --media
python -m grasp_c run --config configs/c.api.json --out results/local/my_run --media --resume
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

C 使用 A/B/C 的公共协议。仓库实际默认分支是 `main`，不存在 `master`。
C 的主实验只选择抓取 ID，所有方法共用 A 的 `policy_mode=fixed`；逐步
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

B 已提供候选生成、实验性几何评分和 Random/Geometry prototype，详见主 README
第 9 节。当前 C 的 API 冒烟配置仍使用 G01/G02/G03 pilot 候选，避免把 API
接入变动与候选集变动混在一起；正式 M0/M1/M2 定义、评分权重和独立任务标签仍待冻结。
五字段公开候选、选择器函数签名和 acceptable_sets 标签接口与共享协议一致。
GEOMETRY_REFERENCE 及其回退明确使用输入分数，不声称执行 B 的正式 M1。
使用 B 导出的 `candidates.scored.json` 时，可替换配置中的 `candidates` 路径；
内部几何 sidecar 不进入 prompt。正式比较前应全组确认评分版本与候选集，创建新的
输出目录，不能把参考算法重命名成正式实验结果。

## 10. 可选：保留的 Codex CLI 接入

`codex` 适配器仍可复用本机已有的 ChatGPT CLI 登录，需要显式选择：

```bash
codex login status
python -m examples.llm_smoke --provider codex --model gpt-5.6-luna --out results/local/codex_smoke.json
python -m grasp_c run --config configs/c.codex.json --out results/local/c_codex
```

此方式不读取、复制或提交登录凭据。适配器在空临时目录运行，关闭项目指令、命令、
图片、搜索和多 agent 工具，并检查 CLI 事件以排除工具调用。Codex 自身仍有系统
指令，与直接 API 的输入环境不同，需作为独立方法比较，不能把它的测试结果视作
OpenAI API key 的验证。模型 ID 为请求 ID，不声称已核实底层快照。

历史上 2026-10-16 关闭缓存的 `gpt-5.6-luna`/Codex CLI 测试返回
`{"selected_id":"G02","ranking":["G02","G03"]}`，真实请求 1 次、无修复/回退，
新版 A 的倒水准备执行成功。该记录只证明当时 Codex 链路可用，且现有候选不足以
证明 LLM 比几何参考更优。旧 `bounded_ik_v1` 记录与当前评分分开保存。

官方说明：[Codex 登录](https://learn.chatgpt.com/docs/auth)、
[非交互运行](https://learn.chatgpt.com/docs/non-interactive-mode)。
