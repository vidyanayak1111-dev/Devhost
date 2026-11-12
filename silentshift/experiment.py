"""
experiment.py - runs many controlled-change trials automatically, then scores them.

    python experiment.py 30        -> 30 trials (about 1 in 3 are "no change")

Each trial:  reset -> apply a random controlled change -> agent checks -> reset
The agent does not know which change was applied.
"""

import os
import random
import sys

import agent
import change_injector as inj
import score

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
random.seed(7)

# start clean
for f in (inj.LOG_FILE, agent.REPORTS_FILE):
    if os.path.exists(f):
        os.remove(f)
inj.reset()
agent.make_baseline()

changes = [c for c in inj.CHANGES if c != "none"]
for t in range(1, N + 1):
    name = "none" if random.random() < 0.33 else random.choice(changes)
    print(f"\n########## Trial {t}/{N} ##########")
    inj.apply(name, trial=t)
    agent.check(trial=t)
    inj.reset()

score.main()
