# C：多模型抓取选择与评估

C 在 A 的可行候选中选择抓取 ID，并让同一套物理控制器执行，包含 M3、A1、一次
输出修复、几何回退、缓存、批量运行、恢复和汇总。**GPT 默认恢复为 Codex CLI 的
现有 ChatGPT 登录**；Gemini 使用官方 `google-genai` SDK 和 `GEMINI_API_KEY`。
默认运行不需要 `OPENAI_API_KEY`，不会调用额外计费的 OpenAI API。

当前仍为 **pilot**：`track=prep` 使用 `observable_primitives_v2` 控制器和
`fixed_target_hold_v2` 评分；`track=sequence` 使用 `staged_sequences_v1` 与
`physical_outcomes_v1`，完整序列与 B 基线接入见第 11 节。正式候选、评分及消融
定义仍需全组冻结，两轨道分别记录、缓存与统计。
C 给 A 的即时输出是 `selected_id/ranking` 和审计 metadata，物理成功由 A 判定。
最终交付是第 7–8 节的 manifest、逐条 trial 和多方法统计 summary。

2026-11-06 的第三轮冻结表评估使用 116 个多族候选、`wide_strict_v2` 场景和
seed 10–29，真实 M3/A1、M0/M1/M2/M4/M5 对比及默认流体复核见
[第 12 节](#12-第三轮冻结表上的真实-m3a1-评估)和
[本次评估报告](PART_C_EVALUATION_20261106.md)。以下旧 pilot 记录保留其当时口径。

## 1. 安装与离线验证

推荐 Python 3.11 或 3.12，在仓库根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-c.txt
python -m unittest discover -s tests -v
python -m grasp_c validate --config configs/c.multimodel.json
```

Windows 可执行 `conda env update -f environment.yml`、`conda activate grasp-a`，
再运行上述 `python -m ...` 命令。已有虚拟环境需重新安装 `requirements-c.txt`，
其中包含 `google-genai`、`python-dotenv` 和可选 OpenAI API 适配器使用的 `openai`。
Codex CLI 需单独安装，并在本机运行 `codex login` 完成 ChatGPT 登录。
跨平台安装使用 `requirements-c.txt`；`requirements-c.lock.txt` 是 macOS arm64/
Python 3.12 的版本快照，每次运行的 manifest 会记录实际环境。

无凭据可以验证完整物理流水线：

```bash
python -m grasp_c run --config configs/c.mock.json --out results/local/c_mock
```

`mock` 是确定性接口测试，物理执行由真实 PyBullet 完成，不能用作 LLM 性能结果。
macOS arm64 编译 PyBullet 若在自带旧版 zlib 的 `fdopen` 处报错，可使用：

```bash
CFLAGS='-include stdio.h' python -m pip install -r requirements-c.txt
```

## 2. 默认账号配置与真实调用

GPT 使用本机 Codex CLI 的 ChatGPT 登录：

```bash
codex login status
# 尚未登录时执行 codex login，并在浏览器完成登录
```

模型沿用已验证的 `gpt-5.6-luna`，参数为 `reasoning_effort=low`。GPT 不需要
OpenAI API key。适配器不读取、复制或提交登录 token；ChatGPT/Codex 额度和模型
访问权限由各自账户决定。模型不可用、登录失败或参数错误都会记录并停止，不会自动
改用 API key 或其他模型。Codex 子进程会移除继承的 `OPENAI_API_KEY` 和
`CODEX_API_KEY`，并拒绝 API key 登录状态；父进程环境不变，独立 API 方法仍可使用。

Gemini key 在 [Google AI Studio](https://aistudio.google.com/apikey) 创建。
复制项目模板后在本地编辑器填写 `GEMINI_API_KEY`：

```bash
cp .env.example .env
```

Windows PowerShell 用 `Copy-Item .env.example .env`。也可以在运行终端直接设置：

```bash
export GEMINI_API_KEY='你的 Gemini API key'
```

```powershell
$env:GEMINI_API_KEY = '你的 Gemini API key'
```

`.env` 已被 Git 忽略；不要把真实 key 放入 JSON、源码或聊天。项目只加载仓库根目录
`.env`，不会搜索父目录或展开 key 内的变量表达式；已导出的环境变量优先。
若 IDE/Codex 未读取终端更新的变量，更新其运行配置或重启进程，或使用本地 `.env`。
缺少 Gemini key 会明确报错，不会把该方法换成 GPT 或几何方法并冒充模型成功。

| 配置 | 所需凭据 | 方法 |
|---|---|---|
| `configs/c.codex.json` | 本机 Codex 的 ChatGPT 登录 | GPT 的 M3/A1 和几何参考 |
| `configs/c.gemini.json` | `GEMINI_API_KEY` | Gemini 的 M3/A1 和几何参考 |
| `configs/c.multimodel.json` | 上述两项 | 同条件比较 GPT、Gemini 的 M3/A1 和几何参考 |

先运行关闭缓存的单次样例：

```bash
python -m examples.llm_smoke --out results/local/codex_smoke.json
python -m examples.llm_smoke --provider gemini --out results/local/gemini_smoke.json
```

Gemini 默认是 `gemini-3.8-flash`、`thinking_level=low` 和 1024 输出 token 上限。
可用 `--model` 指定账户可访问的准确 ID，更换模型时应同步调整批量配置中的参数。
样例为仍有旧模型权限的账户保留 2.5 Flash 的 `thinking_budget=0` 分支；
显式指定 `gemini-2.5-pro` 时使用其最小思考预算 `thinking_budget=128`，不关闭思考。
真实调用发现：`models.list()` 即使列出 `gemini-2.5-flash`，新用户生成请求仍可能
收到 404，提示该模型不再向新用户提供并建议 3.8 Flash。列表不保证实际生成权限。

样例见 [`examples/llm_smoke.py`](../examples/llm_smoke.py)。它打印
`raw_initial_response`、`selected_id`、`ranking`、`live_requests`、`cache_hit`、修复/
回退标记与 `episode.success`，并保存完整 trial。只有新调用、未回退的模型回答与
物理成功同时成立时才退出 0；物理失败本身不意味着模型调用失败。输出若已存在会被
拒绝，重跑请指定新路径。

2026-10-16 恢复默认 Codex 方式后，关闭缓存重新实测返回
`{"selected_id":"G02","ranking":["G02","G03"]}`，请求模型 `gpt-5.6-luna`，
真实请求 1 次、cache_hit=false、无修复/回退。新版 A 的倒水准备执行成功，完整
保持 2 秒，无碰撞/掉落。完整记录为本地
`results/local/codex_restored_default_live_smoke.json`。
本次 Gemini 官方 SDK 已读取 key，模型列表请求成功，
但改用 3.8 Flash 后两次完整样例及一次最小生成请求均返回 HTTP 503/UNAVAILABLE，
服务端提示需求过高、稍后重试。完整样例的物理成功来自几何回退，**不能视为 Gemini
生成或评估成功**。OpenAI API key 方式尚未实测；它不属于默认流程，无需为此配置
`OPENAI_API_KEY`。Gemini 服务恢复后应重跑关闭缓存的样例。

随后按要求测试 `gemini-2.5-pro`：项目样例与最小 SDK 生成请求均返回
HTTP 404 / NOT_FOUND，服务端明确说明该模型不再向新用户开放，建议
`gemini-3.1-pro-preview`。本次没有取得 Pro 回答，也没有自动换模型；完整项目记录
在 `results/local/gemini25_pro_native_live_smoke.json`。可使用
`python -m examples.llm_smoke --provider gemini --model gemini-2.5-pro --out results/local/gemini_pro_retry.json`
复测；当前默认仍保留已确认的 `gemini-3.8-flash`。

通过单次测试后运行多模型比较：

```bash
python -m grasp_c run --config configs/c.multimodel.json --out results/local/c_multimodel
python -m grasp_c summarize results/local/c_multimodel
```

Gemini SDK 继承 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量，无需把代理凭据写进实验
配置。官方说明：[Codex 登录](https://learn.chatgpt.com/docs/auth)、
[Codex 非交互运行](https://learn.chatgpt.com/docs/non-interactive-mode)、
[Gemini quickstart](https://ai.google.dev/gemini-api/docs/quickstart)。

## 3. 在代码中调用 GPT 或 Gemini

两种后端共用 `LLMProvider` 边界；选择器、修复、缓存和评估器无需修改。
默认 GPT 使用 Codex CLI：

```python
from grasp_a.contracts import load_candidates
from examples.evaluate_one_trial import evaluate_trial
from grasp_c.providers import build_provider
from grasp_c.selectors import LLMSelector

provider = build_provider({"type": "codex", "timeout_s": 120})
provider.preflight()
selector = LLMSelector(
    provider, "gpt-5.6-luna", variant="M3",
    parameters={"reasoning_effort": "low"},
    cache_dir=".cache/grasp_c",
)
trial = evaluate_trial(
    "pour_prep", 0, "M3_CODEX",
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

GPT 适配器通过 `codex exec` 在空临时目录中调用，关闭项目指令、命令、图片、搜索
和多 agent 工具，并检查事件以排除工具调用。Codex 自身仍有系统指令，与直接 API
的输入环境不同，统计中保留独立方法/provider 身份；模型 ID 是请求 ID，不声称已经
核实底层模型快照。

Gemini 使用 `genai.Client(...).models.generate_content(...)`，将相同 prompt 映射
为 `system_instruction` 与对话内容，使用 JSON MIME 类型与 JSON Schema 约束
输出。两种后端均保存实际文本、token 用量和状态，再执行相同的本地输出校验。
环境 seed 只固定仿真并进入日志/缓存，不自动作为模型解码 seed。Gemini 3 的
`thinking_level` 与 2.5 的 `thinking_budget` 不能混用，也不能复制到 Codex 参数。

## 4. 多模型与其他服务

`configs/c.multimodel.json` 是默认多模型配置，包含 Codex GPT 和 Gemini 的 M3/A1，
以及几何参考；所有方法共享同一份 seed、候选、观测配置和固定执行器。
每个方法独立指定 provider、model、parameters 和 variant。新增模型应使用独立
方法 ID 和新的运行目录，不会静默换模型。

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
`"api": "responses"`。不支持 JSON Schema 时可设置 `structured_outputs: false`，
仍执行本地严格输出校验。各服务参数和 token 字段可能有差异，需先做真实冒烟测试；
示例见 `configs/c.compatible.json`。原生 Gemini 使用 `type: gemini`，不填 OpenAI
兼容 base_url。

新服务若不兼容 OpenAI，可增加实现 `LLMProvider` 的适配器，提供 `identity`、
`preflight()`、`generate(ModelRequest) -> ModelResponse`，再在 `build_provider()`
注册。选择器、缓存和物理评估器不依赖具体 SDK。

## 5. 方法与失败策略

| 方法 | 当前定义 |
|---|---|
| M3 | LLM 根据任务意图和公开候选信息排序并选择 |
| A1 | 暂定消融：去掉 task_id 和任务描述，其余输入/输出协议相同 |
| GEOMETRY_REFERENCE | 按输入 geometry_score 降序，分数相同时按 ID 升序；本地 pilot 参考 |
| B_RANDOM_REFERENCE / b_random | 调用 B 的 Random，忽略评分，按 scene seed 和有序可行列表生成排名 |
| B_GEOMETRY_EXPERIMENTAL / b_geometry | 调用 B 的实验几何选择器，要求可核验的评分版本、权重及候选来源 |
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
批量入口还将轨道、场景/控制器/评分版本、观测配置、完整候选指纹、任务 ID、任务
定义指纹和 B 来源绑定为缓存上下文；这些审计身份不会添加到模型输入。
参数、候选公开信息、prompt 或模型改变会得到不同缓存。

- `cache_mode: read_write`：复用已有响应，缺失时调用并保存。
- `cache_mode: read_only`：只重放，缺失明确报错。
- `cache_mode: off`：不读写，用于新的独立模型采样。

设置 `repeats` 后，每个 repeat 都有独立 sample_id，避免把一个缓存回答当成多次
独立 LLM 调用。A1 移除任务 ID 和公开任务描述，两个任务的候选相同时 prompt 可相同，
但批量入口仍以任务身份区分缓存，不会跨任务复用回答。新的 B/C Codex 示例显式关闭
缓存，用于真实模型采样；已保存 trial 的恢复仍会跳过该条，不重新调用模型。
用相同 seed、候选、控制器和环境比较方法。seed 0–9 已参与控制器 pilot；正式
实验划分由小组冻结，配置 `seed_split.evaluation` 时会检查不与 `pilot_controller` 重叠。

## 7. 批量运行、恢复与输出

```bash
python -m grasp_c run --config configs/c.multimodel.json --out results/local/my_run --media
python -m grasp_c run --config configs/c.multimodel.json --out results/local/my_run --media --resume
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

以上为 `prep` 的输出结构：每个 seed 只筛选一次；`sequence` 按 task/seed 独立筛选，
结构及数值状态见第 11 节。每次物理 episode 都新建世界。结果逐条原子保存，运行目录有
进程锁。`validate` 离线检查配置，不调用模型或要求 API key；`summarize` 只读取已保存记录。
退出码 0 表示运行完整且没有运行/协议异常，物理失败需读取 `episode.success`。

## 8. 准备任务的统计口径

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
C 的主实验只选择抓取 ID，同轨道所有方法共用 A 的 `policy_mode=fixed`；逐步
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

## 10. 显式可选：OpenAI API key 方式

默认 GPT 不需要本节。仅当组员明确希望使用额外计费的 OpenAI API 时，才在
[OpenAI Platform](https://platform.openai.com/api-keys) 创建 key，并在本机设置
`OPENAI_API_KEY`。其 API 计费和用量独立于 ChatGPT 订阅。

```bash
export OPENAI_API_KEY='你的 OpenAI API key'
python -m examples.llm_smoke --provider openai --model gpt-6-luna --out results/local/openai_smoke.json
python -m grasp_c run --config configs/c.openai.json --out results/local/c_openai
```

`configs/c.api.json` 将 GPT 的 OpenAI API 和 Gemini API 放在同一实验中，需要两个
key；与默认 `configs/c.multimodel.json` 的 Codex GPT 方式不同。OpenAI 适配器使用
官方 SDK 的 Responses API、`store=False` 和严格 JSON Schema。此方式尚未使用
当前账户完成真实联网验证；Codex 登录的成功记录不能替代 API key 验证。

官方参考：[OpenAI quickstart](https://developers.openai.com/api/docs/quickstart)、
[OpenAI Python SDK](https://developers.openai.com/api/reference/python)、
[OpenAI 结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)。

## 11. 完整序列与 B 基线接入

C 现在支持显式 `track="sequence"`，包含 `transport_place`、`handover_transfer`、
`pour_water`。它调用 A 的 `get_sequence_candidates / run_sequence`；旧配置省略
`track` 时仍为 `prep`，使用 `get_feasible_candidates / run_episode`。不能在同一份
配置中混用新旧任务，也不能把两个轨道的成功率、可行列表或运行目录混用。

| 配置 | 轨道和输入 | 方法 |
|---|---|---|
| `configs/c.sequence.mock.json` | 完整序列；现有 G01/G02/G03 pilot 候选 | 几何参考与 M3 mock，无真实模型 |
| `configs/c.sequence.codex.json` | 完整序列；共享 B 已评分候选与来源文件 | B Random、B 实验几何、Codex M3/A1 |
| `configs/c.prep.b.codex.json` | 旧准备任务；同一份 B 输入 | 相同四种方法 |

### 先固定 B 输入，再运行 C

在项目根目录激活环境，按顺序执行：

```bash
python -m grasp_c prepare-b --out results/local/c_b_inputs
python -m grasp_c validate --config configs/c.sequence.codex.json
codex login status
python -m grasp_c run --config configs/c.sequence.codex.json --out results/local/c_sequence
python -m grasp_c run --config configs/c.sequence.codex.json --out results/local/c_sequence --resume
python -m grasp_c summarize results/local/c_sequence
```

`prepare-b` 只做静态候选生成和几何评分，不执行筛选、任务或 LLM 请求。输出目录
必须不存在，以免覆盖上一份输入。它导出以下文件，所有比较方法读取同一份候选：

| 文件 | 用途 |
|---|---|
| `candidates.scored.json` | B 已计算实验性评分的完整候选，供 A 执行与筛选 |
| `provenance.json` | 评分版本、权重、候选/来源/内部报告指纹，供 C 校验与记录 |
| `report.internal.json` | 生成参数、分项分数、排除原因及几何来源审计 |
| `features.internal.json` | 内部几何诊断，不能发送给选择器 |

两份 B/C Codex 配置默认引用 `results/local/c_b_inputs/`。如需修改生成参数或权重，
用 `prepare-b --generation-config ... --scoring-config ... --out 新目录`，同步修改
配置的 `candidates` 与 `b_provenance`，并使用新的 C 运行目录。`validate` 会检查
候选与来源、内部报告指纹是否匹配；只把生成器占位零分文件改名不会得到有效 B 输入。

GPT 继续使用本机 Codex 的 ChatGPT 登录和 `gpt-5.6-luna`，无需 `OPENAI_API_KEY`。
M3/A1 在这两份配置中均设置 `cache_mode="off"`；新 trial 会真实调用模型，但
`--resume` 会跳过已保存 trial，不会因为关闭响应缓存而重复调用。默认也保留失败
记录；`--resume --retry-errors` 仅重试 C 的筛选/选择/执行调用异常，旧尝试归档。
任务物理失败、A 返回的数值错误和拒绝记录不会被当成可重试调用异常。改变任务
参数、源码、资产、环境、评分输入或 provider 身份时，恢复会拒绝，需新建运行。

如需录像，首次运行及恢复均加 `--media`；完整序列可同时加 `--plain` 关闭 A/B
标记。显示模式与录像选择也进入运行身份，恢复时必须保持一致。水仿真在 CPU 上
可能持续数分钟。`summarize` 只读取并校验已有记录，不调用模型，也不重新执行物理。

无需账号的接口检查可以直接使用现有候选，不需要 `prepare-b`：

```bash
python -m grasp_c validate --config configs/c.sequence.mock.json
python -m grasp_c run --config configs/c.sequence.mock.json --out results/local/c_sequence_mock
```

旧准备任务与 B 的比较使用不同输出目录：

```bash
python -m grasp_c validate --config configs/c.prep.b.codex.json
python -m grasp_c run --config configs/c.prep.b.codex.json --out results/local/c_prep_b
```

### 统一输入与审计边界

完整序列按 **task/seed** 筛选一次，然后让同一条件下的所有方法使用同一有序可行
列表。筛选绑定空心杯场景、完整候选指纹、任务定义与观测配置，不复用旧实心杯
轨道的筛选缓存。每次物理执行均创建新世界，执行动作和接收策略不随方法改变。

M3 仅收到公开任务意图和五字段候选：`id/region/approach/gripper_width/geometry_score`；
A1 移除任务信息，其余选择协议一致。完整位姿、B 内部特征、终端评分阈值、流体
质量、接触状态和正确答案不发送给模型。场景、候选、任务定义及评分来源指纹仅在
缓存上下文和审计日志中使用，不会悄悄加入 prompt。

`b_random` 复用 B 的 Random 实现，忽略评分，但保留相同输入候选和评分来源；
随机种子等于 scene seed，输入顺序不变时重复运行排名一致，repeat 不会将它变成
独立随机抽样。`b_geometry` 使用 B 的评分降序、ID 升序排名，记录评分版本与权重。
B 的模型仍使用已知杯身、理想圆柱接触及 URDF COM 等几何近似，尚未针对空心杯、
杯中液体和新动作完成物理验证，因此仍称 **实验性几何参考**，不能冒充正式 M1。
`prepare-b` 的评分模型为 `assets/mug.urdf`，序列执行使用 `assets/mug_hollow.urdf`；
当前评分不包含液体的质心变化。来源校验还会逐项比对报告与候选分数、权重和配置
指纹，防止只替换出处权重或报告文件造成评分来源错配。
上述旧 `prepare-b` 示例没有接入 M2。第三轮已实现的任务风险 M2 由 oracle 流程运行，
与这些旧实验几何示例分开；当前评估入口见第 12 节。

### 完整序列的输出和统计分母

完整序列独立使用 `grasp_c_sequence_run_v1` manifest、
`evaluation_sequence_trial_v1` trial 和 `grasp_c_sequence_summary_v1` summary。
manifest 保存 `hollow_cup_sequences_v1 / staged_sequences_v1 / physical_outcomes_v1`、
规范化 `sequence_config`、每任务 `task_spec` 和指纹、候选快照、B 来源、模型身份及
媒体选项。`sequence_config: {}` 会在校验时展开为 A 的默认值；正式比较前应冻结。

| 输出 | 内容 |
|---|---|
| `filters/TASK/seedN.json` | 当前 task/seed 的共享筛选、身份或异常 |
| `trials/METHOD/TASK/seedN_repeatR.json` | 筛选、选择/原始模型回答、完整 A episode 与原始执行状态 |
| `summary.json` | 按方法/任务的选择指标、物理检查、阶段结果、数值有效性与配对比较 |
| `attempts/`、`media/` | 显式重试的历史记录，以及启用时的录像/截图 |

`completed` 只代表 A 走完动作，成功必须读取 `episode.success`。汇总保留 A 的
`completed/rejected/execution_failed/simulation_error/simulation_invalid`，并分别保留
C 的空候选、筛选异常、选择异常、非法输出和执行调用异常。流体数值异常不能当成
碰撞、掉落或真实任务失败；没有明确数值有效证据的记录单独统计为 unknown。

| 指标 | 分母 |
|---|---|
| `rates.conditional_task_success` | 数值有效且状态为 `completed` 的 episode |
| `rates.task_success_including_execution_failures` | 数值有效的 `completed` 和 `execution_failed` episode |
| `rates.end_to_end_success_coverage` | 全部计划 trial，显示缺失、筛选和调用失败的影响 |
| `rates.numerical_validity` | 已返回且非 `rejected` 的 A episode |
| `physical_flags`、阶段/任务证据 | 仅数值有效的 `completed/execution_failed`；各布尔检查还报告实际观测数 |

每个 rate 同时输出 numerator、denominator 和 rate；没有样本时保持 null，不补零。
放置统计桌面独立支撑、供体分离及位置误差；交接统计接收器支撑、供体分离和高度；
倒水统计转移率、撒漏率、质量及数值状态。模型有效性、修复/回退、缓存与 token
仍使用独立的选择统计分母，物理成功不会被当成模型回答有效的证明。

方法间配对只比较相同 task/seed/repeat、同候选/任务定义/观测/版本和 manifest，
且双方均数值有效的记录；配对分母为 `paired_numerically_valid_n`。其余记录按原因
列入排除统计，不能把旧准备任务或不同数值设置补到新任务的成功样本中。

### 本次集成验证

2026-10-24，使用 B 默认 12 条评分候选、三种完整任务及 seed 0，执行 B 随机、
实验几何、Codex M3/A1 共 12 条试验。六次模型请求全部为关闭缓存的新回答，无
修复和几何回退。M3、A1、几何参考各 3/3 物理成功；随机基线 2/3 成功，倒水的
`tilt_ik_failed` 按 `execution_failed` 保留。倒水成功样本的转移率为 0.9921875，
撒漏率为 0.0078125，数值有效。完成后的 `--resume` 验证没有新增模型、筛选或
物理执行；旧 prep+B 的 4 条试跑也全部成功。

可追踪摘要见 [`validation.json`](../results/c_ab_integration_20261024/validation.json)，
其中包含原始回答、候选和任务指纹、完整 trial 的哈希及恢复验证。这里只证明集成
可用，单 seed 不能支持方法优劣结论；正式比较仍需冻结评分、任务标签和未见 seed。

## 12. 第三轮冻结表上的真实 M3/A1 评估

`grasp_c.table_evaluation` 只读第三轮已有的可行列表与物理结果，向模型提供同一份
公开五字段和任务描述，保存完整原始回答、调用/修复/回退、token 与耗时，然后按
选中 ID 查表评分。M3 保留任务信息，A1 移除任务信息；默认模型沿用现有配置
`gpt-5.6-luna / low`，通过已有 ChatGPT 登录调用。模型名称是请求 ID。

```bash
python -m grasp_c validate --config configs/c.r3.codex.json
python -m grasp_c.table_evaluation run --config configs/c.r3.codex.json --source results/oracle_20261103/r3_v3p_wide_strict_v2 --out results/local/c_r3 --workers 4
python -m grasp_c.table_evaluation run --config configs/c.r3.codex.json --source results/oracle_20261103/r3_v3p_wide_strict_v2 --out results/local/c_r3 --workers 4 --resume
python -m grasp_c.table_evaluation pbf --out results/local/c_r3 --workers 6
python -m grasp_c.table_evaluation summarize --out results/local/c_r3
```

每个方法、任务、seed 采样一次，共 120 条模型选择。每条只允许一次格式修复，仍
失败才按既有几何回退规则选择；回退参与系统成功率，同时另报排除回退后的成功率。
默认关闭响应缓存，已保存 trial 的 `--resume` 不重调用模型，也不自动重试错误。
改变模型、参数、源码、资产、环境或输入时拒绝恢复；新实验应使用新目录。

`pbf` 对五种基线和两种 LLM 的倒水选择按 `(seed, candidate_id)` 去重，在本次
冻结代码与本机环境下使用默认 256 粒子、8 次迭代、2 个子步重新执行。完整 episode
保存在 `pbf/seedN/ID.json`，包含数值有效性、动作阶段与物理判据；不将历史精简
PBF 记录中缺失的数值状态补成有效。已保存的物理失败同样保留，续跑只补缺失项。
`pbf_manifest.json` 独立记录这一阶段的执行代码指纹，并关联选择阶段 manifest。
因此可在保留模型回答的前提下修复 C 的结果校验，但 A/B、资产、输入与环境必须
保持一致；已有 PBF 结果的继续执行还要求该阶段自身代码指纹不变。

输出的 `manifest.json` 冻结配置、provider 身份、代码/资产、包版本、历史源文件
逐文件 SHA-256；`trials/` 保存提示词与响应审计；`summary.json` 和 `report.md`
给出各任务/方法的计划数、有效物理分母、缺失/排除数、成功率、Wilson 95% 区间，
以及按同一 seed 配对的精确检验与整组比较的 Holm 校正。倒水代理与 PBF 单独列出，
没有全量真实流体表就不报告 PBF Oracle。数值异常与执行调用异常不算物理失败。

兼容旧数据时先校验候选和族文件的原始字节哈希，再校验候选规范化哈希。Python
3.12 改变浮点 `sum`，历史四元数归一化与本机可能相差约 1e-16；程序仅接受能按
历史顺序求和精确复现的哈希，并在 manifest 中同时保存历史和本机指纹，不做
任意舍入匹配，也不改变 A 的在线协议。

这仍是回顾性仿真 pilot：seed 0–9 用于 M4 建表，10–29 虽与训练分离，但其结果
已在上游第三轮报告中分析，不能称完全盲测。`noisy_truth` 场景由模拟真值加噪声生成
位姿估计，不是真实视觉感知。静态分数只支持杯身类，杯沿/把手的零分不能解释成
物理上不可抓。一次采样无法估计模型重复调用的稳定性，且本轮只评估一个真实模型。
