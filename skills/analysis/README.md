# analysis

Skills for understanding a problem statement before anything is built, and
helping the prompter understand it too: what behavior or functionality it
actually asks for, and how it decomposes into units of work.

Effort sets depth:

- **Low effort**: shallow. Take the problem roughly as stated and focus on
  efficient chunking, units of work that are clearly sequenced or
  parallelizable.
- **High effort**: deep. Dig into the underlying problem and how it relates
  to the implementation the model can actually see (code, data, docs), then
  propose units of work grounded in it.

| Skill | Purpose |
| --- | --- |
| [decompose](decompose/SKILL.md) | Understand a problem statement and break it into units of work |

Add more as `analysis/<name>/SKILL.md`; the install loop in the root README
picks them up automatically.
