"""Data Catalog Search Assistant (assignment DAI-041).

Pipeline: synthetic warehouse -> metadata harvest + steward CSVs -> raw layer ->
SQL staging/validation -> curated catalog -> role-filtered FTS index -> assistant
(optional LLM, validated against retrieved evidence).
"""

__version__ = "1.0.0"
