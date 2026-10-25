# Related work and remaining hypotheses

This document distinguishes verified prior-work scope from untested Explorer hypotheses. It is not a statement of worldwide priority.

* [ACT](https://arxiv.org/abs/2304.13705) already learns action chunks for low-cost bimanual manipulation. Using ACT, or building a recording interface around it, is reuse rather than an original policy algorithm.
* [TRANSIC](https://arxiv.org/abs/2405.10315) already combines a simulation policy with human corrections through residual learning. Its action-space distillation and point-cloud observations are relevant, but its simulation/hardware stack is not automatically compatible with ROSMASTER or NX.
* [UMI](https://arxiv.org/abs/2402.10329) and [LEGATO](https://arxiv.org/abs/2411.03682) already use transferable trajectory/action representations and kinematic adaptation. A raw SO-101 action head cannot therefore be declared a valid cross-embodiment transfer simply because it loads into a model.
* [KITE](https://kite-manip.github.io/) explicitly separates interaction prediction from embodiment-specific kinematic decoding. The inspected author page labels code as forthcoming. It is a close conceptual competitor; no code-tested or hardware-tested status follows from that page.
* [LIBERO](https://arxiv.org/abs/2306.03310) and [LOTUS](https://arxiv.org/abs/2311.02058) already study lifelong transfer, task order, retention and growing skill libraries. A versioned library alone is an engineering resource, not a novel lifelong-learning method.
* [ENPIRE](https://research.nvidia.com/labs/gear/enpire/) already studies agent-driven physical policy improvement with repeatable reset and verification. A generic coding-agent loop cannot be claimed new. Explorer's possible contribution must be isolated through controlled experiments on its specified embodiments, evidence contracts and adaptation mechanism.
* [Wu et al. robot-to-robot handover](https://link.springer.com/article/10.1007/s10514-025-10201-y) studies grasp/exchange-pose planning. Its introduction explicitly leaves the physical passing phase for future study. This does not establish release safety for low-cost arms without force sensing.

The remaining Explorer hypotheses concern measured compatibility-aware source reuse, object/contact-effect adaptation across SO-101 and ROSMASTER, independently verified skill composition, measured retention and identifiable cause-directed repair. None is established by the platform architecture. Each needs a matched strong baseline, component ablation, sealed evaluation and retained negative outcomes.

Bibliographic corrections are recorded in the working registry while preserving the seed. In particular P119's original arXiv identifier resolved to an unrelated fluid-dynamics paper; the working source is the actual ICCV2025 IRASim paper. The robomimic website candidate P016 is a duplicate pointer to P015, not an additional unique research article.
