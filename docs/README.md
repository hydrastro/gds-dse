# Documentation

The project studies flow-parameter DSE across three PDKs, with repeated seeds
to assess variability and ranking stability. The baseline walkthrough is a
preliminary check. These pages cover setup, configuration and analysis.

| Guide | What it answers |
| --- | --- |
| [Setup](setup.md) | How do I add the submodules, install the environment and validate RTL? |
| [Study plan](study-plan.md) | What question does each experiment answer, and how many runs does it cost? |
| [Running the study](running-study.md) | How do I prepare, run, limit CPU use, resume and collect results? |
| [Configuration](configuration.md) | Which file should I edit, and which value takes precedence? |
| [Analysis](analysis-guide.md) | How should I interpret spread, failures, rankings and transfer? |
| [PDK assumptions](pdk-values.md) | Which libraries, corners and electrical constraints are compared? |
| [Versioning](versioning.md) | What is pinned, why does it matter, and what is recorded automatically? |
| [Adding a design](experiment-guide.md) | How does another unmodified RTL repository join the suite? |
| [Validation](project-review.md) | What do software checks establish, and what still needs a real flow? |

Experiment files are templates, not a list of jobs that must all be run.
See [the experiment index](../experiments/README.md) before selecting a larger
campaign.
