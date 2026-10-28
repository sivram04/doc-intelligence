"""Phase 4: QLoRA fine-tune of Qwen2.5-1.5B-Instruct for field extraction.

Runs as a Kaggle GPU notebook (T4, free tier) via `scripts/kaggle_train.sh`.
Input: the docintel SFT dataset (train.jsonl / val.jsonl, chat format).
Outputs in /kaggle/working:
  adapter/            LoRA weights
  model-q8_0.gguf     merged model for Ollama / llama.cpp
  metrics.json        config, data hash, train/val loss, runtime
Loss is on the assistant answer only (prompt-completion format).
"""
import glob
import hashlib
import inspect
import json
import math
import os
import subprocess
import sys
import time

# Kaggle gives 2x T4; one is plenty for 1.5B and SFTTrainer refuses nn.DataParallel.
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "trl>=0.19", "peft>=0.15", "bitsandbytes>=0.45",
                "gguf", "sentencepiece", "protobuf"], check=True)
# Kaggle ships torchao 0.10, which current PEFT rejects at merge time; LoRA merge does not need it.
subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "-q", "torchao"], check=False)

import torch
from datasets import load_dataset
from peft import LoraConfig, PeftModel, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, set_seed
from trl import SFTConfig, SFTTrainer

CFG = {
    "base_model": "Qwen/Qwen2.5-1.5B-Instruct",
    "seed": 42,
    "epochs": 2,
    "lr": 2e-4,
    "lora_r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "batch_size": 4,
    "grad_accum": 4,
    "max_length": 2048,
}
OUT = "/kaggle/working"
t0 = time.time()
set_seed(CFG["seed"])

data_dir = os.path.dirname(glob.glob("/kaggle/input/**/train.jsonl", recursive=True)[0])
with open(f"{data_dir}/train.jsonl", "rb") as f:
    data_hash = hashlib.sha256(f.read()).hexdigest()


def to_prompt_completion(ex):
    return {"prompt": ex["messages"][:2], "completion": ex["messages"][2:]}


ds = load_dataset("json", data_files={"train": f"{data_dir}/train.jsonl", "val": f"{data_dir}/val.jsonl"})
ds = ds.map(to_prompt_completion, remove_columns=ds["train"].column_names)

tok = AutoTokenizer.from_pretrained(CFG["base_model"])
bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                         bnb_4bit_compute_dtype=torch.float16)
model = AutoModelForCausalLM.from_pretrained(CFG["base_model"], quantization_config=bnb, device_map={"": 0},
                                             dtype=torch.float16)
model = prepare_model_for_kbit_training(model)

lora = LoraConfig(r=CFG["lora_r"], lora_alpha=CFG["lora_alpha"], lora_dropout=CFG["lora_dropout"],
                  target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
                  task_type="CAUSAL_LM")
steps = math.ceil(len(ds["train"]) / (CFG["batch_size"] * CFG["grad_accum"])) * CFG["epochs"]
sft_kwargs = {
    "output_dir": f"{OUT}/ckpt", "num_train_epochs": CFG["epochs"], "learning_rate": CFG["lr"],
    "per_device_train_batch_size": CFG["batch_size"], "per_device_eval_batch_size": CFG["batch_size"],
    "gradient_accumulation_steps": CFG["grad_accum"], "lr_scheduler_type": "cosine",
    "warmup_steps": max(1, steps // 20),
    "logging_steps": 10, "eval_strategy": "epoch", "save_strategy": "no", "fp16": True,
    "max_length": CFG["max_length"],
    "gradient_checkpointing": True, "completion_only_loss": True, "report_to": "none", "seed": CFG["seed"],
}
# TRL/transformers rename args between releases: drop unknown optional ones, but never the ones
# that change what is trained.
accepted = set(inspect.signature(SFTConfig.__init__).parameters)
dropped = sorted(set(sft_kwargs) - accepted)
required = {"output_dir", "num_train_epochs", "learning_rate", "max_length", "completion_only_loss"}
if required & set(dropped):
    raise RuntimeError(f"SFTConfig no longer accepts required args: {sorted(required & set(dropped))}")
print("SFTConfig: dropped unsupported args", dropped)
args = SFTConfig(**{k: v for k, v in sft_kwargs.items() if k in accepted})
trainer = SFTTrainer(model=model, args=args, train_dataset=ds["train"], eval_dataset=ds["val"],
                     processing_class=tok, peft_config=lora)
# T4 has no bf16; keep the trainable LoRA weights in fp32 so the fp16 grad scaler can unscale them.
for p_ in trainer.model.parameters():
    if p_.requires_grad:
        p_.data = p_.data.float()
train_out = trainer.train()
eval_out = trainer.evaluate()
trainer.model.save_pretrained(f"{OUT}/adapter")
tok.save_pretrained(f"{OUT}/adapter")
history = [h for h in trainer.state.log_history if "loss" in h or "eval_loss" in h]

# Merge LoRA into an fp16 base and convert to GGUF (q8_0 needs no llama.cpp build).
del model, trainer
torch.cuda.empty_cache()
base = AutoModelForCausalLM.from_pretrained(CFG["base_model"], dtype=torch.float16, device_map={"": 0})
merged = PeftModel.from_pretrained(base, f"{OUT}/adapter").merge_and_unload()
merged.save_pretrained("/tmp/merged", safe_serialization=True)
tok.save_pretrained("/tmp/merged")
subprocess.run(["git", "clone", "--depth", "1", "https://github.com/ggml-org/llama.cpp", "/tmp/llama.cpp"], check=True)
subprocess.run([sys.executable, "/tmp/llama.cpp/convert_hf_to_gguf.py", "/tmp/merged",
                "--outtype", "q8_0", "--outfile", f"{OUT}/model-q8_0.gguf"], check=True)

metrics = {
    "config": CFG, "train_data_sha256": data_hash, "train_examples": len(ds["train"]),
    "val_examples": len(ds["val"]), "train_loss": train_out.training_loss, "val_loss": eval_out["eval_loss"],
    "history": history, "runtime_min": round((time.time() - t0) / 60, 1),
    "gpu": torch.cuda.get_device_name(0),
}
with open(f"{OUT}/metrics.json", "w") as f:
    json.dump(metrics, f, indent=2)
print(json.dumps({k: v for k, v in metrics.items() if k != "history"}, indent=2))
