"""Good News Briefing — a self-hosted RSS → local-LLM → digest pipeline.

See README.md for the overview. The pieces:
  config.py    -- knobs, env loading, the FEEDS list
  prompts.py   -- CRITERIA, DIGEST_PROMPT, the JSON schema
  models.py    -- Article + Verdict typed data structures
  llm.py       -- the LM Studio / OpenAI-client wrapper
  sources.py   -- fetch(): RSS in -> list[Article] out
  store.py     -- the SQLite seen-table
  deliver.py   -- writing/opening the file and emailing it
  pipeline.py  -- orchestration: wires the stages together
  cli.py       -- argparse + entry point
"""
