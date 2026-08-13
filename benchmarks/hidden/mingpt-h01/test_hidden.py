import torch
import torch.nn.functional as F

from mingpt.utils import top_k_logits


def test_top_k_logits_gives_zero_probability_to_masked_tokens():
    logits = torch.tensor([[10.0, 9.0, 8.0, 7.0]])
    out = top_k_logits(logits, 2)
    probs = F.softmax(out, dim=-1)
    assert probs[0, 2] == 0.0
    assert probs[0, 3] == 0.0
