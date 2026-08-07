import sys, torch
from algorithms.coslight.controller import CoSLightNetwork, OBS_DIM, PHASE_FEATURES, PRESSURE_PRIOR_SCALE, HOLD_PRIOR_BIAS
ckpt = sys.argv[1] if len(sys.argv) > 1 else "runs/coslight_parallel/bias_v17_20260804_210726.pt"
data = torch.load(ckpt, map_location="cpu", weights_only=False)
sd = data["model_state_dict"]
m = CoSLightNetwork(num_agents=20, obs_dim=OBS_DIM, act_dim=4, top_k=5, hidden=64)
m.load_state_dict(sd); m.eval()
print("PRESSURE_PRIOR_SCALE =", PRESSURE_PRIOR_SCALE, "HOLD_PRIOR_BIAS =", HOLD_PRIOR_BIAS)
torch.manual_seed(0)
B = 4096
with torch.no_grad():
    enc = torch.randn(B, 20, 64)
    pf = torch.randn(B, 20, 4, 8); pf[..., 6:] = 0.0
    coll = torch.arange(5).view(1, 1, 5).expand(B, 20, 5)
    ctx = m.selected_collaborator_context(enc, coll)
    logits = m._actor_logits(enc, coll, pf, collaborator_context=ctx)
    bias = m.collab_bias(ctx)
    pp = m.pressure_prior_scale * pf[..., 5] + m.hold_prior_bias * pf[..., 6]
    margins = (logits - bias).topk(2, dim=-1).values
    gap = (margins[..., 0] - margins[..., 1]).abs()
    print("logits_abs_mean      =", logits.abs().mean().item())
    print("pressure_prior_abs   =", pp.abs().mean().item())
    print("bias_abs_mean        =", bias.abs().mean().item())
    print("top2_logit_gap_mean  =", gap.mean().item(), " median=", gap.median().item())
    print("gap<0.1 frac         =", (gap < 0.1).float().mean().item())
    print("gap<0.5 frac         =", (gap < 0.5).float().mean().item())
    print("gap<1.0 frac         =", (gap < 1.0).float().mean().item())
