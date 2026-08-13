from nanochat.gpt import GPT, GPTConfig


def test_generate_with_top_k_zero_produces_valid_tokens():
    config = GPTConfig(vocab_size=150, n_layer=2, n_head=2, n_kv_head=2, n_embd=32)
    model = GPT(config)
    tokens = list(model.generate([1, 2, 3], max_tokens=3, top_k=0, temperature=0.0))
    assert len(tokens) == 3
    assert all(0 <= token < 150 for token in tokens)
