import torch

from model import GPT, GPTConfig


def test_generate_with_top_k_above_vocab_size_produces_valid_tokens():
    torch.manual_seed(0)
    model = GPT(GPTConfig(vocab_size=33, block_size=16, n_layer=1, n_head=1, n_embd=8))
    model.eval()
    idx = torch.randint(0, 33, (1, 4))
    out = model.generate(idx, max_new_tokens=3, top_k=64)
    assert out.shape == (1, 7)
    assert out.min() >= 0 and out.max() < 33
