"""Ablation: does the learned collab_bias give the collaborator message an
independent lever on phase actions? Loads a trained V17 checkpoint and compares
argmax(logits) with bias on/off, bias action-spread, and a random-bias control."""
import sys, torch
from algorithms.coslight.controller import CoSLightNetwork, OBS_DIM, PHASE_FEATURES

ckpt_path = sys.argv[1] if len(sys.argv) > 1 else (
    "runs/coslight_parallel/bias_v17_20260804_210726.pt")
data = torch.load(ckpt_path, map_location="cpu", weights_only=False)
sd = data.get("model_state_dict", data) if isinstance(data, dict) else data
n_agents = 20
act_dim = sd["actor_head.2.weight"].shape[0]      # 4
hidden = sd["collab_bias.0.weight"].shape[1]        # 64
top_k = int(data.get("top_k", data.get("model_config", {}).get("top_k", 5))) if isinstance(data, dict) else 5
print(f"ckpt={ckpt_path} agents={n_agents} act={act_dim} hidden={hidden} top_k={top_k}")

torch.manual_seed(0)
model = CoSLightNetwork(num_agents=n_agents, obs_dim=OBS_DIM, act_dim=act_dim,
                        top_k=top_k, hidden=hidden)
model.load_state_dict(sd)
model.eval()

B = 4096
with torch.no_grad():
    encoded = torch.randn(B, n_agents, hidden)
    phase_features = torch.randn(B, n_agents, act_dim, PHASE_FEATURES)
    phase_features[..., 6:] = 0.0
    collaborators = torch.arange(top_k).view(1, 1, top_k).expand(B, n_agents, top_k)
    ctx = model.selected_collaborator_context(encoded, collaborators)

    logits = model._actor_logits(encoded, collaborators, phase_features,
                                 collaborator_context=ctx)
    bias = model.collab_bias(ctx)
    logits_no_bias = logits - bias
    logits_zero_ctx = model._actor_logits(encoded, collaborators, phase_features,
                                          collaborator_context=torch.zeros_like(ctx))
    rand_bias = torch.randn_like(bias) * bias.abs().mean()
    logits_rand_bias = logits_no_bias + rand_bias

    def argmax_change(a, b):
        return (a.argmax(-1) != b.argmax(-1)).float().mean().item()

    # per-action spread of the learned bias (mean abs max-min over actions)
    spread = (bias.max(-1).values - bias.min(-1).values).abs().mean().item()
    # correlation between bias and the action-relative part it should provide
    bias_centered = bias - bias.mean(-1, keepdim=True)

    print(f"bias_abs_mean            = {bias.abs().mean().item():.6f}")
    print(f"bias_action_spread       = {spread:.6f}  (max-min per agent, mean)")
    print(f"bias_centered_abs_mean   = {bias_centered.abs().mean().item():.6f}")
    print(f"argmax_change bias vs no_bias  = {argmax_change(logits, logits_no_bias):.4f}  (accumulated lever)")
    print(f"argmax_change ctx vs zero_ctx  = {argmax_change(logits, logits_zero_ctx):.4f}  (message-content lever)")
    print(f"argmax_change random bias ctrl = {argmax_change(logits_no_bias, logits_rand_bias):.4f}  (same-magnitude random)")
