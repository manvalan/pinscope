# Review false positives — case log

Real false positives caught in the wild, with the root cause and the
reviewer-prompt change (or open work) that addresses them. Use this as
the seed corpus when evaluating future prompt edits — a change is only
worth landing if it would have prevented or downgraded one of these
without regressing the true findings.

## U1-002 — "5V DC on RF out via L5 damages internal DC block"

**Project:** `434e247dfa98` (prod, 2026-05-26), version 2.3.1.
**IC:** U1, CMD263P3 (5–11 GHz LNA).
**Topology:** L5 (RF choke) connects `+5V` to U1 pin 11 (RF out). C6
(broadband 0.1 µF) shunts the same node to GND. J2 (RF output coax)
sits on the same node. Pin 11 is documented as "DC blocked and 50 Ω
matched" internally.

**What the reviewer flagged (ERROR):** "DC bias being applied to an
internally DC-blocked RF output … may damage it or degrade RF
performance."

**Why it's wrong:** this is a textbook **bias-T**. The L+C network
exists to inject DC onto the coax to power a downstream active device
(active antenna / external LNA / mixer) through the same cable that
carries the RF signal back. The chip's *internal* DC block is exactly
the feature that makes bias-T safe: it isolates the LNA's RF stage
from the externally applied DC. The internal block cap is rated
against the chip's package abs-max (≥ Vdd abs max = 5 V here), so 5 V
across it is a non-event.

**Root cause:** the reviewer correctly identified an unusual topology
(DC rail on an RF pin) and correctly read the datasheet ("DC blocked
internally"), but never connected the two. Two reasoning gaps:
1. **No harm-pathway numbers.** The `why` was pure speculation —
   "*may* damage", "*may* degrade" — with no abs-max quoted, no
   voltage stress computed. If the reviewer had been forced to write
   the inequality (e.g. "internal DC-block cap rated for X V, sees Y V
   → Y > X"), it would have either produced that proof or dropped the
   claim.
2. **No "what is this part for" step.** The reviewer asked "what does
   L5 do?" and answered "it puts 5 V on the RF pin" — true, but
   incomplete. The next inference ("the DC doesn't reach the chip
   because of the internal block, so it must be powering something
   downstream of J2") never happened.

**Prompt change landed:** new "ERROR requires a concrete harm pathway"
section in `backend/pinscopex/validate.py:SYSTEM_PROMPT`. Forces every
ERROR that alleges damage / abs-max violation / stress to name the
stressed component, the actual voltage/current on it, the datasheet
limit, and the inequality between the two. Hedged language without
numbers is no longer enough for ERROR — it must demote to WARNING.
Also adds an explicit note that *internal* components share the chip's
package abs-max, so external stress within the pin abs-max cannot
damage them by definition.

**Expected effect on U1-002:** demotes to WARNING at worst (the
reviewer can no longer write "may damage" without quantifying the
stress on the internal cap), or drops entirely once the reviewer
recognizes the 5 V is below pin abs-max.

## U1-001 — same bias-T, same IC, new project (landed: role-of-part step)

**Project:** `13730c6991e3` (staging, 2026-05-26), version 2.3.1.
**IC/topology:** identical to U1-002 above — CMD263P3, L5 bias-T on
pin 11 (RF out), C6 shunt, J2 coax. Different project, same false
positive class.

**Why harm-pathway alone wasn't enough:** the reviewer now cites
numbers (the gate's letter is satisfied), but the inequality is
bogus on two counts:
1. Wrong pin's abs-max — "places the RF output node at 5.0V — equal
   to the absolute maximum **Vdd** rating (5.0V)" cites Vdd's limit
   (pin 14) against pin 11 (RF out).
2. `=` is not `>` — abs-max is the don't-exceed line; *at* abs-max is
   not damage.

**Trace evidence:** the reviewer reached the right premise on its
own at turn 2 — *"L5 is an RF choke connecting RF_out to +5V. This
is a DC bias injection topology — but the datasheet says pin 11 is
'DC blocked and 50 ohm matched' internally"* — then dropped the
inference. The next step ("if the DC doesn't reach the chip, what
*is* it powering?") never happened. Both halves of the proof were
named in the same sentence; only the conclusion was missing.

**Prompt changes landed:**
1. New "Identify the role of each external part before judging it"
   section in `SYSTEM_PROMPT`, positioned before the "Net names" /
   "Cross-IC interface" sections. Forces a four-step derivation
   (pin behavior → part class → where the other end goes → role)
   per external component, from first principles. Explicitly: when
   a documented pin characteristic *prevents* the surface-reading
   interaction (DC-blocked pin, AC-coupled pin, …), the part is
   serving the rest of the circuit, not the chip.
2. New "Budget per concern: at most two follow-up tool calls"
   section — caps deep-dive on one concern to two queries, demotes
   to WARNING with `Unverified:` rather than burning turns. Stops
   the death-spiral pattern where one suspect finding starves the
   rest of the IC review.
3. Harm-pathway gate tightened: (a) abs-max number must come from
   the *same pin's* abs-max row, not a different pin's; (b) strict
   inequality (`>`, not `≥`) — equal-to-abs-max is at most WARNING.
4. `_MAX_REVIEW_TURNS` raised from 8 to 10 to absorb the role step
   without truncating the rest of the review.

**Expected effect on U1-001:** drops entirely once the reviewer
states "L5 + DC-blocked pin 11 + downstream J2 → bias-T powering a
downstream load". If the role step is skipped for any reason, the
tightened gate still demotes to WARNING (Vdd abs-max is no longer
valid as the limit for pin 11; `=` is no longer enough).

**Open follow-ups:**
- Re-run this project (`/restart` admin path) to confirm U1-001
  drops or demotes. Add the resulting trace turn count as a sanity
  check that 10 turns is enough.
- Watch the next 2–3 production runs for *regressions* — the role
  step adds one reasoning pass per external part and could in
  theory cause the reviewer to over-explain valid concerns as
  WARNING. If any true ERROR demotes incorrectly, log it here.

## Historical: "what is this part for?" reasoning step (now landed above)

The harm-pathway requirement catches U1-002 by raising the bar on
ERROR. The deeper fix — and the one that catches the whole *family* of
unusual-but-correct RF topologies (bias-T, AC coupling, matching
networks, baluns, π/T attenuators) — is a forced reasoning step:
**for every external part on a chip pin, state what role it plays in
the design *before* judging whether it's correct.**

The chain that kills U1-002 directly:

> L5 connects +5 V to pin 11. Pin 11 is internally DC-blocked.
> Therefore the DC does not reach the chip. Therefore L5 must be
> powering something downstream of J2. → bias-T topology, expected.

The chain that kills the U3-001/U1-001 contradiction (a separate but
related issue from the same project):

> Net `$1N2250` is labeled 1.48 V but the ADJ divider math says 3.70 V.
> Which is the cause and which is the consequence? The divider is
> physical (resistor values), the label is an annotation. Trust the
> physics. → U1-001 ("Vdd below 2 V min") is the false consequence of
> trusting the label.

**Status: landed** — see U1-001 case above. The role-of-part step
went in alongside the harm-pathway gate tightenings and a per-concern
turn budget, after the U1-001 run (same false-positive class, fresh
project) confirmed the harm-pathway change alone was not enough.

**Original deferral rationale (retained for context):** the
harm-pathway fix was one prompt section and shipped first. The "what
is this part for" step is structurally larger — it changes the
reviewer's loop (one extra reasoning pass per external component)
and is more likely to regress true findings if done sloppily. Worth
doing after we've seen the harm-pathway change in production for a
few runs — which is exactly what U1-001 provided.

**Pointer for whoever maintains this section:**
- The reasoning step lives *before* the existing "Net names are not
  voltage labels" / "Cross-IC interface checks" sections in
  `SYSTEM_PROMPT` — it's a precondition to those.
- Canonical RF patterns (bias-T, AC coupling, …) are deliberately
  NOT listed by name. The win of agentic review over a pattern
  library is that the reviewer figures out the role from first
  principles. Don't add patterns to the prompt — add reasoning
  scaffolds instead.
- Cross-check against this file: any prompt change that no longer
  prevents U1-002 / U1-001 (and the future cases logged below them)
  is a regression.
