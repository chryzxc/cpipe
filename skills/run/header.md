# The header of every role brief

`cpipe.py brief` puts it in front of `brief-<role>.md` (`roles.md`, Handing a step to a role): Direct or Card,
then Rules and Lean for a new session, Lean alone for a resumed one. `<…>` parts are filled in by it.

## Direct
Direct request from a delivery run. No task board, no cards: do this work and reply with the result.

## Card
Card from a delivery run. This brief is your procedure and outranks your usual card rules.
When done, complete this card with your full result as the summary:
hermes kanban complete <your card id> --summary "<result>"
Whatever the outcome, complete the card: do not block it, request changes or create other cards.

## Rules
Work only in <worktree>. Do not commit, push, merge, open pull requests or message anyone.
Do not skip or weaken tests. Never print secrets or .env contents.
A failure that also happens without this change is the baseline: note it and keep going. Never stop for a
decision: choose within the request's scope and say what you chose.
The user's instructions for this work, word for word; they settle the questions they answer:
<instructions>
Report findings with their evidence and the fix you suggest, never a verdict such as hold, blocked or not
ready for release: the delivery run decides.
Use your own skills for this kind of work where you have them.
Keep it lean: the project's own dev server, test runner and tools first; evidence is the command and its
result; no hash manifests, integrity receipts or copies of the tree. At your time budget, stop and reply
with what you have and what is left.
Load only the cpipe files named below, each as skill_view(name='cpipe:run', file_path='<file>') (without
skill_view, read <skill dir>/<file> with your file tool), once per session (again only when its text has left
your context); never load cpipe:run's SKILL.md itself: that is the delivery run's own procedure, not yours.
Never list or poll processes. Write only the files your work needs and your reply file, never the run log,
now.md, receipts or helper scripts around the project's own commands.
Reply in at most 20 lines: the result, the evidence, what is left. Only what does not fit goes in
<run dir>/<role>-reply.md, and you give its path: findings, failures and corrections, each in at most 5 lines
(a finding: file and line, what is wrong, why it matters, the fix, the check that shows it). Never what passed,
what you did not do or the brief restated.

## Lean
Every reply resends all you have read, so read little: the lines you need (search first, a whole file only
when you need all of it), never a file you already have, never a file again to check an edit the tool
reported applied. Work in few replies: independent reads, searches, edits and commands go together in one
reply. A change and its check are one reply, not two: all its edits, then the command that checks it. Calls
in one reply run in the order given, so the check sees the edits: never wait for an edit's result before
sending its check. Test and build output goes to a file in <run dir>/logs/, and the same command prints the
exit code and only the summary and failure lines, so no reply goes to reading the file.
Run no test suite and no build your step does not ask for: the brief gives the checks' results on this tree.
A test you run to prove one point is the narrowest command that shows it, never the same one twice.
Read a diff as `git diff --stat` first, then each file's diff once; never the whole diff again.
Skip the project's general guides (architecture, standards, design, contributing, repo maps, notes) unless
your step says to read them.
