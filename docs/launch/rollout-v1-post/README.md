# Source of the Rollout post for v1

The published post is
https://jazzyalex.github.io/agent-sessions/blog/session-bench-v1/ (file
`docs/_posts/2026-10-08-session-bench-v1.md` in the Agent Sessions repository).

- `front.yml`, `body.md`, `figures.html`: title and summary, text, and the
  styles and figure templates.
- `gen_data.py`: makes the chart data of the front matter from
  `data/leaderboard-v1.yml` and `metric-rows.json`.
- `metric-rows.json`: the 31 metric rows (correct, observed, decoded counts) of
  each of the 36 replays. Running the commands of
  `artifacts/survival-v1-release/REPRODUCE.md` prints the same rows.
- `build.py`: joins the parts into the post file. The paths inside it point at
  a scratch folder; change them before use.
- `social-card.html`: the 1200 by 630 card, rendered to
  `docs/assets/session-bench-v1-social-card.png` in the Agent Sessions
  repository.

`09-the-rollout-v1-release.md` beside this folder is the first draft. It was
replaced by this post and is kept as history; one statement in it is wrong
(Claude Desktop does not batch tool results; in the three runs its model made
the edit inside a shell command, so the edit has no result of its own).
