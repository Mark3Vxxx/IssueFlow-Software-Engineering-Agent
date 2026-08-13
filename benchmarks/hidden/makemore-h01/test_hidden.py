import torch

from makemore import ModelConfig, RNN


def test_rnn_recurrence_propagates_through_multiple_steps():
    config = ModelConfig()
    config.vocab_size = 12
    config.block_size = 6
    config.n_embd = 8
    config.n_embd2 = 8
    model = RNN(config, "gru")
    out1, _ = model(torch.tensor([[5, 7, 9]]))
    out2, _ = model(torch.tensor([[5, 8, 9]]))
    assert not torch.allclose(out1[:, -1], out2[:, -1])
