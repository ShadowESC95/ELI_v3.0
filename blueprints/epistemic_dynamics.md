# Epistemic dynamics: what ELI's figures mean, and whether they are borne out

ELI had many quantities in 0..1 called confidence that meant different things: how surely a sentence
was routed, how much an agent brought back, how good a reply looked, how far several samples agreed,
how strong a memory still was. The equation audit of October 2026 (the three documents in the owner's
keeping: the audit, the roadmap and the thoughts on it) found the mathematics mostly sound and the
*meanings* overloaded: a ranking score, an evidence priority, a classifier margin and a chance of being
right all lived in the same nominal range. This is the programme that answered it, equation by
equation: keep the simple mathematics that was right (half-life decay, reciprocal rank fusion, cosine,
circular time, HMAC chains), and replace the places where a hand-made index stood in for uncertainty,
evidence dependence or memory dynamics with a form that can be measured against what happens.

The spine, as the thoughts put it:

    observation → evidence → belief → prediction → error → learning → memory

## 1. A chance that means something (`cognition/epistemics.py`)

| Was | Is |
|---|---|
| The reply's figure (`engine._score_response_confidence`, an index from length, grounding, evidence and intent) labelled "high"/"low" by fixed bands | The figure stays what it is, an index. The **chance the reply is right** is that figure calibrated on what earlier turns said of such figures: Platt scaling on the figure's logit, `p = σ(slope·logit(score) + shift)`, fitted by Newton's method on the penalised log-loss with a standard-normal prior on the shift and on the slope's departure from one. With nothing recorded, `p == score`; it moves only as outcomes say it should. |
| No record of whether figures proved right | Every CHAT reply's figure is put in the evidence ledger (`answer_assessed`, with its class and features: grounded, mode, agents, length, the self-consistency share and entropy, and the model's own confidence in the tokens of the reply). The next turn settles it: a correction (`correction_patterns.is_answer_correction`) → wrong, an endorsement ("thanks", "spot on", `is_endorsement`) → right; anything else leaves it open (`engine._settle_recall_outcome`, the same place the memories behind the answer are settled). |
| The model's own uncertainty, token by token, never read | A user-facing generation carries a logits processor (`gguf_inference._TokenConfidence`) that reads, at each step, the model's distribution over the vocabulary before sampling (a log-softmax, well under a millisecond beside a decode step) and, one step later, which token was chosen: the chosen token's log-probability and the entropy of the distribution it came from. The reply's summary (mean and least log-probability, the share of tokens chosen at under an even chance, the mean entropy and the first step's) goes onto the turn's facts and into the assessment's features (`epistemics.FEATURES`). A grammar-held call is the grammar's choice, not the model's, and background work is nobody's reply: neither carries the recorder. |
| Platt scaling on the figure alone | The fit is a logistic regression on the figure's logit **and** the token figures, `p = σ(w·[logit(score), mean_logprob, min_logprob, mean_entropy, low_share] + shift)`, Newton's method on the penalised log-loss with a standard-normal prior about 1 for the score's weight and about 0 for every other weight and the shift. A figure the outcomes have not shown to tell right from wrong keeps no weight; one that has (replies the model was unsure of token by token proving wrong) gains it, and `report(kind)` states every weight. With nothing recorded `p == score`, as before. |
| — | `Probability` carries its basis: kind, class ("grounded" or "chat", falling back to the kind as a whole), the raw score, the features, how many outcomes the fit rests on, the fit's own expected calibration error. The turn meta carries `probability` and `calibration`; the badge's label comes from `p`. |
| The ledger's Brier score for tool predictions, hand-binned | One owner of the scoring rules: `brier`, `ece` (expected calibration error, ten equal bands), `reliability` (chance given against share proved right, by band). The ledger's `calibration_report` uses them. `report(kind)` gives Brier and ECE raw and calibrated, so the fit's worth is itself measured. |

The world hears it: an `answer_assessed` event carries `p`, and ELI's **evidence confidence** and
**uncertainty** (the awareness figures the rooms' Measures console and the world panel show) are the
running means of the sitting's chances and their complements (`autonomy_engine`), starting over after
five minutes' quiet, in place of fixed steps up and down by event kind.

## 2. Self-consistency keeps the shape of the vote

| Was | Is |
|---|---|
| The modal answer's share of the samples, folded into the figure as `score − 0.35·(1 − share)` | The share, as before, and beside it the **entropy of the vote** normalised by the sample count (`epistemics.entropy`): a 3–2 split and five different answers both leave the winner with a minority, and the entropy tells them apart. Both go to the calibration as features. |

## 3. Shared evidence counted once (`cognition/agent_bus.py`)

| Was | Is |
|---|---|
| Per-agent contributions summed; a corroboration bonus when three or more agents contributed | Each result names what it rests on (`_provenance_roots`: memories by their **lineage root**, so a reflection that restates a memory rests on that memory; turns, file paths, addresses; the knowledge graph names the memories it was seeded from). Two results' **dependence** is the share of roots they both rest on, or, where neither names records, the share of words one has in the other. `independent_evidence` gives the amount once shared evidence is counted once (the sum less, per pair, their dependence times the smaller contribution, never below the strongest alone) and the **effective number of sources**, `(Σc)² / Σᵢⱼ ρᵢⱼ cᵢ cⱼ`: three readers of one record are one source. The corroboration bonus needs three or more *independent* sources. The critic says "N sources, M independent". |

## 4. How readily a memory comes to mind (`memory/policy.py`, `memory/memory.py`)

The forgetting curve stays: `2^(−t/H)` with the half-life stretched by origin, importance and use says
how long a row is **kept**. How readily it is **recalled** is another matter, after ACT-R's account of
declarative memory (Anderson & Schooler, 1991), used as engineering:

| Was | Is |
|---|---|
| One age and one use count: the strength, plus a linear 30-day recency, in the recall ranking | **Base-level activation**: `ln Σⱼ tⱼ^(−d)` over the row's uses, each known use at its age, the uses whose times are not kept spread evenly from the oldest known age back to the first encounter (the usual approximation), `d = 0.5`. Ten uses over months and one use yesterday are no longer alike. |
| Origin and importance as multipliers on the half-life only | The same factors as log terms on the activation: `+ ln(origin factor) + ln(1 + 3·importance)`, so what the user stated, or said matters, comes to mind more readily as it is kept longer. |
| Look-alikes ranked independently | **Competition**: `A*ᵢ = Aᵢ + ln(shareᵢ)`, the share being `e^Aᵢ` over `e^Aᵢ` plus every other `e^Aⱼ` weighted by how alike j is to i (the share of content words in common, `scoring.jaccard`). A look-alike as strong as i halves i's share; the stronger of two memories of nearly the same thing is the one that comes first. |
| — | **Chance of coming to mind** among the candidates: a logistic on the activation standardised against the set (`retrievable`), ACT-R's retrieval chance with its threshold and noise read off the set rather than fixed. The recall works it out in one query by id (`Memory._accessibility`) and `fuse_memory_score(accessibility=…)` and the reranker use it in place of strength and recency together; the stored weight keeps its job of deciding what is archived. |
| The top of the list could be five ways of saying one thing | **Maximal marginal relevance** over the final list (`scoring.mmr_select`, λ = 0.7, relevance the row's own ranking score as a share of the best): later places go by relevance less likeness to what is already chosen, so the context carries complementary memories. |

## 5. How sure a claim about the user is (`memory/claims.py`)

| Was | Is |
|---|---|
| `confidence + 0.05` per confirmation, capped at 0.99; a disputed newcomer capped at 0.4 | `claim_confidence`: the mean of a **Beta posterior** with one pseudo-count each way, each source weighted as belief revision weighs provenance (`belief.PROVENANCE_WEIGHT`: the user's own word 1, a document 0.6, something observed 0.5, ELI's inference 0.35). One user statement 0.67, two 0.75, five 0.86, never 1; a contradiction pulls both sides back, and the user's word contradicted by an inference (0.6) stays ahead of the inference (0.4). `describe()` ends with the basis: "sure 0.75: you said so twice; contradicted". The Evidence Wall's sheets carry it. |

## 6. The rest of the audit's findings

| Where | Was | Is |
|---|---|---|
| `coding/search.py` | UCB-shaped: `score + c·√(ln N / nᵢ)` with the node's own static score | **UCT**: the value of a node is the mean of what came of it, its own score and the score of each refinement made from it, so a node whose refinements keep failing is passed over for one whose refinements improve. |
| `perception/voice_profile.py` | Nearest centroid in standardised features; confidence from the margin to the runner-up; every clip gets a label | Each emotion a Gaussian with its own spread per feature (variances shrunk toward the whole set's by as many pseudo-samples as there are features), **Mahalanobis** distance, a **posterior** over the classes with their sizes as prior, and **open-set rejection**: a clip further from every class than the chi-square 99 % quantile (Wilson–Hilferty) is no emotion rather than the least unlike one. |
| `perception/wakeword.py` | "Pink" noise bed that was a running sum of white noise (brown, 1/f²); the threshold chosen on the training fit | Real pink (1/f, shaped in the spectrum) beside white and brown; the threshold from **out-of-fold** scores (five folds) of heads that never saw the clip they score, then one head trained on everything. |
| `memory/document_index.py` | `1/(60 + rank)` from rank 0, unlike the memory reranker's `1/(k + rank + 1)` | The one constant and counting (`reranker.RRF_K`, ranks from one). |
| `eli/tools/image_engine/image_engine/quality.py` | Exposure `1 − 1.8·|mean − ½|`: a clipped frame kept a tenth of the credit | Factor 2: all black or all white scores 0. |

## Method constants, each stated where it is used

`d = 0.5` (ACT-R decay), MMR `λ = 0.7`, ten reliability bands, five folds, `z = 2.326` (99 %), a
standard-normal prior on the calibration fit (about 1 for the score's weight, 0 for the token figures and
the shift), `EDGE = 10⁻³` for a score at exactly 0 or 1, an even chance (`log 0.5`) as the line below which
a token counts as chosen unsurely. None is a size or a limit; each is a method's usual setting, named and
documented, and the behaviour it controls is tested.

## Not done

- A latent valence/arousal state (a Kalman filter over the acoustic and text reads) for the affect
  stack; the reads are still fused per turn and kept as a timeline.
- Hardware system identification (a learned map from a configuration to latency, memory and
  throughput); the planner stays measurement-by-simulation, which the audit called the strongest part.
- Streamed replies are assessed where their meta is published; a reply that never reaches
  `_publish_last_response_meta` is not.
- Claim-level chances inside one reply (the thoughts' end state) are not attempted; the reply has one
  chance.
