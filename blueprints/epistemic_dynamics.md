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

| An agent's "calibration" factor was a rolling average of its own self-confidence times evidence density, `0.5 + rolling`, never checked against anything | Each settled reply names the agents behind it. An agent's factor is how often the replies it contributed to proved right against replies as a whole, its rate shrunk toward the whole's by the calibration's pseudo-count, within the old bounds; until a settled reply names it, the rolling factor stands. |

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
| The ranking's weights were set once and never checked | Each memory shown to a reply is a figure of kind `recall` (`orchestrator._assess_recalls`, under `recall:<turn>:<memory>`): its ranking score with how readily it came to mind, its importance, how much of the question it holds and whether more than one retriever found it. The next turn settles them with the reply (an endorsement: used; a correction: not). Once outcomes exist the **chance a memory is of use**, `σ(w·[logit(score), accessibility, importance, overlap, agreement] + b)` fitted on them (`epistemics.weights_for`), orders the shown memories (`reranker.rerank_candidates`, `recall_chance`); until then the index does. Each shown memory is a thought in the Memory Archive saying why it came to mind (§10). |

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

## 7. The user's affect as a latent state (`cognition/affect_state.py`)

| Was | Is |
|---|---|
| Each turn's read (an emotion label with the reader's sureness, the voice's arousal) taken on its own: the last read above a line of 0.55 set the policy; a mood was three reads of one kind in a row; a read under 0.45 was ignored outright | The reads are observations of a state `x = [valence, arousal]` that moves with inertia and fades toward neutral when nothing is heard, `x_t = φ x_{t−1} + w_t`, `φ = e^(−Δt/τ)`, kept by a **Kalman filter** with its uncertainty. A read's value is its label's sign at the reader's sureness `c`, its noise `c(1 − c)` plus a floor measured from this user's reads; a typed turn observes no arousal. The time constant τ and how far the state wanders between reads are **identified from this user's own reads** by the likelihood of the filter's innovations over a grid; a mood that turns is caught by the innovation test (the last two surprises past the chi-square 95 % point raise the state's uncertainty to what they say it moved). A state is meant when it stands clear of neutral (0.45) and of its own doubt (1.28 σ, 90 % one-sided): one read reaches 0.75 σ, three in a row 1.7. |
| `emotion_timeline.assess`, `affect_policy.current_policy` and the world's social read each read the raw figure | All three read the state: the timeline's assessment carries it (`affect`) and gates a check-in on it, the policy's confidence is the chance the state's sign is meant, the world's nudges use it once it stands. The baseline (what is normal for this user) and the check-in cooldown are as they were. |
| A turn with no emotion in its words or voice read as neutral, whatever the state | `tone_adaptor._fuse` falls back to the standing state when neither system reads anything this turn: a negative state is met as irritation is (calm), a positive one as happiness, at the chance the state is meant. A mood does not end because one sentence carried no sign of it. |

## 8. The chance a claim is right (`memory/claims.py`, kind `claim`)

| Was | Is |
|---|---|
| A claim's sureness was a Beta posterior (§5) and nothing checked it; every claim reached a reply's evidence at a fixed 0.9 | `claims.chance`: the sureness **calibrated on what became of claims of that origin** that replies rested on, the second kind in `epistemics` with its own ledger events (`claim_assessed`, `claim_outcome`). A claim used by a reply is assessed under the turn's id; the next turn settles it with the reply: an endorsement settles every claim used right, a correction settles wrong only the claim its words speak of, and the user settling a conflict settles the figures of both sides. The Evidence Wall's sheets and `basis()` carry the chance ("chance 0.52 after 12 such claims were checked"). |
| The reply's chance knew nothing of what it stood on | The claims a reply rested on go into its assessment as features: how many, the least chance among them and the mean, so the fit learns that a reply resting on a doubtful claim is more often wrong. Claim evidence is scored by its chance and marked `disputed` when it is. |

## 9. What became of ELI's actions, as something it learns from (`runtime/action_outcomes.py`)

The evidence ledger recorded every run and answered one question of it, an action's reliability.
A failure was classified and, for a missing app or tool, met with an install offer; the next request
tried the same thing the same way, and a reply could report a success nothing had checked.

| Was | Is |
|---|---|
| A result's `ok` was the record | **Verified execution** (`verify`, applied in `executor.execute` before anything records or reports the run): a run that writes names a file, which must exist and be newer than the run began; a search must carry results. A file that is not there turns the result into a failure that says so ("did not take effect: the file x is not there"), and what the handler claimed is kept beside it; a search that brought nothing back has no effect. What this cannot see (a window, a browser tab) is left as reported and marked unchecked, never flipped on a guess. |
| One reliability per action | The **chance a run like this works here**, by its target: runs are classed by what they were about (`target_class`: an app by name, a site by host, a path by extension, a query), and the chance is a recency-weighted Beta estimate over the runs of that class, shrunk toward the action's rate by `m = 3` pseudo-runs, and that toward every action's (Beta(1, 1)); each run weighs `0.5^(age / 7 days)`, as the ledger's reliability weighs them. The commonest cause of the class's failures rides with it. |
| An error was text | A **cause** by the one taxonomy (`runtime/failure_taxonomy`: dependency, missing resource, permission, timeout, network, resource, user input, data, an error inside ELI), with `nothing_found` for a search that came back empty and `unverified` for an effect ELI could not see; the cause is recorded with the run and said in words (`cause_words`). |
| Nothing was learned from what worked afterwards | **Alternatives** (`alternatives`): an action that succeeded for the same aim within ten minutes of a failure of this one, in the same session (the same target, or a subject naming it: "spotify" in "open.spotify.com"), with the chance it works after such a failure, `(ok + 1)/(tried + 2)`, how many times, and whether it was proven on this very target. |
| A failure ended the request | **Attempt or advise** (`advice`, in `executor._action_post_dispatch`): an alternative proven on this very target, likelier than another go of the failed action, and safe to run unasked (never one that destroys, sends, pays or reaches outside, never an explicit-only action, never an alternative of an alternative) is tried at once, and the reply says what failed, why, and what worked instead. Otherwise the reply carries the advice (`said`): how the action has fared here lately, this failure's cause, and what has worked instead, with "say so if you want me to try that". The install offer stands behind both. |
| ELI could not say what works | **The record, told** (`report`, `words`): by action and target, how often it worked, the causes of its failures, its weakest target, and what worked instead, as data and in words. It opens `EXPLAIN_FAILURE_LOG`, is the whole answer to "what has worked lately", "which of your commands keep failing" (`router.what_has_worked`, read in the one sentence that asks: a complaint about a feature whose words are spread over several sentences is not the question), joins the self-report when any action has failed, and goes into the fix task the self-repair hands the coding agent. |
| A prediction that missed taught nothing | A run predicted to work that failed, or the other way about, by more than an even chance is a **surprise** (§11): the memories about its target matter more. |

## 10. One shape for a thought (`cognition/thoughts.py`)

Every decision below was made silently: a memory brought to mind, a claim a reply rested on, a sentence
read as a command, something made of ELI's own accord, a failed action and what came next, a fix ELI
proposed for itself, a memory let go. Each now writes one record, `{kind, what, why, chance, decision,
reason, subject, request_id, ref, status, outcome, how, when, room}`, settled later by what became of
it, so "why did you think of that", "how sure were you", "why did you drop it" and "what became of it"
have one answer from the chat, the world's rooms and any package that reads ELI's world. The kinds and
their rooms: `recall`/`memory` in the Memory Archive, `claim` on the Evidence Wall, `surprise`/`conflict`
in the Anomaly Room, `action` in the Debug Basement, `route`/`act` in the Core Room, `proposal`/`diagnosis`
in the Upgrade Bay. The world feed carries them (`thoughts`, capability `thoughts`, `GET
/v1/world/thoughts?room=&kinds=`), read as often as the other records; ELI VR shows them on the screens
of the room they belong to, each with its decision and reason, its chance and what it proved. A thought
is kept 90 days, as outcomes are.

## 11. Surprise, salience, interest and curiosity

| Was | Is |
|---|---|
| A memory's importance was set when it was stored and reinforced only when it was recalled and helped | **Surprise raises importance** (`memory/salience.note_surprise`): a correction of a reply or of a claim, an action predicted to work that failed (or the other way about), and a mood that turned are prediction errors; the memories about the subject move `α · error · (1 − importance)` toward one, `α = 0.25` (the Rescorla–Wagner learning rate at its usual middle setting), the error being the chance that was given for what did not happen. Each surprise is a thought in the Anomaly Room and an event ELI's world hears. |
| Use was reinforced on the row; nothing reached its importance | **Promotion in the daily upkeep** (`Memory.promote_used`): a memory that went into answers nobody corrected stands at least at `(helped + 1)/(helped + corrected + 2)`, and one corrected more often than it helped no higher than that share. Pure arithmetic on the counts. A memory let go by the upkeep (faded, unused, derived) is a thought with its reason and its strength as its chance; it is in the archive, still searched when live evidence is thin. |
| The user's interests were counts: a word in two messages in a month, the last week doubled; a fixed boost of 0.15 for a memory tagged as a preference | **Interest as activation** (`memory/interest.py`): a topic in the user's talk is a memory in theirs, with the base-level activation of §4 over their own mentions, `A = ln Σⱼ tⱼ^(−0.5)`; the focus is the topics with the highest activation, and a topic's share of the total (`e^A` over the sum) is how much of their attention it holds. A new memory about a topic in focus takes `0.20 · share` into its importance (the weight the score gives a self-statement); the recall's boost is `0.15 · share` for anything the user did not state about themselves outright; the proactive daemon's focus areas are these, with their shares. |
| ELI's curiosity was a bar nudged by a tenth per reflection; its own moments were acted on oldest first | **Curiosity as prediction error** (`cognition/curiosity.py`): ELI's curiosity about a subject is the sum of its surprises about it, each weighed `0.5^(age / 7 days)`, plus one for each open conflict about it; the world's curiosity bar is the running mean of the sitting's surprises' sizes (`autonomy_engine`, `AwarenessState.surprises`), and a reflection or a reasoning stage no longer moves it. The moment ELI acts on (`urges.act`) is the one it is most curious about, the oldest among equals. |

## 12. Gates that ask the evidence (`epistemics.worse_below`)

Three lines decided replies and none had been checked: a bus answer was taken at an aggregate of 0.7,
memory context under 0.5 was dropped, and a reply grounded under 0.35 was replaced by a hedge. Each
line stands, but it is now **asked of the outcomes** before it is applied: whether the settled replies
whose figure fell below the line proved right less often than those at or above it, a one-sided
two-proportion test at `z ≥ 1.282` with at least five settled replies on each side. Shown nothing
(too few on a side), the line holds as it did; shown that replies below it did as well, it opens;
shown that they did worse, it holds. The figures the gates read ride on every reply's assessment
(`aggregated`, `memory_conf`, `grounding`, with the path it took and whether memory context was used),
which is what makes the test possible. A tool result that reports success is trusted by the action's
measured reliability once there is history (`evidence_arbitration._score_tool_result`), the fixed 0.86
only until then.

## 13. Routes, acts and proposals as figures

| Kind | The figure | Its features | Settled by |
|---|---|---|---|
| `route` (`engine._assess_route`, where the sentence's route is decided) | the route's own confidence, calibrated per stage (a rule, the parser, the resolver, the chat fallback) | how far down the stages it was read; whether the command parser read it the same way | the next user turn: a correction, a complaint about ELI or a redo settles it wrong; moving on settles it right (a misroute draws a complaint, a right route the next request). An autonomous turn settles nothing. |
| `act` (`urges.act`, something made of a moment of ELI's own) | the base rate of acts of that kind standing | how long the user had been quiet | taken back by the user (`building.apply` undo, `urges.undone`) → wrong; nobody took it back for a day (`urges._settle_stood`) → right. A kind whose rate falls under that of acts as a whole is let go of, and the thought says so. |
| `proposal` (`self_improvement.propose_candidate`) | an even chance, calibrated per verdict (fixed, unchanged, inconclusive) | how often the failure it answers recurred | rejected or regressing → wrong at once; adopted and through probation → right; rolled back → wrong (`_settle_proposal`). Each diagnosed failure is a thought in the Upgrade Bay. |
| `recall` (§4) | the ranking score | accessibility, importance, overlap, agreement | the reply it was shown to |

The awareness bars the rooms show are measured where they can be: evidence confidence and uncertainty
(§1), **repair pressure** as the share of the sitting's tool runs that failed (a running mean over the
runs, `AwarenessState.tools`), **curiosity** as the mean size of the sitting's surprises (§11). The
rest still ease by event.

A reply that denies the record of a period whose record the turn has in hand ("I have no record of
that session", "the logs from that day do not exist in my context") is held to it: the sentence is
dropped (`self_claims._DENIES_THE_RECORD`, on `_period_found`) and the period log's own lines are
served in its place.

## Method constants, each stated where it is used

`d = 0.5` (ACT-R decay), MMR `λ = 0.7`, ten reliability bands, five folds, `z = 2.326` (99 %), a
standard-normal prior on the calibration fit (about 1 for the score's weight, 0 for the token figures and
the shift), `EDGE = 10⁻³` for a score at exactly 0 or 1, an even chance (`log 0.5`) as the line below which
a token counts as chosen unsurely; for the affect state a grid of time constants from 5 minutes to 8 hours
and of wander shares from 0.05 to 1, a prior spread of 1/4 (a label as likely either way), the chi-square
95 % point over two innovations, 0.45 as the line a state must clear and 1.28 σ (90 % one-sided) as its
significance; for the learning loop `m = 3` pseudo-runs of shrinkage, a half-life of 7 days on a run or a
surprise, ten minutes as the window in which a success is the same request tried another way, `α = 0.25`
as the surprise learning rate, `z ≥ 1.282` with five settled figures a side before a gate's line is judged,
a day before an act is taken to have stood, 90 days as the life of a thought and of an outcome. None is
a size or a limit; each is a method's usual setting, named and documented, and the behaviour it controls
is tested.

## Not done

- A run's effect is checked where ELI can see it (a file, a search's results); an opened window, a
  played track or a browser tab is reported as the handler saw it and marked unchecked, not verified.
- The affect state reads text and voice; a vision read of the user's face would be a third observation
  of the same state and is not taken.
- Hardware system identification (a learned map from a configuration to latency, memory and
  throughput); the planner stays measurement-by-simulation, which the audit called the strongest part.
- Streamed replies are assessed where their meta is published; a reply that never reaches
  `_publish_last_response_meta` is not.
- A reply's chance is one figure informed by its claims' chances; it is not yet decomposed into a chance
  per sentence.
