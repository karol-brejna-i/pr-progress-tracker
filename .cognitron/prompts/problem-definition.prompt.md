
# Intent

I would like to have a service that is able to monitor PRs progress.
I want to be able to analyze, how fast the PRs get reviewed, accepted, merged.

## What to capture

I need to be able to track progress of certain PR.

I would like to see milestones of PR lifecycle:
- PR creation
- going from DRAFT to ready (OPEN)
- when PR was approved (internally == approval by a GitHub user listed in `config/internal-reviewers.txt`, externally == approval by a user listed in `config/external-reviewers.txt`; approvals by users in neither list are counted as "other" and do not satisfy either milestone)
- change request during review
- etc.


I would like to be able to measure the following times (elapsed time):
- how much it took for the PR to get approved (internally and externally)
- how much time it took a PR for going from draft to ready state
- other values in the future.

## Which PRs

For now, the input to the service will be a list of PRs to be monitored.
The mechanics ay be as simple as having a text file with PR list.
Each line of the PR list is a full PR URL (e.g. https://github.com/org/repo/pull/123). PRs may be in other repositories, so the action must use a token with read access to those repos.

## Where to run 
I would like for the service to be ran periodically as GitHub action.
The configuration and input data should be a part of the repo that hosts the action.
The mechanics should be simple, maybe using GitHub CLI (`gh`) commands, maybe GitHub API.

## Data storage
The metadata of the PRs that are obtained from GitHub and computed by the service should be persisted, It can be simple data files (json, csv) or some simple database as SQLite -- or any other that will serve it's purpose.
Persisted data must be committed back to the hosting repository (e.g. under `data/`) by the action itself; keep one record per PR and append event history rather than overwriting.

## Data presentation
The service should be abe to show the statistics of monitored PRs to the user.
In early stage of the project, it could be simply by creating a fresh markdown page with the results.
In the later iterations, it could be a web page hosted on GitHub pages. The page could read the results dynamically (from data source),j provide ability to filter 
the results, show more details, add or remove PRs from monitoring. But it is next phase, not this one.

# Goal

Your goal is to write a design proposal for creating a service as described in the Intent section. Propose mechanics, architecture, and building blocks; do not scaffold the workflow, scripts, or data files in this task.
The plan should consider target solution, but right now focus on gathering the data and simple way of presenting the information.


## Additional requirements for the AI Agent

Use subagents only if a large, independent investigation is needed to prepare the design proposal.

Deliver what was asked, at the scope intended. Make routine judgment calls yourself, and check in only when different readings of the request would lead to materially different work. If the request seems mistaken or a better approach exists, say so in a sentence and continue with the task as asked rather than quietly narrowing, widening, or transforming it. Finish the whole task, and stop short of actions that are clearly beyond what was asked.


Keep responses focused, brief, and concise. Keep disclaimers and caveats short, and spend most of the response on the main answer. When asked to explain something, give a high-level summary unless an in-depth explanation is specifically requested.

Only correct an earlier statement when the error would change the user's code, conclusions, or decisions. State corrections plainly and briefly, then continue the task. For slips that change nothing for the user, make the fix and move on without noting it.

Before your first tool call, say in one sentence what you're about to do. While working, give a brief update only when you find something important or change direction. When you finish, lead with the outcome: your first sentence should answer "what happened" or "what did you find" with supporting detail after it for readers who want it. 

Delegate to a subagent only for large tasks that are genuinely independent and parallelizable, such as a wide multi-file investigation. Do not delegate work you can finish yourself in a handful of tool calls, and do not use subagents to verify or double-check your own work. If one subagent can complete the task, use one rather than several, and keep spawn counts low.


Match the length of written documents to what the task needs: cover the substance, but do not pad with filler sections, redundant summaries, or boilerplate.

When you finish your analysis, write down the results in a dedicated markdown document. I want it to be the context for the future chat with AI agents. Let it be as self-contained as possible.