import os
import types
import atexit
from time import perf_counter
from functools import partial
from dataclasses import fields, asdict

import torch
import torch.multiprocessing as mp
from transformers import AutoTokenizer, AutoModelForCausalLM, StoppingCriteria, StoppingCriteriaList
from transformers import RepetitionPenaltyLogitsProcessor
try:    
    from vllm import LLM
    from vllm import SamplingParams as VllmSamplingParams
    from vllm.inputs import TokensPrompt as TokensPrompt
    SUPPORT_VLLM = True
except ImportError:
    SUPPORT_VLLM = False

from soulxpodcast.config import Config, SamplingParams
from soulxpodcast.models.modules.sampler import _ras_sample_hf_engine
from soulxpodcast.utils.device import get_device, llm_dtype


class GenerationProgress(StoppingCriteria):
    """Lightweight console progress for slow CPU/MPS autoregressive runs."""

    def __init__(self, prompt_length: int, max_tokens: int):
        self.prompt_length = prompt_length
        self.every = min(25, max(1, max_tokens // 4))
        self.last_reported = 0

    def __call__(self, input_ids, scores, **kwargs):
        generated = input_ids.shape[-1] - self.prompt_length
        if generated >= self.last_reported + self.every:
            self.last_reported = generated
            print(f"[INFO] Generated {generated} audio tokens...", flush=True)
        return torch.zeros(input_ids.shape[0], dtype=torch.bool, device=input_ids.device)

class HFLLMEngine:

    def __init__(self, model, **kwargs):
        config_fields = {field.name for field in fields(Config)}
        config_kwargs = {k: v for k, v in kwargs.items() if k in config_fields}
        config = Config(model, **config_kwargs)
        
        self.tokenizer = AutoTokenizer.from_pretrained(model, use_fast=True)
        config.eos = config.hf_config.eos_token_id # speech eos token;
        self.device = get_device()
        dtype = llm_dtype(self.device)
        # device_map="mps" is not consistently supported by all Transformers
        # releases, so load first and move explicitly on non-CUDA backends.
        if self.device.type == "cuda":
            self.model = AutoModelForCausalLM.from_pretrained(
                model, dtype=dtype, device_map=str(self.device)
            )
        else:
            self.model = AutoModelForCausalLM.from_pretrained(model, dtype=dtype)
            self.model.to(self.device)
        self.config = config
        self.pad_token_id = self.tokenizer.pad_token_id

    def generate(
        self,
        prompt: list[str],
        sampling_param: SamplingParams,
        past_key_values=None,
    ) -> dict:
        
        stopping_criteria = StoppingCriteriaList([
            GenerationProgress(len(prompt), sampling_param.max_tokens),
        ])
        if sampling_param.use_ras:
            sample_hf_engine_handler = partial(_ras_sample_hf_engine, 
                    use_ras=sampling_param.use_ras, 
                    win_size=sampling_param.win_size, tau_r=sampling_param.tau_r)
        else:
            sample_hf_engine_handler = None
        rep_pen_processor = RepetitionPenaltyLogitsProcessor(
            penalty=sampling_param.repetition_penalty,
            prompt_ignore_length=len(prompt)
        ) # exclude the input prompt, consistent with vLLM implementation;
        with torch.no_grad(): 
            input_len = len(prompt)
            generated_ids = self.model.generate(
                input_ids = torch.tensor([prompt], dtype=torch.int64, device=self.device),
                do_sample=True,
                top_k=sampling_param.top_k,
                top_p=sampling_param.top_p,
                min_new_tokens=sampling_param.min_tokens,
                max_new_tokens=sampling_param.max_tokens,
                eos_token_id=self.config.hf_config.eos_token_id,
                temperature=sampling_param.temperature,
                stopping_criteria=stopping_criteria,
                past_key_values=past_key_values,
                custom_generate=sample_hf_engine_handler,
                use_cache=True,
                logits_processor=[rep_pen_processor]
            )
            generated_ids = generated_ids[:, input_len:].cpu().numpy().tolist()[0]
        output = {
            "text": self.tokenizer.decode(generated_ids),
            "token_ids": generated_ids,
        }
        return output

class VLLMEngine:

    def __init__(self, model, **kwargs):
        
        config_fields = {field.name for field in fields(Config)}
        config_kwargs = {k: v for k, v in kwargs.items() if k in config_fields}
        config = Config(model, **config_kwargs)
        
        self.tokenizer = AutoTokenizer.from_pretrained(config.model, use_fast=True)
        config.eos = config.hf_config.eos_token_id # speech eos token;
        self.device = get_device()
        os.environ["VLLM_USE_V1"] = "0"
        if SUPPORT_VLLM:
            self.model = LLM(model=model, enforce_eager=True, dtype="bfloat16", max_model_len=8192, enable_prefix_caching=True,)
        else:
            raise ImportError("Not Support VLLM now!!!")
        self.config = config
        self.pad_token_id = self.tokenizer.pad_token_id

    def generate(
        self,
        prompt: list[str],
        sampling_param: SamplingParams,
        past_key_values=None,
    ) -> dict:
        sampling_param.stop_token_ids = [self.config.hf_config.eos_token_id]
        with torch.no_grad():
            generated_ids = self.model.generate(
                TokensPrompt(prompt_token_ids=prompt), 
                VllmSamplingParams(**asdict(sampling_param)),
                use_tqdm=False,
            )[0].outputs[0].token_ids
        output = {
            "text": self.tokenizer.decode(generated_ids),
            "token_ids": list(generated_ids),
        }
        return output
