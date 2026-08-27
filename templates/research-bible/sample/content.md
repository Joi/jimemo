---
title: "Dialogic Goal Elicitation and Clarification"
subtitle: "How skilled practitioners help people discover, reframe, and decompose what they actually want"
kicker: "Research Bible"
date: "Compiled 25 August 2026"
provenance:
  - label: "Sources in corpus"
    value: "394"
  - label: "Retrieval slices"
    value: "27"
  - label: "Evidence gate"
    value: "Passed"
  - label: "Refute adversary"
    value: "OpenAI family"
legend:
  - tag: "[Author, Year]"
    meaning: "An attributed claim, resolved against the bibliography."
  - tag: "[disputed: …]"
    meaning: "Figures the corpus reports differently; never silently averaged."
  - tag: "[confidence: …]"
    meaning: "Strength and transfer-distance of a claim."
  - tag: "FLAG"
    meaning: "A number resting on a weak or single source."
sections:
  - heading: "Why stated goals misrepresent underlying desires"
    body: |
      The premise of any goal-elicitation practice is that the first goal a
      person states is a starting point, not a destination. The sources
      reviewed here offer several mechanisms by which stated goals diverge
      from the goals that actually govern satisfaction and persistence:
      action identification theory [Vallacher & Wegner, 1987], goal systems
      theory [Kruglanski, 2023], self-concordance research [Sheldon &
      Elliot, 1999], and an engineering folklore tradition — the XY problem
      — that arrived at a similar description independently [Meta Stack
      Exchange, 2010]. How often such divergence occurs in practice is not
      established by these sources [confidence: mechanism well-attested,
      prevalence unmeasured].

      > **Background.** The mainstream position across motivational
      > psychology, therapeutic practice, and requirements engineering is
      > that stated goals are surface expressions shaped by what is
      > cognitively present, socially safe, and easy to articulate, and
      > that the underlying desire must be recovered by structured probing.
      > Within that consensus the schools differ on whether the deep goal
      > pre-exists and merely needs uncovering, or is partly constructed by
      > the act of elicitation itself.

      People shift toward stating means instead of ends when a task becomes
      difficult [Vallacher & Wegner, 1987], and question phrasing with
      embedded assumptions distorts answers [Schoeb, 2014]. The transfer of
      these findings to everyday help-seeking is inferential rather than
      measured.
  - heading: "Motivational interviewing: well-tested, partly disconfirming"
    body: |
      Motivational interviewing is the best-evidenced dialogic method in
      this corpus, and the evidence is partly disconfirming of its own
      theory: meta-analytic effects are real but modest, and the
      hypothesized mediator — client change talk — carries
      [disputed: mediation estimates range from strong to null across the
      Magill et al. (2014, 2018) meta-analyses]. What survives review is
      the practical toolkit:
      open questions, reflective listening, and rolling with resistance
      outperform confrontation in engagement outcomes [Miller & Rollnick,
      2013] [confidence: strong within addiction and health-behavior
      trials; transfer beyond them thinner].
  - heading: "What the questioning literature actually supports"
    body: |
      The process evidence favors subtraction over addition: across two
      process meta-analyses, directive and confrontational elicitor
      behavior predicts counter-change speech and worse outcomes, while
      added technique produces change talk that fails to predict outcomes
      independently [Magill et al., 2014; Magill et al., 2018]
      [confidence: correlational pattern, not randomized manipulation of
      elicitor behavior]. Automated goal dialogues work, but the effect
      does not live where the branding says: a scripted GROW chatbot
      matched human coaches on goal attainment [Terblanche et al., 2022],
      and in the corpus's one factorial dismantling trial only repeated
      administration moved the primary outcome — the
      motivational-interviewing component did not [Fitzsimmons-Craft et
      al., 2024] FLAG [single factorial trial, one clinical domain].
      Large language models under-ask, answering prematurely rather than
      clarifying [Luo et al., 2025], and timing questions off the model's
      own uncertainty beats imitating when humans ask [Testoni &
      Fernández, 2024] [confidence: early, fast-moving literature].
unresolved: |
  Two bibliography entries did not resolve at verification time and are
  retained with their retrieval snapshots: the Schoeb (2014) publisher page
  timed out (snapshot on file), and one Meta Stack Exchange thread moved
  behind a redirect chain the probe refuses to follow.
bibliography: |
  - Fitzsimmons-Craft, E. E., et al. (2024). Optimizing a chatbot for
    eating-disorders services uptake: a factorial trial. *International
    Journal of Eating Disorders*.
  - Kruglanski, A. W. (2023). *New Developments in Goal Systems Theory.*
    Oxford University Press.
  - Luo et al. (2025). ClarifyMT-Bench: benchmarking multi-turn
    clarification for conversational LLMs. arXiv:2512.21120.
  - Magill, M., et al. (2014). The technical hypothesis of motivational
    interviewing: a meta-analysis. *Journal of Consulting and Clinical
    Psychology*, 82(6). Also Magill, M., et al. (2018), follow-up
    process meta-analysis.
  - Meta Stack Exchange (2010). "What is the XY problem?"
  - Miller, W. R., & Rollnick, S. (2013). *Motivational Interviewing:
    Helping People Change* (3rd ed.). Guilford Press.
  - Schoeb, V. (2014). Question design in physiotherapy. *Studies in
    Communication Sciences*, 14(1).
  - Sheldon, K. M., & Elliot, A. J. (1999). Goal striving, need
    satisfaction, and longitudinal well-being. *JPSP*, 76(3).
  - Terblanche, N., Molyn, J., de Haan, E., & Nilsson, V. (2022).
    Comparing artificial intelligence and human coaching goal
    attainment. *PLOS ONE*, 17(6).
  - Testoni, A., & Fernández, R. (2024). Asking the right question at
    the right time: human and model uncertainty in clarification.
    *EACL 2024*.
  - Vallacher, R. R., & Wegner, D. M. (1987). What do people think they're
    doing? Action identification. *Psychological Review*, 94(1).
colophon: "Produced by the deeper-research pipeline: Exa retrieval slices with an OpenAlex academic anchor, an evidence gate, and an independent refute-mode adversary."
---
**Purpose.** This reference document informs the design of a dialogic AI
skill that helps a user understand and reframe a goal before acting on it.
It maps why stated goals mislead, the major elicitation traditions and
their evidence, and what the empirical record supports about questioning.
Every empirical claim below carries source attribution to the retrieved
corpus; passages set off as background asides are orienting context, not
corpus findings.

*Sample content adapted (heavily trimmed) from the goal-elicitation example
in the deeper-research repository.*
