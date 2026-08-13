import torch

from model import GPT, GPTConfig


def test_crop_block_size_updates_config_and_model_runs():
    model = GPT(GPTConfig(vocab_size=65, block_size=48, n_layer=1, n_head=1, n_embd=8))
    model.crop_block_size(24)
    assert model.config.block_size == 24
    assert model.transformer.wpe.weight.shape[0] == 24
    logits, _ = model(torch.randint(0, 65, (1, 24)))
    assert logits.shape[-1] == 65
