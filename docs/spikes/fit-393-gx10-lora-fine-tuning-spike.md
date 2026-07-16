# FIT-393: GX10 LoRA/QLoRA feasibility spike

Date: 2026-07-16  
Scope: research only; no training run was performed  
Decision: **NO-GO on building a production pipeline now; conditional GO for one local feasibility probe after the production-prompt benchmark is ready.**

## Summary and recommendation

The ASUS Ascent GX10 is plausibly capable of parameter-efficient fine-tuning in its 128 GB unified memory, and NVIDIA now publishes DGX Spark LoRA and QLoRA playbooks for the same GB10/ARM64 platform. That proves the platform class can fine-tune models; it does **not** prove that the exact Qwen3.6-35B-A3B or Qwen3-VL-30B-A3B model, target-module map, trainer, and LM Studio artifact path work end to end.

The right decision is therefore:

1. Finish the production-prompt benchmark work and record the prompt-only ceiling first.
2. Run a disposable, local-only GX10 probe against a small owner-authored dataset.
3. Proceed to a pipeline only if the exact model can train, merge/convert, load in LM Studio, and clear the evaluation gate below.

Expected effort is **1-2 engineering days** for that feasibility probe and **another 3-5 days** for a reproducible local data/evaluation pipeline. Expected gain is **unknown today** because the production prompts have not yet been baselined on the expanded weightlifting set. A reasonable adoption bar is a repeatable 5 percentage-point absolute gain on a targeted benchmark task with no schema, safety, latency, or fallback regression. If prompt optimization already saturates the benchmark, fine-tuning has no demonstrated value and remains a no-go.

## Evidence rules

The labels used below are:

- **Proven-official:** the responsible vendor or maintainer explicitly documents the claim.
- **Plausible-compatible:** required components appear compatible, but no source proves the exact end-to-end combination.
- **Unverified:** a required exact-model or exact-version claim has not been established.

Source authority is intentionally narrow: this repository proves current app behavior; NVIDIA and ASUS prove hardware/platform capability; each software maintainer proves its own compatibility. Individually compatible components do not establish an end-to-end training or serving path. Conflicts and missing version evidence remain unverified.

## Current system evidence

- The app's operational metrics table stores no prompt or completion text (`docs/prd/11-ai-coach-recommendations.md:262-267`), but the adjacent `adjust_cache.response_json` does persist structured model output (`app.py:10275-10405`). Benchmark output also contains model estimates when stdout is captured (`support/meal_model_benchmark.py:1038-1065`). Neither is an authorized training corpus; both are explicitly excluded below.
- Raw food photos are discarded after extraction (`docs/VISION.md:107-108`). This spike does not change that commitment.
- The text adapter sends compact context to a configurable primary/fallback LM Studio endpoint and accepts only a strict intent patch; Python remains the deterministic decision and safety layer (`lm_studio_adapter.py:1-12,26-35`).
- The checked-in defaults are not one canonical model declaration: the text primary defaults to `qwen/qwen3-30b-a3b-2507`, its fallback defaults to `qwen/qwen3.6-35b-a3b`, and the vision adapter defaults to `qwen3-vl-30b-a3b-instruct@q4_k_xl` (`lm_studio_adapter.py:30-35`; `local_vision_adapter.py:39-53`). Runtime configuration must be captured on the GX10 before a probe; issue text is configuration evidence, not architecture proof.
- The benchmark keeps cases in source control and allows private photos to be attached only at runtime (`support/meal_model_benchmark.py:2-7`). Its candidate gate already requires every selected case to be schema-valid, quality-passing, and within route/task latency limits (`support/meal_model_benchmark.py:1193-1217`).

## Hardware and platform support

| Claim | Status | Evidence and consequence |
| --- | --- | --- |
| DGX Spark is an ARM64 GB10 system with 128 GB shared LPDDR5x memory. | **Proven-official** | NVIDIA's [DGX Spark porting guide](https://docs.nvidia.com/dgx/dgx-spark-porting-guide/overview.html) describes a 20-core ARM64 SoC and 128 GB shared memory. This is evidence for the GB10 reference platform, not automatically for every ASUS image or installed package version. |
| ASUS Ascent GX10 uses GB10 and 128 GB unified memory. | **Proven-official** | ASUS lists the ARM v9.2-A GB10 and 128 GB unified memory in the [Ascent GX10 technical specification](https://www.asus.com/us/networking-iot-servers/desktop-ai-supercomputers/asus-ascent-gx10/techspec/). The Ubuntu SKU and NVIDIA DGX OS variant are distinct system images; record the actual model, firmware, OS image, and memory visible on the owner's unit. |
| Current DGX Spark software is an Ubuntu-derived ARM stack with CUDA and PyTorch. | **Proven-official** | NVIDIA's [release notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html) list DGX OS 7.5.0, driver 580.159.03, CUDA 13.0.2, and the current Spark PyTorch/Jupyter environment. The [porting guide](https://docs.nvidia.com/dgx/dgx-spark-porting-guide/overview.html) identifies Ubuntu 24.04 LTS. These versions are the reference stack to pin; the GX10 must be checked rather than assumed identical. |
| GB10 can run local LoRA/QLoRA jobs. | **Proven-official for NVIDIA's documented recipes** | NVIDIA's [PyTorch playbook](https://build.nvidia.com/spark/pytorch-fine-tune/instructions) demonstrates Llama LoRA/QLoRA. Its [NeMo AutoModel playbook](https://build.nvidia.com/spark/nemo-fine-tune/instructions) demonstrates Llama LoRA/QLoRA and Qwen3-8B full SFT. Neither demonstrates LoRA/QLoRA for the app's exact 30B/35B MoE models. |
| QLoRA's bitsandbytes dependency has ARM64 Blackwell binaries. | **Proven-official at the component level** | The [bitsandbytes installation matrix](https://huggingface.co/docs/bitsandbytes/installation) lists Linux aarch64 wheels for CUDA 12.8-13.0 including `sm121`, plus NF4/FP4 and 8-bit optimizers. This does not prove the exact Qwen MoE target mapping. |
| The complete exact-model training stack is supported. | **Unverified** | No primary source found here demonstrates Qwen3.6-35B-A3B or Qwen3-VL-30B-A3B LoRA/QLoRA, on GB10, through merge/conversion, then LM Studio. Missing any one of those probe results keeps the pipeline at conditional no-go. |

### Minimum probe stack

Pin the GX10's installed versions rather than installing a speculative parallel stack. The strongest published reference is [Unsloth's DGX Spark recipe](https://unsloth.ai/docs/blog/fine-tuning-llms-with-nvidia-dgx-spark-and-unsloth): NVIDIA PyTorch 25.09/CUDA 13.0, a pinned Triton checkout, xformers built for compute capability 12.1, bitsandbytes 0.48.0, Transformers 4.56.2, and TRL 0.22.2. That recipe is **Proven-official** for Unsloth on DGX Spark, but still **Unverified** for the owner's ASUS image and exact Qwen MoE. Record image digests and Python package locks in the private probe log. Do not move to CUDA 13.2/13.3 containers on the stock driver merely because they are newer; their documented driver floors are higher than the current Spark 580-series driver.

Before using the stack, verify `uname -m`, OS release, driver/CUDA versions, PyTorch version and CUDA availability, `torch.cuda.get_device_capability()`, bitsandbytes import/4-bit load, and the exact Hugging Face model revision. A component failure is a stop condition, not permission to substitute an unsourced wheel.

## Candidate toolchains

| Toolchain | Platform evidence | Exact-model status | Recommendation |
| --- | --- | --- | --- |
| NVIDIA PyTorch Spark playbook + Transformers/PEFT/TRL | **Proven-official** for Spark LoRA/QLoRA; Hugging Face documents [TRL's PEFT integration](https://huggingface.co/docs/trl/en/peft_integration) and [MoE parameter targeting](https://huggingface.co/docs/peft/main/package_reference/lora). | **Unverified** for Qwen3.6's packed MoE parameters and vision encoder. | Best first probe because NVIDIA owns the platform recipe; begin with language-only, attention-only LoRA. |
| NVIDIA NeMo AutoModel | **Proven-official** Spark recipes include Llama LoRA/QLoRA and Qwen3-8B full SFT. | **Unverified** for Qwen3.6-35B-A3B/Qwen3-VL-30B-A3B LoRA/QLoRA. | Second choice only if the exact architecture appears in a pinned NeMo support matrix. |
| Unsloth | **Proven-official** for its pinned [DGX Spark recipe](https://unsloth.ai/docs/blog/fine-tuning-llms-with-nvidia-dgx-spark-and-unsloth). | **Unverified** for this Qwen3.6 MoE configuration and the owner's ASUS image. | Strongest first reference stack, still gated by an exact-model dry load and one-step save/reload probe. |
| Axolotl | General LoRA/QLoRA support is not proof of an ARM64/GB10 exact-model stack. | **Unverified**. | Exclude from the first probe unless its maintainers document the exact ARM64/Blackwell combination. |

## Memory math

The base model must be resident by **total** parameters; MoE active parameters reduce per-token compute, not stored weights. Qwen's official model cards report [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) as 35B total/3B active and [Qwen3-30B-A3B](https://huggingface.co/Qwen/Qwen3-30B-A3B) as 30.5B total/3.3B active. The 35B model's published config has 40 layers, hidden size 2,048, 256 experts, 8 routed experts per token, and expert intermediate size 512 ([config](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/main/config.json)).

### Base residency

| Base | Raw BF16 weights | Raw 4-bit weights | Planning budget |
| --- | ---: | ---: | --- |
| 35B total | 70 GB (65.2 GiB) | 17.5 GB (16.3 GiB) | 18-24 GB for 4-bit weights plus scales/metadata/allocator fragmentation; measure rather than assume. |
| 30.5B total | 61 GB (56.8 GiB) | 15.25 GB (14.2 GiB) | 16-22 GB on the same basis; vision activations and encoder state are additional. |
| Qwen3-VL-30B-A3B-Instruct checkpoint | 62.2 GB published repository size | 15.5 GB raw 4-bit lower bound | No fit claim: the official [model card](https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct) and [config](https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct/blob/main/config.json) pin a 27-layer, hidden-size-1,152 vision encoder in addition to the MoE text stack. |

Hugging Face documents that nested quantization can save a further 0.4 bits/parameter ([bitsandbytes quantization guide](https://huggingface.co/docs/transformers/main/quantization/bitsandbytes)); the table deliberately does not spend that saving before a measured load. As a cross-check rather than an estimate, Qwen's official [Qwen3-30B-A3B GGUF repository](https://huggingface.co/Qwen/Qwen3-30B-A3B-GGUF) publishes a Q4_K_M artifact of about 18.6 GB.

### Adapter and optimizer residency

For a weight shaped `d_out x d_in`, LoRA adds `r * (d_in + d_out)` trainable parameters. PEFT confirms that count depends on rank and matrix shape and that MoE models may require `target_parameters`, not ordinary module replacement ([PEFT LoRA reference](https://huggingface.co/docs/peft/main/package_reference/lora)).

Two Qwen3-30B-A3B bounds derived from its official config and Transformers implementation show why target selection matters:

- `q/k/v/o` across all 48 layers is `835,584 * r`: about 13.4M trainable parameters at rank 16.
- Its fused expert tensors are `gate_up_proj[128,1536,2048]` and `down_proj[128,2048,768]`. A naive expert rank 16 would add about 629M parameters. PEFT's [Qwen3-MoE guidance](https://huggingface.co/docs/peft/main/package_reference/lora) recommends dividing effective expert rank by the expert count; rank 16 therefore bottoms out at rank 1 per expert, or about 39.3M expert-adapter parameters. The exact Qwen3.6 packed layout and selected targets must be counted independently.

Budget 12-16 bytes per trainable adapter parameter for BF16 adapter weights and gradients plus FP32 AdamW moments and, where used, FP32 master weights. The Qwen3 attention-only rank-16 example is therefore roughly 160-215 MB of train state; the expert rank-1 example is roughly 470-630 MB. These are adapter-state bounds, not total training-memory claims. The exact count must come from `print_trainable_parameters()` after injection against the pinned model revision.

### Activations, cache, and unified-memory headroom

Training activations are sequence-, batch-, kernel-, and checkpointing-dependent and cannot be inferred from the 3B active count alone. Inference KV cache is a separate budget; training should disable `use_cache`, so it is not added as though it were a training allocation. Use batch 1, sequence 1,024, BF16 compute, and gradient checkpointing for the first probe, then record `torch.cuda.max_memory_allocated()` and `max_memory_reserved()`.

For serving the Qwen3-30B-A3B reference model, the published 48 layers, 4 KV heads, and 128-dimensional heads give a BF16 KV-cache lower-bound formula of `2 * 48 * 4 * 128 * 2 bytes * tokens`: about 3.0 GiB at 32,768 tokens and 12.0 GiB at 131,072 tokens, before allocator/runtime overhead. That inference budget is intentionally excluded from the training estimate.

Vision fine-tuning is outside the first feasibility probe. Its minimum activation worksheet must add, at each vision stage, `batch * image_tokens * 1,152 * activation_bytes`, plus intermediate/attention tensors across 27 vision layers and the text-stack activations. `image_tokens` is dynamic, so a future vision probe must pin the processor revision, min/max pixels, image-grid output, and representative phone-photo dimensions, then measure peak memory. Until that worksheet and a one-step run exist, the vision model remains an explicit no-go rather than inheriting the text-model fit conclusion.

The measurable headroom equation is:

`128 GB - display reservation (2 or 4 GB) - observed idle OS/runtime use - quantized base - adapter/train state - measured activations/workspaces`.

NVIDIA's current [release notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html) document the 2/4 GB display reservation and unified-memory OOM behavior. Do not run LM Studio concurrently with training. The probe passes memory feasibility only if the measured peak stays below 90% of memory available after the observed idle baseline and can complete twice without OOM. Static arithmetic alone is not a fit claim.

## Training data provenance and privacy

There is no permission to turn production traffic into training data. The following are prohibited: production prompts, captured model completions, user-derived meal/workout text, extracted photo descriptions, raw photos, `adjust_cache.response_json`, captured benchmark estimates, or records reconstructed from logs/caches.

Allowed sources are deliberately narrow:

1. Source-controlled benchmark scenarios, copied by stable case ID and commit.
2. New owner-authored inputs and owner-authored target JSON. A target written by the owner is not a retained model completion.
3. Synthetic pairs generated locally from non-personal templates, then reviewed and corrected by the owner before inclusion.

Each pair needs a local manifest with pair ID, source category, author/generator, source revision, license, review status, and split assignment. Synthetic generation stays on the owner's machines. If an external service would be required, that data path is out of scope and the pair is excluded rather than “de-identified” ad hoc. No personal photos or photo-derived records enter a dataset; private images may still be supplied transiently to the existing benchmark exactly as they are today.

Run probes under an isolated disposable `DATA_DIR`, with LM Studio request/history logging disabled if the installed version exposes that control. Redirect benchmark output only into the disposable probe directory. On completion, verify that no raw image was copied, unload the probe model, and remove the isolated cache DB, benchmark output, temporary image mappings, trainer checkpoints not selected for evaluation, and application/request logs. If LM Studio's installed version cannot prove or control request retention, use a disposable local server/profile for the probe and delete it afterward; do not point training tooling at the production LM Studio history.

## Evaluation gate

The gate cannot be run until the benchmark uses the exact shipped production prompts and the expanded weightlifting cases are merged. Pin the repository commit, model revision, prompt revision, case IDs, image map, sampler settings, and LM Studio/runtime versions. Use a never-trained holdout with at least 20 cases for the targeted task; every task included in the decision must have a nonempty holdout. Run baseline and candidate three times in alternating order.

A tuned artifact may serve only when all of these hold:

1. `candidate_passed` is true on every run: 100% schema validity, 100% existing per-case quality checks, and every existing route/task latency gate passes.
2. For each case, reduce the three repeats to a majority pass/fail, then compare baseline and candidate on the same cases. On the targeted task, the candidate improves majority quality-pass rate by at least 5 percentage points, no other task declines, and a paired bootstrap confidence interval (10,000 resamples, fixed seed, case as the resampling unit) has a lower bound above zero. Report wins, losses, and ties by case.
3. No baseline-passing case becomes a candidate failure in any repeat, and deterministic validation rejects the same malformed/adversarial fixtures.
4. Median paired per-case latency remains inside the checked-in limits and does not exceed 110% of the baseline median; there is no undefined “noise” exception.
5. The primary endpoint failure, timeout, malformed JSON, and model-unavailable tests still route to the existing deterministic/manual pending-review fallback.

Report per-case results and the range across repeats, not only a mean. If both models saturate the binary checks, the benchmark cannot prove gain; add discriminating owner-authored holdout cases before making a serving decision. Never lower the gate to justify the tuned model.

## Serving and rollback

LM Studio on DGX Spark and the stock Qwen3.6 model are **Proven-official** through NVIDIA's [LM Studio Spark playbook](https://build.nvidia.com/spark/lm-studio/overview). LM Studio officially supports importing a local GGUF with [`lms import`](https://lmstudio.ai/docs/cli/local-models/import) and dry-run resource estimation/loading with [`lms load`](https://lmstudio.ai/docs/cli/local-models/load). Direct PEFT-adapter loading in LM Studio was **not verified**.

The conditional artifact path is therefore:

1. Train a PEFT adapter against an exact, hashed base revision.
2. Merge into that base only with the trainer/PEFT-supported merge path; preserve the tokenizer, processor, generation config, special tokens, and chat template. Confirm the Apache-2.0 base license and carry notices with the derivative.
3. Convert the merged Hugging Face checkpoint to GGUF only if the pinned llama.cpp converter recognizes the exact Qwen architecture; quantize the converted base with the pinned llama.cpp build. The existence of llama.cpp's [`convert_lora_to_gguf.py`](https://github.com/ggml-org/llama.cpp/blob/master/convert_lora_to_gguf.py) and NVIDIA's [Qwen3.6 inference playbook](https://build.nvidia.com/spark/llama-cpp/overview) does not by itself prove conversion of this merged adapter.
4. Validate tokenizer/chat-template parity, schema outputs, benchmark results, and a checksum; then `lms import --copy --user-repo owner/fit-393-tuned <artifact.gguf>` and `lms load --estimate-only` before a real load.
5. Load the tuned model under a new identifier. Do not overwrite or unload the shipped model until the gate passes.

Rollback is configuration-only: point `LM_STUDIO_PRIMARY_MODEL` back to the shipped model identifier, keep the existing fallback endpoint/model, reload, and rerun one known-good schema case plus one forced-primary-failure case. If merge, exact-architecture conversion, import, or rollback validation is unverified, LM Studio deployment is a no-go; a vLLM test endpoint may be used for the feasibility probe but does not satisfy the LM Studio acceptance path.

## Go/no-go, effort, and expected gain

**Current verdict: NO-GO for pipeline implementation. Conditional GO for a disposable local feasibility probe.**

The hardware and component-level QLoRA support are credible. The blockers are exact-model MoE adapter targeting, measured activation headroom, a verified merged-artifact conversion path into LM Studio, and—most importantly—an observed prompt-only benchmark ceiling. The probe should stop at the first failed blocker and leave no service/config changes behind.

Estimated effort:

- 1-2 days: pin the GX10 stack; dry-load 4-bit weights; inject/count a narrow adapter; run a tiny local SFT; merge; attempt exact conversion/import; collect memory and rollback proof.
- 3-5 additional days only after that succeeds: create/review the local dataset and manifest, automate repeatable baseline/candidate evaluation, and package a reversible local runbook.
- Ongoing: owner review for new target pairs and benchmark drift.

Expected gain relative to prompt optimization is **not yet measurable**. Fine-tuning is worth continuing only if prompt optimization leaves a stable, material benchmark gap and the tuned candidate clears every gate above. Otherwise the expected gain is effectively zero relative to its operational cost.

## Sources

- [NVIDIA DGX Spark system and porting guide](https://docs.nvidia.com/dgx/dgx-spark-porting-guide/overview.html)
- [NVIDIA DGX Spark release notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html)
- [NVIDIA PyTorch fine-tuning playbook](https://build.nvidia.com/spark/pytorch-fine-tune/instructions)
- [NVIDIA NeMo AutoModel fine-tuning playbook](https://build.nvidia.com/spark/nemo-fine-tune/instructions)
- [NVIDIA llama.cpp Qwen3.6 playbook](https://build.nvidia.com/spark/llama-cpp/overview)
- [NVIDIA LM Studio on DGX Spark playbook](https://build.nvidia.com/spark/lm-studio/overview)
- [ASUS Ascent GX10 technical specification](https://www.asus.com/us/networking-iot-servers/desktop-ai-supercomputers/asus-ascent-gx10/techspec/)
- [Unsloth DGX Spark fine-tuning recipe](https://unsloth.ai/docs/blog/fine-tuning-llms-with-nvidia-dgx-spark-and-unsloth)
- [Qwen3.6-35B-A3B model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)
- [Qwen3.6-35B-A3B config](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/main/config.json)
- [Qwen3-30B-A3B model card](https://huggingface.co/Qwen/Qwen3-30B-A3B)
- [Qwen3-30B-A3B official GGUF repository](https://huggingface.co/Qwen/Qwen3-30B-A3B-GGUF)
- [Qwen3-VL-30B-A3B-Instruct model card](https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct)
- [Qwen3-VL-30B-A3B-Instruct config](https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct/blob/main/config.json)
- [Hugging Face bitsandbytes installation matrix](https://huggingface.co/docs/bitsandbytes/installation)
- [Hugging Face bitsandbytes quantization guide](https://huggingface.co/docs/transformers/main/quantization/bitsandbytes)
- [Hugging Face TRL PEFT integration](https://huggingface.co/docs/trl/en/peft_integration)
- [Hugging Face PEFT LoRA reference](https://huggingface.co/docs/peft/main/package_reference/lora)
- [LM Studio local GGUF import](https://lmstudio.ai/docs/cli/local-models/import)
- [LM Studio model loading and resource estimate](https://lmstudio.ai/docs/cli/local-models/load)
- [llama.cpp LoRA-to-GGUF converter](https://github.com/ggml-org/llama.cpp/blob/master/convert_lora_to_gguf.py)
