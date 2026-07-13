# Quality Evaluation

Use sanitized fixtures only. Never commit cookies, API keys, raw private comments, or generated account exports.

Run the same input three times with identical parameters and separate output directories. Check each run with:

```bash
python -m douyin_topic_packager evaluate \
  --pain-signals <output>/pain_signals.json \
  --topic-packages <output>/topic_packages.json \
  --require-generator llm
```

Release acceptance criteria:

- every package pain point exists in the extracted signal set;
- every evidence string maps to a stored source reference;
- every package comes from the required generator during provider regression tests;
- no package asks for fabricated proof or individual diagnosis;
- weak-only runs contain exploratory packages only;
- a stopped comment run resumes only failed or missing videos;
- all three repeated model runs pass the offline quality gate.
