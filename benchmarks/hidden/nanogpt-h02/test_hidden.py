import torch

from model import GPT, GPTConfig


def test_eval_mode_deterministic_with_extreme_dropout():
    torch.manual_seed(123)
    model = GPT(GPTConfig(dropout=0.9, block_size=16, n_layer=1, n_head=1, n_embd=8, vocab_size=33))
    model.eval()
    idx = torch.randint(0, 33, (1, 16))
    with torch.no_grad():
        first, _ = model(idx)
        second, _ = model(idx)
    assert torch.allclose(first, second)
