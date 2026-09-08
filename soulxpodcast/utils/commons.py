import random
import numpy as np
import torch


def set_all_random_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        # MPS shares the CPU RNG in current PyTorch releases; manual_seed above
        # is sufficient and this branch intentionally avoids CUDA-only calls.
        pass
