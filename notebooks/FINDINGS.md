# Phase 2 Findings

_Corpus: **118** structured threads (from `data/structured/threads.jsonl`). Norms min-n threshold is 30; buckets below that are flagged `insufficient_data` but still reported for development._

## Top insights

1. **Largest role bucket is `swe_intern`** with **76** threads (64% of the corpus).
2. **Role mix:** `swe_intern`=76, `swe`=18, `data_intern`=9, `swe_new_grad`=8, `ml`=5, `data`=2.
3. **Year/seniority labels:** unknown=53, year_1=26, year_2=18, year_3=10, year_4=5, new_grad=5.
4. **Critique volume (real, excl. not_a_critique):** mean **3.3**, median **2**, max **22** per thread (raw messages: mean 5.41).
5. **Top critique categories (real critiques):** other (58), wording (57), bullet_quality (49), formatting (42), section_order (33), metrics (28). **other rate=15%** (gate: <15%; excluded 250 not_a_critique).
6. **Sections most often targeted by critiques:** general (264), experience (176), projects (72), formatting (50), skills (44).
7. **`swe_intern` skill prevalence (top):** Python=87%, C++=83%, C=74%, JavaScript=74%, Git=72%, SQL=62%, Java=58%, React=58%.
8. **Most common section order(s):** `education > experience > projects > skills` (n=17); `skills > experience > projects > education` (n=12); `education > skills > experience > projects` (n=12).
9. **Bullets per entry:** median **2**, mean **2.42** across **581** entries (one-page heuristic budget is ≤4 experience / ≤3 project bullets).
10. **Page convention for `swe_intern`:** `one_page` (median bullets/entry=2.0).
11. **Skill normalization coverage:** 48/50 of top observed raw spellings map cleanly (rate=96%).
12. **Data sufficiency:** `swe_intern` meets the n≥30 bar.

## Figures

- `notebooks/figs/01_role_distribution.png`
- `notebooks/figs/02_year_distribution.png`
- `notebooks/figs/03_critiques_per_thread.png`
- `notebooks/figs/04_critique_categories.png`
- `notebooks/figs/05_top30_skills.png`
- `notebooks/figs/05b_top_skills_swe_intern.png`
- `notebooks/figs/06_section_order.png`
- `notebooks/figs/07_bullets_per_entry.png`
