# Human fallacies the simulated participant must reproduce

Research digest behind `usability_kit/fallible_participant.py`. Each finding maps
to a mechanism in code (perception is enforced structurally; cognition by prompt).

## Perception (enforced in the gaze model, not asked of the LLM)

| Finding | Source | Mechanism |
|---|---|---|
| People scan, rarely read word by word; at most 28% of words read per visit, 20% likelier | [NN/g How Little Do Users Read](https://www.nngroup.com/articles/how-little-do-users-read/), [How Users Read on the Web](https://www.nngroup.com/articles/how-users-read-on-the-web) | Per-block read depth `words_per_block`; fragments end in "…"; the LLM only receives read fragments |
| Scanning patterns: F-pattern (two horizontal bars then a vertical stripe down the left), layer-cake (headings only), spotted (hunting a keyword), commitment (full read when motivated) | [NN/g F-shaped pattern](https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content), [Text scanning patterns](https://www.nngroup.com/articles/text-scanning-patterns-eyetracking) | First two text blocks read ~2× deeper; later blocks only their first words; headings favored; scent-matching blocks pulled forward |
| Attention leans left: 80% of viewing time on the left half | [NN/g Horizontal attention leans left](https://www.nngroup.com/articles/horizontal-attention-leans-left) | Left-half salience multiplier on desktop |
| 57% of viewing time above the fold, 74% in the first two screenfuls | [NN/g Scrolling and attention](https://www.nngroup.com/articles/scrolling-and-attention/) | Only the viewport is perceived; top-of-screen prior; scrolling costs an action |
| Illusion of completeness: users stop when the page looks finished | [NN/g Illusion of completeness](https://www.nngroup.com/articles/illusion-of-completeness/) | "More below" is reported only when something is visibly cut at the fold (phone) or the scrollbar shows it (desktop) |
| Banner blindness: ad-like elements, top strips and the right rail get ignored | [NN/g Banner blindness revisited](https://www.nngroup.com/articles/banner-blindness-old-and-new-findings/) | Ad-like class/id ×0.15, desktop right rail ×0.5 |
| Decorative/stock photos are ignored; content photos (products, people) get looked at | [NN/g Photos as web content](https://www.nngroup.com/articles/photos-as-web-content) | Large images salient, small icons not; the LLM gets only the alt text of an image it fixated |
| Users leave pages within 10–20 s unless value is clear | [NN/g How long do users stay](https://www.nngroup.com/articles/how-long-do-users-stay-on-web-pages) | Per-screen time budget (`screen_budget_s`) caps fixations |
| Lower-literacy users plow text word by word and miss elements from a narrower field of view | [NN/g Lower-literacy users](https://www.nngroup.com/articles/writing-for-lower-literacy-users/) | `reading: plower` → deeper reads, slower words/s, steep distance decay between fixations |
| Reading comprehension is harder on phone-size screens | [NN/g Reading on mobile](https://www.nngroup.com/articles/mobile-content/) | Phone personas get smaller budgets and read less per block |
| Change blindness: changes outside the focus of attention go unnoticed | [NN/g Change blindness](https://www.nngroup.com/articles/change-blindness-definition/) | Each screen is perceived afresh by gaze; nothing is "diffed" for the persona |
| Fixations last ~200–300 ms (range ~150–400) | [ScienceDirect: fixation duration](https://www.sciencedirect.com/topics/psychology/fixation-duration) | Fixation dwell 200–320 ms + ~60 ms per extra word |
| Saliency differs by UI type; top-left and large text/images dominate early fixations | [UEyes, CHI 2023](https://dl.acm.org/doi/fullHtml/10.1145/3544548.3581096) | Salience = size × font × contrast × position prior |

## Cognition (enforced by the persona prompt)

| Finding | Source | Prompt rule |
|---|---|---|
| Satisficing / muddling through: users click the first reasonable option | Krug, *Don't Make Me Think*; [NN/g Information foraging](https://www.nngroup.com/articles/information-foraging) | Pick the first good-enough option; gaze also stops early on a scent match (`satisfice`) |
| Information scent: link label + context decide the next click | [NN/g Information scent](https://www.nngroup.com/articles/information-scent) | Persona states `looking_for` in its own words; only literal/stem matches raise salience, so vocabulary mismatch causes real misses |
| Jakob's law: users expect your site to work like the sites they already use | [NN/g Jakob's law](https://www.nngroup.com/videos/jakobs-law-internet-ux) | Expect MercadoLibre/bank/Google conventions |
| Users skip instructions and long paragraphs | [NN/g How users read](https://www.nngroup.com/articles/how-users-read-on-the-web) | Never read long text unless stuck |
| Placeholders get mistaken for filled values or vanish from memory | [NN/g Placeholders are harmful](https://www.nngroup.com/articles/form-design-placeholders) | Fields are reported with their placeholder; persona may misread it |
| Slips (unconscious, e.g. fat-finger) vs mistakes (wrong mental model) | [NN/g Slips](https://www.nngroup.com/articles/slips) | Phone motor slips on small adjacent targets (`slip_rate`); mistakes come from the prompt |
| Buried tabs/accordion content gets overlooked | [Baymard horizontal tabs](https://baymard.com/research-articles/avoid-horizontal-tabs) | Closed disclosures show only their label |
| After ~10 s of waiting attention drifts | [NN/g Response times](https://www.nngroup.com/articles/website-response-times/) | Slow loads are told to the persona as waiting time |

## LLM-participant caveats

- Unconstrained LLM agents are super-users ([UXAgent](https://arxiv.org/abs/2502.12561)); the fix is a perceptual bottleneck, not a weaker model.
- Output is simulation evidence: "synthetic completion rate", never a user success rate.
