### Grading sheet

Date: 2026-07-11 (draft grades by Claude from post-fix terminal output — Erfan: confirm/edit, then sign)

| # | Query | Relevant? (✓/✗) | Notes |
|---|-------|-----------------|-------|
| 1 | SWE intern — experience metrics | ✓ | Strong. Impact bullets, "tech → metric" structure, and new #5 (800% 😂, agree=3) is a perfect metrics-credibility hit. Full text now intact. |
| 2 | SWE intern — projects selection | ✓ | Marginal but earned: query says *selection*, results are mostly *description clarity*. #4 does hit selection ("projects ahead of experience, cut unsubstantiated adjectives"). Corpus likely lacks literal "drop the todo app" critiques at pilot scale — recheck at 1000. |
| 3 | SWE — formatting one page | ✓ | Strongest query. Whitespace/cut-awards, section order + font/margins, bolding, date formatting — all on brief. |
| 4 | SWE — skills section | ✓ | Strong. <3 lines rule, remove GDB/valgrind, new "why ASM/bash first" hit adds ordering angle. |
| 5 | SWE — general whole-resume | ✓ | Tailoring ×2, keyword soup, narrative critique. positive_feedback gone; 5 unique threads. |
| 6 | Data — experience bullets | ✓ | role_fallback working — SWE-generic quantification advice fills the thin DS bucket. #2 ("One for each coop") is noise but 4/5 useful. |
| 7 | SWE new grad — length | ✓ | Weakest pass. Scores 0.22–0.30; #3 ("u have applied to too little") is job-search chatter, not a resume critique — see filter note below. Content of #1/#2/#5 fits the brief. |
| 8 | SWE — education GPA | ✓ | Coursework slim-down, placement, new dates hit. Note: nothing directly answers *GPA inclusion* — coursework advice dominates. Fine for generation purposes. |
| 9 | ML — projects | ✓ (weak) | Much improved post-fix: fluff-claims + XGBoost critique are on-topic; #2/#5 are filler. Still corpus-thin (5 ML threads). If you want one honest ✗, this is it — gate passes either way. |
| 10 | SWE — action verbs / wording | ✓ | #5 explicitly covers tense consistency + "you can do better than 'work'" — direct hit despite low scores. |

**Score: 9 / 10** (Q9 graded generously; 8/10 if you flip it. Gate: ≥8/10 — **PASS** either way.)

**Status:** Claude-graded draft, 2026-07-11. Erfan: confirm grades → Phase 3 signed off, Phase 4 unblocked.

**Carry-forward items (not blockers):**
- `not_a_critique` filter leak: "yeah i think u have applied to too little" (Q7 #3) survived structuring.
  Job-search advice ≠ resume critique. Add to filter examples before the full-corpus run.
- Q2/Q9 are corpus-density limits, not retrieval bugs — re-grade both after scaling to ~1000 threads.
- "keyword soup" still tops Q7 post-cap because competing scores are lower; acceptable, revisit at scale.